"""Speak a message on any online Assist satellite (the kitchen Voice PE, and any added later).

Factored out of the morning briefing so other senders — reminders, and whatever comes next —
can talk out loud through the same path instead of each re-implementing it. Best-effort by
design: an offline satellite (nobody home) or a failed announce never raises to the caller,
so the reliable channel (the phone push) is never held up by a speaker that didn't answer.

Reads HA_URL / HA_TOKEN from the environment; callers load the secret bundle (config.load_secrets)
before importing, same as the timer scripts already do.
"""
from __future__ import annotations

import os
import sys

import httpx

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
_HDRS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def satellites() -> list[str]:
    """Assist satellites currently reachable (the kitchen Voice PE, and any added later)."""
    r = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=15)
    r.raise_for_status()
    return [s["entity_id"] for s in r.json()
            if s["entity_id"].startswith("assist_satellite.")
            and s["state"] not in ("unavailable", "unknown")]


def announce_report(message: str) -> dict:
    """HA service acceptance is observable; actual hearing is not."""
    report = {"discovery": "ok", "satellites": []}
    try:
        sats = satellites()
    except Exception as e:
        report.update(discovery="unknown", error_type=type(e).__name__)
        return report
    for ent in sats:
        attempt = {"entity_id": ent, "status": "unknown"}
        try:
            httpx.post(f"{HA_URL}/api/services/assist_satellite/announce", headers=_HDRS,
                       json={"entity_id": ent, "message": message},
                       timeout=120).raise_for_status()
            attempt["status"] = "accepted"
        except Exception as e:
            attempt["error_type"] = type(e).__name__
            print(f"announce on {ent} failed: {type(e).__name__}", file=sys.stderr)
        report["satellites"].append(attempt)
    return report


def announce(message: str) -> list[str]:
    """Compatibility helper: satellites whose HA announcement call succeeded."""
    return [a["entity_id"] for a in announce_report(message)["satellites"]
            if a["status"] == "accepted"]
