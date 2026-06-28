"""`weather` tool — garden-focused forecast: rain QPF, freeze watch, NWS alerts.

Open-Meteo (free, no key) for the quantitative forecast — daily `precipitation_sum`
is the "how much rain" QPF and `temperature_2m_min` is the freeze signal. NWS
`api.weather.gov` adds official active alerts (frost/freeze warnings, etc.). Defaults
to the homestead lat/lon (from HA config). Read-only — no safety-gate concerns.

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


def active_alerts() -> list[dict]:
    """Active NWS alerts for the homestead's forecast zone (best-effort)."""
    h = {"User-Agent": _UA, "Accept": "application/geo+json"}
    try:
        pt = httpx.get(f"https://api.weather.gov/points/{LAT},{LON}", headers=h, timeout=15)
        pt.raise_for_status()
        zone = pt.json()["properties"].get("forecastZone", "").rstrip("/").split("/")[-1]
        if not zone:
            return []
        al = httpx.get(f"https://api.weather.gov/alerts/active?zone={zone}", headers=h, timeout=15)
        al.raise_for_status()
        out = []
        for f in al.json().get("features", []):
            p = f.get("properties", {})
            out.append({"event": p.get("event", "Alert"),
                        "headline": p.get("headline") or p.get("event", ""),
                        "severity": p.get("severity", "")})
        return out
    except Exception:  # noqa: BLE001 — NWS flakes; alerts are a bonus layer
        return []


def first_freeze(rows: list[dict]) -> dict | None:
    """First day at/below the frost threshold, with a freeze/frost label."""
    for row in rows:
        if row["lo"] <= FROST_F:
            return {**row, "kind": "freeze" if row["lo"] <= FREEZE_F else "frost"}
    return None


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
        return (f"No frost or freeze in the next {len(rows)} days "
                f"(lowest forecast low is {min(r['lo'] for r in rows):.0f}°F).")
    label = "Hard freeze" if ev["kind"] == "freeze" else "Frost"
    return f"{label} watch: {_nice_date(ev['date'])} low {ev['lo']:.0f}°F (threshold {FROST_F:.0f}°F)."


def _alerts_text() -> str:
    al = active_alerts()
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
