"""`reminder` tool — one-shot, time-triggered phone reminders.

"Remind me at 7:00 to pot up the transplants" → a row in the reminders store that a
one-minute systemd timer (reminders_tick.py) pushes to the phone at that time. The model
doesn't remember anything and nothing in the firing path uses the model — it just files
the row. This is the schedule-problem-goes-to-a-timer half of the determinism principle.
"""
from __future__ import annotations

import datetime as dt
import re

import reminders_store as store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "reminder",
        "description": ("Set a one-shot reminder that pushes a notification to the user's phone at "
                        "a chosen time. 'create' with the reminder text and when it should fire; "
                        "'list' shows pending reminders; 'cancel' drops one by its id. For 'when', "
                        "pass the user's time phrase VERBATIM — '7am', 'at 7:00', '9pm', 'tomorrow "
                        "at 7', 'tonight', 'tomorrow morning'. The tool computes the actual date "
                        "itself; do NOT calculate a date yourself (you get it wrong). Only pass a "
                        "full ISO 8601 datetime when the user names an explicit calendar date like "
                        "'June 20'. Use this for any 'remind me to …' request; never hold it yourself."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "cancel"]},
                "text": {"type": "string", "description": "for create: what to remind the user about, e.g. 'pot up the transplants'"},
                "when": {"type": "string", "description": "for create: the user's time phrase verbatim — '7am', '9pm', 'tomorrow at 7', 'tonight', 'tomorrow morning'. The tool figures out the date. Use a full ISO datetime ONLY for an explicit calendar date."},
                "id": {"type": "integer", "description": "for cancel: the reminder id shown by 'list'"},
            },
            "required": ["action"],
        },
    },
}

# A bare clock time: 7, 7:00, 7am, 7:30 pm, 19:00.
_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?$", re.I)
# A leading relative day, so the TOOL (not the model) does the date math.
_DAY_RE = re.compile(r"^(today|tonight|tomorrow|tmrw|tom)\b[\s,]*(?:at\s+)?(.*)$", re.I)
# Fuzzy dayparts -> a default hour, so "tomorrow morning" resolves deterministically.
_DAYPART = {"morning": 9, "noon": 12, "midday": 12, "afternoon": 15,
            "evening": 18, "night": 21, "tonight": 21}


def _parse_when(when: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """Resolve a when-phrase to an absolute future datetime, or None if unreadable.

    All the date arithmetic lives here — never in the model, which is unreliable at it.
    Handles: a full ISO datetime (explicit calendar date); a bare clock time ('7:00',
    '9pm') -> next occurrence; an optional leading 'today/tonight/tomorrow'; and a fuzzy
    daypart ('tomorrow morning', 'this evening')."""
    now = now or dt.datetime.now()
    s = (when or "").strip().lower()
    if not s:
        return None
    # 1) ISO datetime — only for an explicit calendar date the user actually named.
    try:
        d = dt.datetime.fromisoformat(s.replace("z", "").strip())
        if d.year > 1900 and ("t" in s or " " in s or "-" in s):
            return d
    except ValueError:
        pass
    # 2) optional relative-day prefix -> a day offset + the remaining time phrase.
    s = s.replace("this ", "").strip()
    plus = 0
    m = _DAY_RE.match(s)
    if m:
        word, rest = m.group(1), m.group(2).strip()
        plus = 1 if word in ("tomorrow", "tmrw", "tom") else 0
        if word == "tonight" and not rest:
            rest = "night"
        s = rest
    s = re.sub(r"^at\s+", "", s).strip()  # "at 7:00" with no day word
    if not s:
        return None
    # 3) fuzzy daypart word -> default hour.
    if s in _DAYPART:
        hour, minute = _DAYPART[s], 0
    else:
        # 4) explicit clock time.
        mt = _TIME_RE.match(s)
        if not mt:
            return None
        hour, minute = int(mt.group(1)), int(mt.group(2) or 0)
        ampm = (mt.group(3) or "").replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + dt.timedelta(days=plus)
    if cand <= now:  # time already passed today and no explicit 'tomorrow' -> next day
        cand += dt.timedelta(days=1)
    return cand


def _fmt(d: dt.datetime) -> str:
    return d.strftime("%a %b %-d at %-I:%M %p")


def execute(action: str, text: str | None = None, when: str | None = None,
            id: int | None = None) -> str:
    if action == "create":
        if not (text or "").strip():
            return "What should I remind you about?"
        due = _parse_when(when or "")
        if not due:
            return f"I couldn't read the time '{when}'. Try a time like '7:00' or a full date and time."
        rid = store.add(due.isoformat(timespec="seconds"), text.strip())
        return f"Reminder #{rid} set — I'll ping your phone {_fmt(due)}: {text.strip()}"
    if action == "list":
        rows = store.pending()
        if not rows:
            return "No reminders pending."
        lines = []
        for r in rows:
            try:
                w = _fmt(dt.datetime.fromisoformat(r["due_at"]))
            except ValueError:
                w = r["due_at"]
            lines.append(f"#{r['id']} — {w}: {r['text']}")
        return "Pending reminders:\n" + "\n".join(lines)
    if action == "cancel":
        if id is None:
            return "Which reminder? Give me its id (say 'list my reminders' to see them)."
        return "Reminder cancelled." if store.cancel(int(id)) else f"No pending reminder #{id}."
    return f"Error: unknown reminder action '{action}'."
