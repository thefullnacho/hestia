"""Reminders tick — fire due reminders to the phone. Runs every minute via a systemd timer.

Dumb on purpose: find reminders whose time has come and haven't fired, push each to the
phone via Home Assistant's notify service, mark it fired. No model, no judgment — the
firing half of the determinism principle. A push that fails is left unfired so the next
tick retries (at-least-once). --dry-run prints what it would send without pushing.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import httpx

import config  # puts brain/ on sys.path + owns paths

config.load_secrets()

import reminders_store as store  # noqa: E402  (after secrets load)

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
# Reuse the garden-watch phone target by default so there's one place to change the device.
NOTIFY = os.environ.get("REMINDER_NOTIFY", os.environ.get("GARDEN_NOTIFY", "mobile_app_alexs_iphone"))
_HDRS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def push(message: str) -> None:
    httpx.post(f"{HA_URL}/api/services/notify/{NOTIFY}", headers=_HDRS,
               json={"title": "⏰ Reminder", "message": message}, timeout=15).raise_for_status()


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    now = dt.datetime.now().isoformat(timespec="seconds")
    due = store.due(now)
    if not due:
        return 0
    for r in due:
        if dry_run:
            print(f"reminders (dry-run) would push #{r['id']}: {r['text']}")
            continue
        try:
            push(r["text"])
            store.mark_fired(r["id"], dt.datetime.now().isoformat(timespec="seconds"))
            print(f"reminders: pushed #{r['id']}: {r['text']}")
        except Exception as e:  # noqa: BLE001 — leave it unfired; next tick retries
            print(f"reminders: push failed for #{r['id']}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
