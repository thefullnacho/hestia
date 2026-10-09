"""`weather` tool — garden-focused forecast: rain QPF, freeze watch, NWS alerts.

Open-Meteo (free, no key) for the quantitative forecast — daily `precipitation_sum`
is the "how much rain" QPF and `temperature_2m_min` is the freeze signal. NWS
`api.weather.gov` adds official active alerts (frost/freeze warnings, etc.). Defaults
to the homestead lat/lon (from HA config). Read-only — no safety-gate concerns.

An alerts outage is reported as unknown, never as an all-clear. Both upstreams are
keyless public APIs, the sanctioned exception to "nothing phones home" (see CLAUDE.md).

Ported from the user's homesteader-labs `weatherApi.ts` / `FrostGuardAlert.tsx`. The
forecast helpers here are shared with the proactive garden-watch job.
"""
from __future__ import annotations

import datetime as dt
import os

import httpx

LAT = float(os.environ.get("HESTIA_LAT", "41.3311594"))
LON = float(os.environ.get("HESTIA_LON", "-72.154657"))
_UA = "Hestia/0.4 (+local home agent)"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# Thresholds (°F). Frost can damage tender crops a few degrees above a hard freeze.
FROST_F = float(os.environ.get("FROST_F", "36"))
FREEZE_F = float(os.environ.get("FREEZE_F", "32"))
# The forecast is a grid cell, the yard is one spot in it, and on a clear calm night the spot
# runs colder. On 2026-10-07 the forecast the morning before said 41°F and the nearest station
# read 35.6°F, so a frost line compared straight against the forecast never fired. A low within
# this margin of FROST_F is possible frost. 6 is the smallest whole margin that would have
# caught that night; garden_watch logs forecast against observed so it can be re-set from data.
FROST_MARGIN_F = float(os.environ.get("FROST_MARGIN_F", "6"))
# Possible frost is only said for the next two nights. Today's row is left out: its low is the
# dawn the 7am run has just watched happen. A forecast this close to the line five days out
# moves too much to act on.
NEAR_FROST_NIGHTS = 2
RAIN_MIN_IN = 0.1  # ignore trace amounts when summarizing "rain coming"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "weather",
        "description": ("Local weather forecast for the homestead, focused on gardening. "
                        "action='briefing' (default) gives rain outlook + freeze watch + any "
                        "official alerts; action='rain' is the quantitative rain forecast (how "
                        "much, which days); action='frost' is the freeze/frost watch; "
                        "action='alerts' lists active National Weather Service warnings. Use this "
                        "for any 'will it rain / how much / will it freeze / frost' question, and "
                        "combine with soil-moisture readings from the home tool to advise watering."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["briefing", "rain", "frost", "alerts"]},
                "days": {"type": "integer", "description": "forecast horizon in days (1-16, default 7)"},
            },
            "required": ["action"],
        },
    },
}


# ----- data fetch (shared with the garden-watch job) ------------------------

def forecast_days(days: int = 7) -> list[dict]:
    """Daily forecast rows: date, hi, lo (°F), rain (inch QPF), pop (% max)."""
    days = max(1, min(16, days))
    params = {
        "latitude": LAT, "longitude": LON,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
        "timezone": "auto", "forecast_days": days,
    }
    r = httpx.get(OPEN_METEO, params=params, headers={"User-Agent": _UA}, timeout=20)
    r.raise_for_status()
    d = r.json()["daily"]
    return [
        {"date": d["time"][i], "hi": d["temperature_2m_max"][i], "lo": d["temperature_2m_min"][i],
         "rain": d["precipitation_sum"][i] or 0.0, "pop": d["precipitation_probability_max"][i]}
        for i in range(len(d["time"]))
    ]


def history_days(start: str, end: str) -> list[dict]:
    """Observed daily rows (same shape as forecast_days, minus pop) from the Open-Meteo
    archive — used by pest-watch to back-fill GDD from the season's biofix. The archive
    lags ~2 days behind realtime; rows with null temps (too recent) are dropped."""
    params = {
        "latitude": LAT, "longitude": LON,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
        "timezone": "auto", "start_date": start, "end_date": end,
    }
    r = httpx.get("https://archive-api.open-meteo.com/v1/archive", params=params,
                  headers={"User-Agent": _UA}, timeout=30)
    r.raise_for_status()
    d = r.json()["daily"]
    return [
        {"date": d["time"][i], "hi": d["temperature_2m_max"][i], "lo": d["temperature_2m_min"][i],
         "rain": d["precipitation_sum"][i] or 0.0}
        for i in range(len(d["time"]))
        if d["temperature_2m_max"][i] is not None and d["temperature_2m_min"][i] is not None
    ]


def active_alerts() -> list[dict] | None:
    """Active NWS alerts for the homestead's forecast zone.

    Returns [] for "checked, nothing active" and None for "couldn't check". Callers must
    keep those apart: this is the frost/freeze warning path, and reporting an outage as
    an all-clear is the one failure here that can cost a crop.
    """
    h = {"User-Agent": _UA, "Accept": "application/geo+json"}
    try:
        # NWS accepts at most 4 decimal places and 301s anything longer. Unfollowed, that
        # redirect raises out of here, which is how this whole layer sat dead while
        # reporting "no active alerts" (2026-08-17). Round, and follow anyway: the host is
        # fixed, so following is safe here in a way it is not for the model-directed
        # `search` fetch.
        pt = httpx.get(f"https://api.weather.gov/points/{LAT:.4f},{LON:.4f}",
                       headers=h, timeout=15, follow_redirects=True)
        pt.raise_for_status()
        zone = pt.json()["properties"].get("forecastZone", "").rstrip("/").split("/")[-1]
        if not zone:
            return None  # no zone resolved is still "we don't know", not "all clear"
        al = httpx.get(f"https://api.weather.gov/alerts/active?zone={zone}",
                       headers=h, timeout=15, follow_redirects=True)
        al.raise_for_status()
        out = []
        for f in al.json().get("features", []):
            p = f.get("properties", {})
            out.append({"event": p.get("event", "Alert"),
                        "headline": p.get("headline") or p.get("event", ""),
                        "severity": p.get("severity", "")})
        return out
    except Exception:  # noqa: BLE001 — NWS flakes; report the gap, never a false all-clear
        return None


def first_freeze(rows: list[dict]) -> dict | None:
    """First night worth protecting crops for, labelled freeze, frost, or near.

    A low at or under FROST_F counts anywhere in the rows. A low within FROST_MARGIN_F above
    it counts as "near" (possible frost), but only for the NEAR_FROST_NIGHTS rows after today.
    The earliest one wins, because that is the first night the covers have to go on."""
    for i, row in enumerate(rows):
        if row["lo"] <= FROST_F:
            return {**row, "kind": "freeze" if row["lo"] <= FREEZE_F else "frost"}
        if 1 <= i <= NEAR_FROST_NIGHTS and row["lo"] <= FROST_F + FROST_MARGIN_F:
            return {**row, "kind": "near"}
    return None


def station_low(day: dt.date) -> float | None:
    """Lowest temperature (°F) the nearest NWS station read over a local calendar day.

    A real thermometer, not a model, so garden_watch can measure how far the forecast runs
    warm here. The calendar day matches how Open-Meteo bounds its daily low. None when the
    station has no readings for the day or cannot be reached."""
    h = {"User-Agent": _UA, "Accept": "application/geo+json"}
    try:
        station = os.environ.get("GARDEN_OBS_STATION")
        if not station:
            pt = httpx.get(f"https://api.weather.gov/points/{LAT:.4f},{LON:.4f}",
                           headers=h, timeout=15, follow_redirects=True)
            pt.raise_for_status()
            st = httpx.get(pt.json()["properties"]["observationStations"],
                           headers=h, timeout=15, follow_redirects=True)
            st.raise_for_status()
            station = st.json()["features"][0]["properties"]["stationIdentifier"]  # nearest first
        start = dt.datetime.combine(day, dt.time()).astimezone()
        ob = httpx.get(f"https://api.weather.gov/stations/{station}/observations", headers=h,
                       params={"start": start.isoformat(),
                               "end": (start + dt.timedelta(days=1)).isoformat()},
                       timeout=20, follow_redirects=True)
        ob.raise_for_status()
        temps = [f["properties"]["temperature"]["value"] for f in ob.json().get("features", [])]
        temps = [c for c in temps if c is not None]
    except Exception:  # noqa: BLE001 — a missing reading is logged as missing, never guessed
        return None
    return round(min(temps) * 9 / 5 + 32, 1) if temps else None


def _nice_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return d.strftime("%a %b ") + str(d.day)  # e.g. "Tue Jun 10"


# ----- formatting -----------------------------------------------------------

def _rain_text(rows: list[dict]) -> str:
    horizon = len(rows)
    wet = [r for r in rows if r["rain"] >= RAIN_MIN_IN]
    total = sum(r["rain"] for r in rows)
    if not wet:
        return f"No meaningful rain expected in the next {horizon} days (total {total:.2f} in)."
    lines = [f"Rain over the next {horizon} days — total {total:.2f} in:"]
    for r in wet:
        lines.append(f"  {_nice_date(r['date'])}: {r['rain']:.2f} in ({r['pop']}% chance)")
    return "\n".join(lines)


def _frost_text(rows: list[dict]) -> str:
    ev = first_freeze(rows)
    if not ev:
        text = (f"No frost or freeze in the next {len(rows)} days "
                f"(lowest forecast low is {min(r['lo'] for r in rows):.0f}°F).")
        ahead = min(rows[1:], key=lambda r: r["lo"], default=None)
        if ahead and ahead["lo"] <= FROST_F + FROST_MARGIN_F:
            text += (f" {_nice_date(ahead['date'])} at {ahead['lo']:.0f}°F is within "
                     f"{FROST_MARGIN_F:.0f}° of the frost line, close enough to watch.")
        return text
    if ev["kind"] == "near":
        return (f"Possible frost: {_nice_date(ev['date'])} low {ev['lo']:.0f}°F. That is above "
                f"the {FROST_F:.0f}°F frost line, but the yard can run {FROST_MARGIN_F:.0f}° "
                f"colder than the forecast on a clear night.")
    label = "Hard freeze" if ev["kind"] == "freeze" else "Frost"
    return f"{label} watch: {_nice_date(ev['date'])} low {ev['lo']:.0f}°F (threshold {FROST_F:.0f}°F)."


def _alerts_text() -> str:
    al = active_alerts()
    if al is None:
        return ("NWS alert check failed (api.weather.gov unreachable), so alert status is "
                "unknown, not clear.")
    if not al:
        return "No active National Weather Service alerts."
    return "Active NWS alerts:\n" + "\n".join(f"  [{a['severity']}] {a['event']} — {a['headline']}" for a in al)


def execute(action: str = "briefing", days: int = 7) -> str:
    try:
        if action == "alerts":
            return _alerts_text()
        rows = forecast_days(days)
        if action == "rain":
            return _rain_text(rows)
        if action == "frost":
            return _frost_text(rows)
        if action == "briefing":
            return "\n".join([_frost_text(rows), _rain_text(rows), _alerts_text()])
        return f"Error: unknown action '{action}' (use briefing, rain, frost, or alerts)."
    except httpx.HTTPError as e:
        return f"Weather backend error (Open-Meteo/NWS): {e}"
    except Exception as e:  # noqa: BLE001
        return f"Error getting weather ({action}): {e}"
