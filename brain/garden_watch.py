"""Proactive garden watch — the morning check that pushes to the phone.

Runs daily via a systemd user timer. Pulls soil moisture from Home Assistant and
the forecast from the weather tool, then pushes a phone notification ONLY when
there's something to act on:
  - Frost/freeze coming   — forecast low <= FROST_F within the horizon
  - A bed is dry          — soil <= DRY_PCT AND no meaningful rain coming (skip if rain due)
  - Heavy rain coming     — a day >= HEAVY_RAIN_IN, a heads-up to skip watering

No notification means nothing needs doing (user chose "only if actionable").
Currently-raining is intentionally NOT an alert. NWS official warnings live in the
weather tool but aren't pushed here. Thresholds are env-overridable.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import httpx

import config  # puts brain/ on sys.path + owns paths

config.load_secrets()
from tools import weather  # noqa: E402  (after secrets load)

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
NOTIFY = os.environ.get("GARDEN_NOTIFY", "mobile_app_alexs_iphone")
DRY_PCT = float(os.environ.get("GARDEN_DRY_PCT", "40"))
HEAVY_RAIN_IN = float(os.environ.get("GARDEN_HEAVY_RAIN_IN", "0.5"))
SAT_PCT = float(os.environ.get("GARDEN_SAT_PCT", "95"))  # waterlogged threshold
SAT_DAYS = int(os.environ.get("GARDEN_SAT_DAYS", "2"))   # consecutive mornings to alert
STATE_PATH = str(config.GARDEN_STATE)
RAIN_WINDOW_DAYS = 3  # "no rain coming" lookahead for the dry-bed test
HORIZON = 7
_HDRS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def soil_beds() -> list[tuple[str, float]]:
    """(bed name, moisture %) for each live soil sensor; skips unavailable ones."""
    r = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=15)
    r.raise_for_status()
    beds = []
    for s in r.json():
        if "soilmoisture" not in s["entity_id"]:
            continue
        try:
            pct = float(s["state"])
        except (TypeError, ValueError):
            continue  # unavailable / unknown
        name = s["attributes"].get("friendly_name", s["entity_id"]).replace(" Soil Moisture", "")
        beds.append((name, pct))
    return sorted(beds)


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def saturated_alert(beds: list[tuple[str, float]], persist: bool) -> str | None:
    """Beds pegged >= SAT_PCT for SAT_DAYS consecutive *mornings*.

    Streaks live in STATE_PATH, advanced once per calendar day, so repeated runs in a
    day don't double-count and a normal post-watering/rain spike (which drains overnight)
    doesn't trip it — only genuinely waterlogged beds do.
    """
    today = dt.date.today().isoformat()
    state = _load_state()
    soggy = []
    for name, pct in beds:
        prev = state.get(name, {"streak": 0, "date": None})
        if prev.get("date") == today:
            streak = prev.get("streak", 0)  # already advanced today; don't re-count
        else:
            streak = prev.get("streak", 0) + 1 if pct >= SAT_PCT else 0
            state[name] = {"streak": streak, "date": today}
        if pct >= SAT_PCT and streak >= SAT_DAYS:
            soggy.append((name, pct, streak))
    if persist:
        _save_state(state)
    if not soggy:
        return None
    names = ", ".join(f"{n} ({p:.0f}%, {d}d)" for n, p, d in soggy)
    return (f"Possibly waterlogged (≥{SAT_PCT:.0f}% for {SAT_DAYS}+ mornings): "
            f"{names} — check drainage / ease off watering.")


def build_alerts(persist: bool = False) -> list[str]:
    """The actionable lines for today, or [] if nothing needs doing.

    `persist` advances the saturation streak state; pass False for dry-runs so testing
    doesn't mutate the streaks.
    """
    rows = weather.forecast_days(HORIZON)
    near_rain = sum(r["rain"] for r in rows[:RAIN_WINDOW_DAYS])
    alerts: list[str] = []

    ev = weather.first_freeze(rows)
    if ev:
        label = "Hard freeze" if ev["kind"] == "freeze" else "Frost"
        alerts.append(f"{label} coming {weather._nice_date(ev['date'])}: "
                      f"low {ev['lo']:.0f}°F — protect tender crops.")

    try:
        beds = soil_beds()
    except Exception as e:  # noqa: BLE001 — forecast alerts still worth sending
        beds = []
        print(f"garden-watch: soil read failed: {e}", file=sys.stderr)
    dry = [(n, p) for n, p in beds if p <= DRY_PCT]
    if dry and near_rain < weather.RAIN_MIN_IN:
        names = ", ".join(f"{n} ({p:.0f}%)" for n, p in dry)
        alerts.append(f"Water these beds (dry, no rain in {RAIN_WINDOW_DAYS} days): {names}.")

    soggy = saturated_alert(beds, persist)
    if soggy:
        alerts.append(soggy)

    heavy = [r for r in rows if r["rain"] >= HEAVY_RAIN_IN]
    if heavy:
        h = heavy[0]
        alerts.append(f"Heavy rain {weather._nice_date(h['date'])}: {h['rain']:.2f} in "
                      f"expected — you can skip watering.")
    return alerts


def push(title: str, message: str) -> None:
    httpx.post(f"{HA_URL}/api/services/notify/{NOTIFY}", headers=_HDRS,
               json={"title": title, "message": message}, timeout=15).raise_for_status()


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    alerts = build_alerts(persist=not dry_run)
    if not alerts:
        print("garden-watch: nothing actionable")
        return 0
    msg = "\n".join(f"• {a}" for a in alerts)
    if dry_run:
        print("garden-watch (dry-run) would push:\n" + msg)
        return 0
    push("🌱 Garden", msg)
    print("garden-watch: pushed:\n" + msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
