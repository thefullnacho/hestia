"""Breeding follow-ups: the pregnancy check and the whelp-watch start, filed as reminders.

A dam's day-28 check and day-56 whelp-watch are dates, not judgement. When a tie is on the
books this files both as ordinary reminders (a row, fired by the one-minute reminders timer and
spoken on the kitchen Voice PE), so nothing depends on the model remembering to ask for them.

Two triggers share `ensure()`: the `records` tool calls it the moment a tie is logged, and the
twice-daily puppy watch calls it as a backstop for ties that never went through the tool (a
retroactive entry, a script). It is idempotent, so running it often costs nothing, and it never
files a reminder whose time has already passed.

Run it by hand with `python breeding_followups.py [--dry-run]`.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

import records_store
import reminders_store

# The action verbs that mean a breeding happened. "paired" is a pairing, not a tie, and a
# correction to a tie date ("tie date corrected") is not a second tie, so those are not here.
TIE_ACTIONS = ("tied", "tie", "bred", "mated")
SAME_BREEDING_DAYS = 10   # a second tie this soon after the first is the same breeding
GESTATION_DAYS = 63       # the due date, matching skills/whelping/references/knowledge.md
CHECK_DAY = 28            # time to confirm: palpation or ultrasound with the vet
WATCH_DAY = 56            # twice-daily temperatures begin
FIRE_HOUR = 8             # local, the same hour as the reminders already filed by hand


def _day(d: dt.date) -> str:
    return f"{d:%B} {d.day}"


def _milestones(dam: str, tie: dt.date) -> list[dict]:
    """What to remind about for one breeding. `words` are what an existing reminder for the
    same dam would say if someone had already set this one by hand."""
    due = tie + dt.timedelta(days=GESTATION_DAYS)
    return [
        {"what": "pregnancy check", "day": tie + dt.timedelta(days=CHECK_DAY),
         "words": ("pregnan", "ultrasound", "palpat"),
         "text": (f"{dam} pregnancy check: bred {_day(tie)}, day {CHECK_DAY} today. "
                  "Time to confirm: schedule a palpation or ultrasound with the vet.")},
        {"what": "whelp-watch", "day": tie + dt.timedelta(days=WATCH_DAY),
         "words": ("whelp", "temperature"),
         "text": (f"{dam} whelp-watch begins: day {WATCH_DAY} since breeding, due around {_day(due)}. "
                  "Start twice-daily temperature checks; a drop below 99 degrees means puppies "
                  "within about a day.")},
    ]


def _ties() -> list[tuple[str, dt.date]]:
    """(dam, tie date) for every breeding on the books, one per breeding. Only a dam counts:
    the entity must be a pet, and one recorded as male or as the sire is not."""
    marks = ",".join("?" * len(TIE_ACTIONS))
    with records_store._conn() as c:
        rows = c.execute(
            f"SELECT en.name AS dam, en.attrs AS attrs, e.ts AS ts FROM events e "
            f"JOIN entities en ON en.id = e.entity_id "
            f"WHERE en.kind = 'pet' AND lower(e.action) IN ({marks}) ORDER BY en.name, e.ts",
            TIE_ACTIONS).fetchall()
    out: list[tuple[str, dt.date]] = []
    last: dict[str, dt.date] = {}
    for r in rows:
        attrs = json.loads(r["attrs"] or "{}")
        if str(attrs.get("sex", "")).lower() == "male" or str(attrs.get("role", "")).lower() == "sire":
            continue
        try:
            day = dt.datetime.fromisoformat(r["ts"]).date()
        except ValueError:
            continue
        if r["dam"] in last and (day - last[r["dam"]]).days <= SAME_BREEDING_DAYS:
            continue
        last[r["dam"]] = day
        out.append((r["dam"], day))
    return out


def _existing() -> list[tuple[str, str]]:
    """(due date, lowercased text) of every reminder, fired or not, so a milestone someone
    already set by hand is not set twice."""
    with records_store._conn() as c:
        return [(r["due_at"][:10], r["text"].lower())
                for r in c.execute("SELECT due_at, text FROM reminders").fetchall()]


def ensure(now: dt.datetime | None = None, dry_run: bool = False) -> list[dict]:
    """File each follow-up that is missing and still ahead of `now`. Returns what it filed (or,
    on a dry run, would file): dicts with dam, what, due_at, text and, once filed, id."""
    now = (now or dt.datetime.now()).replace(tzinfo=None)
    seen = _existing()
    out = []
    for dam, tie in _ties():
        for m in _milestones(dam, tie):
            due = dt.datetime.combine(m["day"], dt.time(FIRE_HOUR))
            if due <= now:
                continue
            iso = due.isoformat(timespec="seconds")
            if any(d == iso[:10] and dam.lower() in t and any(w in t for w in m["words"])
                   for d, t in seen):
                continue
            entry = {"dam": dam, "what": m["what"], "due_at": iso, "text": m["text"]}
            if not dry_run:
                entry["id"] = reminders_store.add(iso, m["text"], announce=True)
                seen.append((iso[:10], m["text"].lower()))
            out.append(entry)
    return out


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    made = ensure(dry_run=dry_run)
    for m in made:
        print(f"{'would file' if dry_run else 'filed'}: {m['dam']} {m['what']} on {m['due_at'][:10]}"
              + ("" if dry_run else f" (reminder {m['id']})") + f"\n    {m['text']}")
    if not made:
        print("breeding-followups: nothing to file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
