"""`home` tool — control and query the house via Home Assistant.

Today the controllable devices are LIFX lights (see Phase 2). Household *state* is
read live here, never cached in memory. Uses the long-lived token from secrets/ha.env.
"""
from __future__ import annotations

import os
import time

import httpx

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
_HDRS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

COLORS = {
    "red": [255, 0, 0], "green": [0, 255, 0], "blue": [0, 80, 255],
    "warm": [255, 170, 90], "white": [255, 255, 255], "orange": [255, 140, 0],
    "purple": [160, 0, 255], "yellow": [255, 220, 40], "pink": [255, 80, 160],
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "home",
        "description": ("Control or query the smart home via Home Assistant. Controllable: "
                        "LIFX lights. Readable: garden soil-moisture sensors (one per bed). "
                        "Use the exact entity_id from the catalog in the system prompt. "
                        "Room groups (light.light_*_lights) control a whole room at once. "
                        "To read a bed's moisture, get_state the matching sensor.*_soilmoisture* entity."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["turn_on", "turn_off", "toggle", "get_state"]},
                "entity_id": {"type": "string", "description": "An exact entity_id (e.g. light.tv, light.light_kitchen_lights) OR, for get_state, a bed/device's friendly name like 'Carrots' or 'Beets' — get_state resolves names to the right sensor. Omit on get_state to list lights + soil sensors."},
                "brightness_pct": {"type": "integer", "description": "0-100, optional for turn_on"},
                "color": {"type": "string", "description": "optional color name for turn_on: " + ", ".join(COLORS)},
            },
            "required": ["action"],
        },
    },
}

_cache: dict = {"t": 0.0, "catalog": "", "soil": ""}


def _soil(states: list[dict]) -> list[dict]:
    """The per-bed soil-moisture sensors (not the raw soilad/soilbatt channels)."""
    return sorted(
        (s for s in states if "soilmoisture" in s["entity_id"]),
        key=lambda s: s["entity_id"],
    )


def _line(s: dict) -> str:
    unit = s["attributes"].get("unit_of_measurement", "")
    return f"  {s['entity_id']} — {s['attributes'].get('friendly_name', '?')} [{s['state']}{unit}]"


def _refresh() -> str | None:
    """Fetch HA once and (re)build both the light catalog and the soil block. Returns an
    error string if HA is unreachable, else None. Both blocks share one 60s cache so a
    moisture question and a light question don't each hit HA."""
    if time.time() - _cache["t"] < 60 and (_cache["catalog"] or _cache["soil"]):
        return None
    try:
        states = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=8).json()
    except Exception as e:  # noqa: BLE001
        return f"(home catalog unavailable: {e})"
    lights = [s for s in states if s["entity_id"].startswith("light.")]
    groups = [s for s in lights if s["entity_id"].startswith("light.light_")]
    singles = [s for s in lights if not s["entity_id"].startswith("light.light_")]
    out = ["Lights you can control (use the exact entity_id):", "Room groups:"]
    out += [_line(s) for s in groups]
    out += ["Individual lights:"] + [_line(s) for s in singles]
    soil = _soil(states)
    soil_block = "\n".join(_line(s) for s in soil) if soil else ""
    _cache.update(t=time.time(), catalog="\n".join(out), soil=soil_block)
    return None


def catalog() -> str:
    """A compact list of controllable lights (cached 60s). Soil sensors get their own
    authoritative block — see soil_catalog() — so the model answers moisture questions by
    reading values, not by guessing bed names through the tool."""
    err = _refresh()
    return err or _cache["catalog"]


def soil_catalog() -> str:
    """The COMPLETE set of per-bed soil-moisture readings, as an answer source (cached 60s).
    Empty string if there are no soil sensors; an error note if HA is unreachable."""
    err = _refresh()
    if err:
        return err
    return _cache["soil"]


def _resolve(entity_id: str, states: list[dict]) -> str | None:
    """Map a loose reference to a real entity_id. Exact id wins; otherwise match a
    sensor/light by friendly name (so 'Carrots' or 'beets' finds the right sensor),
    which a small model handles far more reliably than copying an entity_id from a table.
    """
    by_id = {s["entity_id"] for s in states}
    if entity_id in by_id:
        return entity_id
    q = entity_id.lower().strip()
    named = [s for s in states if q in s["attributes"].get("friendly_name", "").lower()]
    if len(named) == 1:
        return named[0]["entity_id"]
    if named:  # prefer an exact friendly-name hit when several contain the string
        exact = [s for s in named if s["attributes"].get("friendly_name", "").lower() == q]
        if len(exact) == 1:
            return exact[0]["entity_id"]
    return None


def execute(action: str, entity_id: str | None = None,
            brightness_pct: int | None = None, color: str | None = None) -> str:
    try:
        if action == "get_state":
            if entity_id:
                states = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=8).json()
                resolved = _resolve(entity_id, states)
                if not resolved:
                    # A garden/bed name the model guessed often won't match a sensor exactly
                    # (e.g. it invents "Herbs"). Rather than dead-end — which derails the whole
                    # answer — hand back every soil reading so the readout still happens.
                    soil = _soil(states)
                    if soil:
                        readings = "; ".join(
                            f"{s['attributes'].get('friendly_name', s['entity_id'])}={s['state']}%"
                            for s in soil)
                        return ("All soil-moisture readings (report these directly; do not mention "
                                f"any name that wasn't found): {readings}")
                    return f"No device matches '{entity_id}'. Check the catalog for the exact name."
                d = next(s for s in states if s["entity_id"] == resolved)
                unit = d.get("attributes", {}).get("unit_of_measurement", "")
                return f"{d.get('attributes', {}).get('friendly_name', resolved)} is {d.get('state')}{unit}"
            states = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=8).json()
            ls = [s for s in states if s["entity_id"].startswith("light.")]
            parts = [f"{s['entity_id']}={s['state']}" for s in ls]
            soil = _soil(states)
            if soil:
                parts.append("| soil: " + "; ".join(
                    f"{s['attributes'].get('friendly_name', s['entity_id'])}={s['state']}%"
                    for s in soil))
            return "; ".join(parts)

        if not entity_id:
            return "Error: entity_id is required for that action."
        service = {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"}.get(action)
        if not service:
            return f"Error: unknown action '{action}'."
        data: dict = {"entity_id": entity_id}
        if action == "turn_on":
            if brightness_pct is not None:
                data["brightness_pct"] = max(0, min(100, int(brightness_pct)))
            if color and color.lower() in COLORS:
                data["rgb_color"] = COLORS[color.lower()]
        r = httpx.post(f"{HA_URL}/api/services/light/{service}", headers=_HDRS, json=data, timeout=12)
        if r.status_code >= 400:
            return f"Home Assistant returned {r.status_code}: {r.text[:120]}"
        # confirm new state
        chk = httpx.get(f"{HA_URL}/api/states/{entity_id}", headers=_HDRS, timeout=8).json()
        state = chk.get("state")
        if state == "unavailable":
            return f"Sent {action} to {entity_id}, but it's currently unavailable (bulb may be powered off at the switch)."
        return f"Done — {entity_id} is now {state}."
    except Exception as e:  # noqa: BLE001
        return f"Error talking to Home Assistant: {e}"
