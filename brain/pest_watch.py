"""Pest watch — phenology-driven pest-emergence alerts (spec: PEST_WATCH.md).

Deterministic sibling of garden_watch, and the first cross-repo ligament: the emergence
table (data/pest-companions.json) is vendored from the Homesteader Labs site —
    cp <site>/content/crops/pest-companions.json data/pest-companions.json
(site = homesteader-labs-next-CLEAN; the site remains the source of truth).

The spine is growing-degree-days accumulated from a biofix (the season's last spring
frost, OBSERVED from the Open-Meteo archive — not a normals table), plus an estimated
soil temperature (trailing-mean air temp; we have moisture probes, not soil-temp probes).
A crop's pest is "in window" when both thresholds are met; each pest alerts ONCE per
season. On the very first run mid-season, windows that are already open are marked
silently — those pests emerged weeks ago and a flood of stale alerts teaches the user
to ignore the real ones.

No LLM anywhere in this file. garden_watch folds these alerts into the 7am push.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import config

config.load_secrets()
from tools import weather  # noqa: E402

GDD_BASE = float(os.environ.get("PEST_GDD_BASE", "50"))
SOILTEMP_WINDOW = int(os.environ.get("PEST_SOILTEMP_WINDOW", "10"))
CROPS = [c for c in os.environ.get("PEST_CROPS", "").split(",") if c.strip()]
BIOFIX_OVERRIDE = os.environ.get("PEST_BIOFIX", "")
STATE_PATH = config.PEST_STATE
DATA_PATH = config.PEST_DATA
_SPRING_END = "-06-30"  # last-frost scan window ends here; later lows are autumn, not biofix


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _gdd(row: dict) -> float:
    return max(0.0, (row["hi"] + row["lo"]) / 2 - GDD_BASE)


def find_biofix(year: int, rows: list[dict] | None = None) -> str:
    """The season's biofix: the last OBSERVED spring frost (lo <= FROST_F before July),
    from the archive. Falls back to Jan 1 if the spring had no frost at all."""
    if BIOFIX_OVERRIDE:
        return BIOFIX_OVERRIDE
    rows = rows if rows is not None else weather.history_days(f"{year}-01-01", f"{year}{_SPRING_END}")
    frosts = [r["date"] for r in rows if r["lo"] <= weather.FROST_F]
    return frosts[-1] if frosts else f"{year}-01-01"


def advance(state: dict, today: dt.date) -> dict:
    """Bring cumulative GDD + soil-temp estimate up to date. Idempotent per calendar day.
    First run of a season resets the accumulator from a fresh biofix."""
    season = str(today.year)
    if state.get("season") != season:
        state = {"season": season, "biofix": find_biofix(today.year),
                 "cumulative_gdd": 0.0, "last_gdd_date": None, "recent_mean_temps": [],
                 "alerted": {}, "first_run_pending": True}

    # Rows since the day after the last accumulated date (or the biofix), through yesterday.
    # history_days drops not-yet-archived rows itself, so "through yesterday" self-clamps.
    start = (dt.date.fromisoformat(state["last_gdd_date"]) + dt.timedelta(days=1)).isoformat() \
        if state["last_gdd_date"] else state["biofix"]
    end = (today - dt.timedelta(days=1)).isoformat()
    if start <= end:
        rows = weather.history_days(start, end)
        for r in rows:
            state["cumulative_gdd"] += _gdd(r)
            state["recent_mean_temps"] = (state["recent_mean_temps"]
                                          + [(r["hi"] + r["lo"]) / 2])[-SOILTEMP_WINDOW:]
        if rows:
            state["last_gdd_date"] = rows[-1]["date"]
    return state


def est_soil_temp(state: dict) -> float | None:
    temps = state.get("recent_mean_temps") or []
    return round(sum(temps) / len(temps), 1) if temps else None


def _crops() -> list[dict]:
    table = json.loads(DATA_PATH.read_text())
    return [c for c in table if not CROPS or c["cropId"] in CROPS]


def _in_window(pest: dict, gdd: float, soil: float | None) -> bool:
    if soil is None or soil < pest["soilTempThreshold"]:
        return False
    return gdd >= pest["gddThreshold"] if "gddThreshold" in pest else True


def _alert_line(crop: dict, pests: list[dict], gdd: float, soil: float) -> str:
    parts = []
    for p in pests:
        top = sorted(p.get("companions", []),
                     key=lambda c: {"strong": 0, "moderate": 1}.get(c.get("evidenceLevel"), 2))[:2]
        comp = "; ".join(f"{c['companion']} ({c['placement']}: {c['reason'].split(';')[0]})"
                         for c in top)
        basis = f"{int(gdd)} GDD" if "gddThreshold" in p else "soil-temp only (lower confidence)"
        parts.append(f"{p['name']} [{basis}]" + (f" — try {comp}" if comp else ""))
    return (f"Pest window opening for {crop['cropId']} (est. soil {soil:.0f}°F): "
            + " | ".join(parts))


def build_alerts(persist: bool = False, today: dt.date | None = None) -> list[str]:
    """Newly-in-window pest alerts, once per pest per season. `persist=False` (dry runs,
    the briefing) never advances state or consumes an alert."""
    today = today or dt.date.today()
    state = advance(dict(_load_state()), today)
    gdd, soil = state["cumulative_gdd"], est_soil_temp(state)
    first_run = state.pop("first_run_pending", False)

    alerts: list[str] = []
    for crop in _crops():
        new = [p for p in crop["pests"]
               if _in_window(p, gdd, soil)
               and state["alerted"].get(f"{crop['cropId']}:{p['name']}") != state["season"]]
        for p in new:
            state["alerted"][f"{crop['cropId']}:{p['name']}"] = state["season"]
        if new and not first_run:
            alerts.append(_alert_line(crop, new, gdd, soil))
        elif new:
            print(f"pest-watch: first run — {crop['cropId']}: "
                  f"{', '.join(p['name'] for p in new)} already in window; marked, not alerted",
                  file=sys.stderr)

    if persist:
        _save_state(state)
        for line in alerts:  # season history for the almanac; only real (persisted) firings
            try:
                import records_store
                records_store.log_event("note", subject="Garden", action="pest_alert", detail=line)
            except Exception as e:  # noqa: BLE001 — history is best-effort, the push is not
                print(f"pest-watch: records log failed: {e}", file=sys.stderr)
    return alerts


def main() -> int:
    dry = "--dry-run" in sys.argv
    alerts = build_alerts(persist=not dry)
    state = _load_state() if not dry else advance(dict(_load_state()), dt.date.today())
    print(f"pest-watch: season {state.get('season')} biofix {state.get('biofix')} "
          f"gdd {state.get('cumulative_gdd', 0):.0f} soil~{est_soil_temp(state)}°F "
          f"alerted {len(state.get('alerted', {}))}")
    for a in alerts:
        print("ALERT:", a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
