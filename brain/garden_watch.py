"""Proactive garden watch — the morning check that pushes to the phone.

Runs daily via a systemd user timer. Pulls soil moisture from Home Assistant and
the forecast from the weather tool, then pushes a phone notification ONLY when
there's something to act on:
  - A sensor is lying     — unavailable, reading 0%, frozen for SOIL_FLAT_HOURS, or a
                            battery at or under SOIL_BATT_LOW that HA has heard from within
                            BATT_FRESH_H. Checked FIRST, because every line below it is only
                            as good as the readings it came from. The gateway pushes battery
                            far less often than moisture, so an older voltage is not acted on
                            (it may already have been swapped) and a battery set that stops
                            refreshing entirely is reported as that, once
  - Frost/freeze coming   — forecast low <= FROST_F within the horizon, or within
                            FROST_MARGIN_F of it in the next two nights. The yard runs colder
                            than the forecast grid on a clear night: on 2026-10-07 the forecast
                            said 41°F the morning before and the nearest station read 35.6°F,
                            so a bare FROST_F line never fired
  - Drain the rain barrels — the season's first freeze in the forecast, said twice (when it
                            first appears, and the morning before) and then never again that
                            winter. A full 55-gallon barrel that freezes can split; an
                            overflowing one in summer costs nothing, so only the freeze is nudged
  - A bed is dry          — soil <= DRY_PCT AND no meaningful rain coming (skip if rain due)
  - Heavy rain coming     — a day >= HEAVY_RAIN_IN, a heads-up to skip watering

The staleness check exists because two failures this season were invisible by design. A dead
sensor vanished from the count, so the briefing said "all 5 beds fine" for a month instead of
naming the sixth. And a frozen gateway does not even change the count: Home Assistant carries
the last value forward, so six beds reported one identical number a day for six days, one of
them a literal 0.0%, and it read as steady soil. A frozen reading is worse than a missing one
because it is confidently wrong, and these numbers are meant to drive a valve one day. All
sensors flat at once is reported as one gateway fault, not six probe faults.

No notification means nothing needs doing (user chose "only if actionable").
Currently-raining is intentionally NOT an alert. NWS official warnings live in the
weather tool but aren't pushed here. Thresholds are env-overridable.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
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
SOIL_FLAT_HOURS = int(os.environ.get("GARDEN_SOIL_FLAT_HOURS", "24"))  # unchanged this long = suspect
SOIL_BATT_LOW = float(os.environ.get("GARDEN_SOIL_BATT_LOW", "1.3"))   # volts, WH51
BATT_FRESH_H = float(os.environ.get("GARDEN_SOIL_BATT_FRESH_HOURS", "12"))  # older = not acted on
BATT_STALL_H = float(os.environ.get("GARDEN_SOIL_BATT_STALL_HOURS", "48"))  # older = say so once
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


def _soil_states() -> list[dict]:
    r = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=15)
    r.raise_for_status()
    return r.json()


def _reported_age_h(state: dict, now: dt.datetime | None = None) -> float | None:
    """Hours since HA last heard this entity reported, or None when the state carries no
    timestamp (test fixtures, and anything that predates `last_reported`)."""
    stamp = state.get("last_reported") or state.get("last_updated")
    if not stamp:
        return None
    try:
        heard = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    now = now or dt.datetime.now(heard.tzinfo)
    return (now - heard).total_seconds() / 3600


def _channel(entity_id: str) -> str:
    """The WH51 channel number, which is the only thing tying a battery entity to a bed."""
    m = re.search(r"(\d+)$", entity_id)
    return m.group(1) if m else ""


def _bed_name(entity: dict) -> str:
    return (entity["attributes"].get("friendly_name", entity["entity_id"])
            .replace(" Soil Moisture", ""))


def _changed_recently(entity_ids: list[str], hours: int) -> dict[str, int]:
    """Distinct states each entity reported in the last `hours`. One history call, not six."""
    start = (dt.datetime.now() - dt.timedelta(hours=hours)).replace(microsecond=0)
    r = httpx.get(f"{HA_URL}/api/history/period/{start.isoformat()}", headers=_HDRS,
                  params={"filter_entity_id": ",".join(entity_ids),
                          "minimal_response": "true"}, timeout=30)
    r.raise_for_status()
    out = {}
    for series in r.json():
        if not series:
            continue
        eid = series[0].get("entity_id")
        if eid:
            out[eid] = len({point.get("state") for point in series})
    return out


def stale_sensors() -> list[str]:
    """Soil sensors that are lying rather than reading.

    `soil_beds` skips anything it cannot parse, which is why Hot Peppers could sit dead from
    2026-08-15 to 2026-09-17 while the briefing kept saying every bed was fine: the count
    quietly went from six to five and nothing said so. Worse, a frozen gateway does not even
    change the count. It carries the last value forward, so the beds read plausible and
    steady, which is exactly what healthy soil looks like. In June 2026 that produced six
    days of identical readings including a literal 0.0%, and it was only caught months later
    by eye.

    This is deterministic on purpose. Staleness is a comparison of rows over a window, not a
    judgement, so no model is involved and none should be.
    """
    states = _soil_states()
    moisture = [s for s in states if "soilmoisture" in s["entity_id"]]
    if not moisture:
        return []
    battery = {s["entity_id"]: s for s in states if "soilbatt" in s["entity_id"]}

    dead, zero, live = [], [], []
    for s in moisture:
        try:
            pct = float(s["state"])
        except (TypeError, ValueError):
            dead.append(s)
            continue
        (zero if pct == 0 else live).append(s)

    flat: list[dict] = []
    if live:
        try:
            distinct = _changed_recently([s["entity_id"] for s in live], SOIL_FLAT_HOURS)
        except Exception as e:  # noqa: BLE001 — a history outage must not fake an alert
            print(f"garden-watch: soil history read failed: {e}", file=sys.stderr)
            distinct = {}
        flat = [s for s in live if distinct.get(s["entity_id"], 2) <= 1]

    out = []
    # All of them at once is one fault upstream, not six coincidences. Say so, because six
    # separate sensor alerts would send someone to check six probes that are all fine.
    if flat and len(flat) == len(live) and not dead and not zero:
        out.append(f"ALL {len(flat)} soil sensors unchanged for {SOIL_FLAT_HOURS}h — that is "
                   f"the Ecowitt gateway, not the beds. Readings are carried-forward, so "
                   f"nothing should act on them until it is back.")
    else:
        for s in dead:
            out.append(f"Soil sensor {_bed_name(s)} is not reporting ({s['state']}).")
        for s in zero:
            out.append(f"Soil sensor {_bed_name(s)} reads 0% — that is a fault, not dry soil.")
        for s in flat:
            out.append(f"Soil sensor {_bed_name(s)} unchanged for {SOIL_FLAT_HOURS}h "
                       f"at {float(s['state']):.0f}% — suspect frozen, not steady.")

    # Batteries last: a warning ahead of the failure, rather than another way to find out
    # after. The battery entities are named `soilbattN` with no bed in them, so the channel
    # number is what ties a voltage to a bed.
    beds_by_channel = {_channel(s["entity_id"]): _bed_name(s) for s in moisture}
    stalled: list[tuple[str, float, float]] = []
    for eid, s in sorted(battery.items()):
        try:
            volts = float(s["state"])
        except (TypeError, ValueError):
            continue
        if volts > SOIL_BATT_LOW:
            continue
        where = beds_by_channel.get(_channel(eid)) or eid
        # The gateway pushes battery far less often than moisture, so a low voltage can be a
        # day old and already fixed. Acting on it sends someone to swap a cell they swapped
        # yesterday, which is the carried-forward failure again, one entity over.
        age = _reported_age_h(s)
        if age is not None and age > BATT_FRESH_H:
            stalled.append((where, volts, age))
            continue
        out.append(f"Soil sensor battery low: {where} at {volts:.1f}V.")

    # Said once, not per sensor: if the batteries have not refreshed in days while moisture
    # keeps arriving, the silence is the thing to report, not the voltages behind it.
    if stalled and max(a for _, _, a in stalled) > BATT_STALL_H:
        oldest = max(a for _, _, a in stalled)
        names = ", ".join(w for w, _, _ in stalled)
        out.append(f"Battery readings have not refreshed in {oldest:.0f}h while moisture keeps "
                   f"arriving ({names}). Low-battery alerts are paused until they do.")
    return out


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


def _season(day: dt.date) -> int:
    """The cold season a date belongs to, named by the year it starts (Jul 1 to Jun 30)."""
    return day.year if day.month >= 7 else day.year - 1


def barrel_alert(rows: list[dict], today: dt.date, persist: bool) -> str | None:
    """Drain-the-barrels nudge for the season's first freeze, or None.

    Two nudges a season at most: the first morning a freeze shows up in the forecast, which
    leaves days to plan, and the morning before it, in case the first was swiped away. After
    that it stays quiet until next season. A reminder that fired every cold morning until April
    would be about barrels already drained, and would teach the user to ignore it."""
    ev = next((r for r in rows if r["lo"] <= weather.FREEZE_F), None)
    if not ev:
        return None
    state = _load_state()
    season = _season(today)
    mine = state.get("_barrels", {})
    if mine.get("season") != season:
        mine = {"season": season, "sent": []}
    days_out = (dt.date.fromisoformat(ev["date"]) - today).days
    due = [n for n in ("first", "eve") if n not in mine["sent"] and (n == "first" or days_out <= 1)]
    if not due:
        return None
    if days_out <= 1:
        due = ["first", "eve"]  # one message covers both when the warning is already late
    mine["sent"] = sorted(set(mine["sent"]) | set(due))
    if persist:
        state["_barrels"] = mine
        _save_state(state)
    return (f"Freeze {weather._nice_date(ev['date'])} (low {ev['lo']:.0f}°F): drain both rain "
            f"barrels and turn the gutter diverter away, or a frozen barrel can split. Leave "
            f"the pergola barrel's spigot open or tip it over, since it keeps catching runoff.")


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
    if ev and ev["kind"] == "near":
        alerts.append(f"Frost possible {weather._nice_date(ev['date'])}: forecast low "
                      f"{ev['lo']:.0f}°F, and the yard can run {weather.FROST_MARGIN_F:.0f}° "
                      f"colder than that. Protect tender crops.")
    elif ev:
        label = "Hard freeze" if ev["kind"] == "freeze" else "Frost"
        alerts.append(f"{label} coming {weather._nice_date(ev['date'])}: "
                      f"low {ev['lo']:.0f}°F — protect tender crops.")

    barrels = barrel_alert(rows, dt.date.today(), persist)
    if barrels:
        alerts.append(barrels)

    try:
        beds = soil_beds()
    except Exception as e:  # noqa: BLE001 — forecast alerts still worth sending
        beds = []
        print(f"garden-watch: soil read failed: {e}", file=sys.stderr)

    # Before anything is said about what the beds need, say whether the beds can be heard at
    # all. A frozen reading looks exactly like a healthy one, so this goes first: everything
    # below it is only as good as the sensors it came from.
    try:
        alerts.extend(stale_sensors())
    except Exception as e:  # noqa: BLE001 — a stale check must never cost the real alerts
        print(f"garden-watch: stale check failed: {e}", file=sys.stderr)
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

    # Pest-emergence windows (GDD + soil-temp spine; see PEST_WATCH.md). Same persist
    # discipline: only the real 7am run advances the season state / consumes an alert.
    try:
        import pest_watch
        alerts.extend(pest_watch.build_alerts(persist=persist))
    except Exception as e:  # noqa: BLE001 — a pest-data problem must not kill soil/frost alerts
        print(f"garden-watch: pest watch failed: {e}", file=sys.stderr)
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
