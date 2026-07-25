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


def announce(message: str) -> list[str]:
    """Speak `message` on every reachable satellite. Returns the ones that played it.
    Each failure is logged and skipped — a spoken announcement is a bonus, never a gate."""
    done = []
    try:
        sats = satellites()
    except Exception as e:  # noqa: BLE001 — HA unreachable: nobody speaks, nobody errors
        print(f"announce: could not list satellites: {e}", file=sys.stderr)
        return done
    for ent in sats:
        try:
            httpx.post(f"{HA_URL}/api/services/assist_satellite/announce", headers=_HDRS,
                       json={"entity_id": ent, "message": message},
                       timeout=120).raise_for_status()  # HA blocks until playback ends
            done.append(ent)
        except Exception as e:  # noqa: BLE001
            print(f"announce on {ent} failed: {e}", file=sys.stderr)
    return done
