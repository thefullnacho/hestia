"""Puppy watch — the neonatal weight curve, checked by arithmetic instead of by a human at 3am.

Deterministic sibling of garden_watch and pest_watch. Runs twice daily via a systemd user
timer while a litter is young, reads the weights already in `records`, and pushes only when
something needs a person:

  - Not weighed today      — the whole system is blind if the number never gets taken, so a
                             missing weighing is itself the first alert, per pup
  - Lost weight            — down vs the previous reading
  - Flat                   — no gain across FLAT_DAYS consecutive days
  - Below birth weight     — still under day-0 after REGAIN_BY_DAY
  - Off peak               — down DROP_PCT or more from this pup's best reading, the
                             strongest fading signal and the one that survives a noisy scale
  - Whelped, nothing logged — a litter exists with no pups recorded against it

The thresholds are husbandry, not diagnosis: every alert says weigh-and-watch or call the
vet, and the vet line is stated rather than implied. The knowledge behind them lives in
`brain/skills/whelping/references/knowledge.md` (small-breed pups are ~4-8 oz at birth,
healthy pups gain a little every day, flat or dropping is the earliest sign of a fading pup).

No LLM anywhere in this file. A fading neonate is a threshold problem and a threshold
problem is a row and a comparison, which is the whole thesis of this repo.

Each run also makes sure every tie on the books has its day-28 pregnancy-check and day-56
whelp-watch reminders (`breeding_followups`), quietly and whether or not a litter is young:
the backstop for a tie that never went through the `records` tool.

A pup that has never been weighed at all cannot be compared to anything, so it is reported
as unweighed rather than silently skipped — the failure this file exists to prevent is a
number nobody looked at, and a pup nobody weighed is the same failure one step earlier.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import httpx

import config  # puts brain/ on sys.path + owns paths

config.load_secrets()
import breeding_followups  # noqa: E402
import records_store  # noqa: E402  (after config puts brain/ on the path)

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
NOTIFY = os.environ.get("PUPPY_NOTIFY", os.environ.get("GARDEN_NOTIFY", "mobile_app_alexs_iphone"))
WATCH_DAYS = int(os.environ.get("PUPPY_WATCH_DAYS", "21"))     # how long a litter stays on daily weights
FLAT_DAYS = int(os.environ.get("PUPPY_FLAT_DAYS", "2"))        # consecutive days with no gain
DROP_PCT = float(os.environ.get("PUPPY_DROP_PCT", "10"))       # % off peak that means call the vet
REGAIN_BY_DAY = int(os.environ.get("PUPPY_REGAIN_BY_DAY", "3"))  # back to birth weight by this day
WEIGH_BY_HOUR = int(os.environ.get("PUPPY_WEIGH_BY_HOUR", "12"))  # don't nag for today's weight before this
STATE_PATH = config.PUPPY_STATE

_OZ = 28.3495
_VET = "Call the vet."


def oz(grams: float) -> str:
    """Grams as ounces, which is the unit a small-breed litter is actually weighed in."""
    return f"{grams / _OZ:.1f} oz"


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _daily(series: list[dict]) -> list[dict]:
    """One reading per calendar day — the last of that day, since a re-weigh corrects the
    first. Two readings an hour apart are one day on the curve, not a gain and a loss."""
    by_day: dict[str, dict] = {}
    for row in series:
        by_day[row["date"]] = row
    return [by_day[d] for d in sorted(by_day)]


def pup_alerts(pup: str, series: list[dict], age_days: int, today: dt.date) -> list[str]:
    """Every threshold this pup crosses today. Pure arithmetic over its own readings —
    no cross-pup comparison, because litter-mates legitimately differ in size and the
    thing that matters is whether THIS pup is gaining."""
    days = _daily(series)
    if not days:
        return [f"{pup} has never been weighed. Weigh and log so there is a baseline."]

    alerts = []
    latest = days[-1]
    # From the FULL series, not the day-collapsed one: a pup weighed on the day it was born
    # has its birth row collapsed away by that same-day weighing, and looking only at `days`
    # would silently disable the below-birth-weight rule for exactly the pups it matters
    # most for.
    birth = next((d for d in series if d["source"] == "birth"), days[0])
    peak = max(days, key=lambda d: d["grams"])

    if latest["date"] != today.isoformat():
        since = (today - dt.date.fromisoformat(latest["date"])).days
        alerts.append(f"{pup} has not been weighed today (last {oz(latest['grams'])} "
                      f"{since} day{'s' if since != 1 else ''} ago). Weigh and log.")

    if len(days) >= 2 and latest["grams"] < days[-2]["grams"]:
        lost = days[-2]["grams"] - latest["grams"]
        alerts.append(f"{pup} LOST weight: {oz(days[-2]['grams'])} -> {oz(latest['grams'])} "
                      f"(down {oz(lost)}). Weigh again in a few hours and watch nursing.")

    # Flat needs FLAT_DAYS+1 readings to see FLAT_DAYS transitions. Reported separately from a
    # loss because flat is the quieter signal and the one a person talks themselves out of.
    if len(days) >= FLAT_DAYS + 1 and latest["grams"] >= days[-2]["grams"]:
        window = days[-(FLAT_DAYS + 1):]
        if all(b["grams"] <= a["grams"] for a, b in zip(window, window[1:])):
            alerts.append(f"{pup} has not gained in {FLAT_DAYS} days "
                          f"({oz(window[0]['grams'])} -> {oz(latest['grams'])}). "
                          f"Healthy pups gain a little every day — watch it nurse.")

    # A normal pup dips in the first day or two, then passes birth weight. Past that, still
    # being under day-0 is not a dip any more.
    if age_days >= REGAIN_BY_DAY and birth["source"] == "birth" and latest["grams"] < birth["grams"]:
        alerts.append(f"{pup} is still under its birth weight at day {age_days} "
                      f"({oz(latest['grams'])} vs {oz(birth['grams'])} born). {_VET}")

    if peak["grams"] > 0:
        off = (peak["grams"] - latest["grams"]) / peak["grams"] * 100
        if off >= DROP_PCT:
            alerts.append(f"{pup} is {off:.0f}% below its best weight "
                          f"({oz(peak['grams'])} on {peak['date']} -> {oz(latest['grams'])}). "
                          f"That is the fading-pup pattern. {_VET}")
    return alerts


def build_alerts(now: dt.datetime | None = None, persist: bool = False) -> list[str]:
    """The actionable lines for this run, or [] when there is nothing to say.

    `persist` records what was sent so the same alert doesn't repeat within a day; pass
    False for dry-runs so testing never consumes a real alert.
    """
    now = now or dt.datetime.now()
    today = now.date()
    litter = records_store.active_litter(WATCH_DAYS, now=now)
    if not litter:
        return []

    if not litter["pups"]:
        lines = [f"{litter['name']} is on the books (whelped {litter['whelp_date']}) but no "
                 f"puppies are logged against it. Log each pup so weights have somewhere to go."]
    else:
        lines = []
        for pup in litter["pups"]:
            for alert in pup_alerts(pup, records_store.weight_series(pup),
                                    litter["age_days"], today):
                # Before the weigh-in hour, a missing weight is just a morning that hasn't
                # happened yet. Every other alert stands regardless of the time of day.
                if "not been weighed today" in alert and now.hour < WEIGH_BY_HOUR:
                    continue
                lines.append(alert)

    # One of each alert per day. The watcher runs twice so a morning problem gets an evening
    # look, but repeating the identical line teaches the user to swipe the notification away.
    state = _load_state()
    sent = state.get(today.isoformat(), [])
    fresh = [line for line in lines if line not in sent]
    if persist and fresh:
        _save_state({today.isoformat(): sent + fresh})  # only today's keys are worth keeping
    return fresh


def push(title: str, message: str) -> None:
    httpx.post(f"{HA_URL}/api/services/notify/{NOTIFY}",
               headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
               json={"title": title, "message": message}, timeout=15).raise_for_status()


def follow_ups(dry_run: bool = False) -> None:
    """Each run, make sure every tie on the books has its pregnancy-check and whelp-watch
    reminders: the backstop for a tie that never went through the `records` tool. Quiet unless
    it files one, and a failure here must not stop the pup alerts that follow."""
    try:
        for m in breeding_followups.ensure(dry_run=dry_run):
            print(f"puppy-watch: {'would file' if dry_run else 'filed'} reminder: "
                  f"{m['dam']} {m['what']} on {m['due_at'][:10]}")
    except Exception as e:  # noqa: BLE001 (see the docstring)
        print(f"puppy-watch: breeding follow-ups failed: {type(e).__name__}: {e}", file=sys.stderr)


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    follow_ups(dry_run)
    try:
        alerts = build_alerts(persist=not dry_run)
    except Exception as e:  # noqa: BLE001 — a watcher that crashes is a watcher nobody notices
        print(f"puppy-watch: failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not alerts:
        print("puppy-watch: nothing actionable")
        return 0
    msg = "\n".join(f"• {a}" for a in alerts)
    if dry_run:
        print("puppy-watch (dry-run) would push:\n" + msg)
        return 0
    push("🐶 Puppies", msg)
    print("puppy-watch: pushed:\n" + msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
