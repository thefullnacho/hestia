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
        "description": ("Set a one-shot reminder OR a kitchen/cooking TIMER that pushes a "
                        "notification to the user's phone. 'create' with when it should fire (and "
                        "the text/label if there is one); 'list' shows pending reminders; 'cancel' "
                        "drops one by its id. For 'when', pass the user's phrase VERBATIM — a "
                        "relative duration for a timer ('10 minutes', 'in 20 min', '1 hour 30 "
                        "minutes', '90 seconds', 'an hour'), a clock time ('7am', 'at 7:00', "
                        "'9pm', 'tonight', 'tomorrow at 7', 'tomorrow morning'), or a date "
                        "('June 20', 'July 1, 2026 at 7:05 am'). The tool computes the actual time "
                        "itself; do NOT calculate or reformat it yourself (you get it wrong). An ISO "
                        "8601 datetime is also accepted. Use this for any 'remind me to …' OR 'set a "
                        "timer' request; never hold it yourself."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "cancel"]},
                "text": {"type": "string", "description": "for create: what to remind about, e.g. 'pot up the transplants'. Optional for a bare timer (defaults to 'Timer')."},
                "when": {"type": "string", "description": "for create: the user's time phrase verbatim — a duration ('10 minutes', 'in 20 min', '1 hour 30 minutes', '90 seconds', 'an hour'), a clock time ('7am', '9pm', 'tomorrow at 7', 'tonight'), or a date ('June 20', 'July 1, 2026 at 7:05 am'). The tool figures out the time; don't reformat it. An ISO datetime is also accepted."},
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

# Month names (full + 3-letter, plus "sept") -> number, so a named calendar date like
# "July 1, 2026 at 7:05 am" or "June 20" resolves in the TOOL, not the model.
_MONTHS: dict[str, int] = {}
for _i, _full in enumerate(("january", "february", "march", "april", "may", "june", "july",
                            "august", "september", "october", "november", "december"), 1):
    _MONTHS[_full] = _i
    _MONTHS[_full[:3]] = _i
_MONTHS["sept"] = 9
# "july 1[, 2026]" or "1 july[ 2026]", optionally followed by "at"/comma + a time phrase.
_NAMED_DATE_RE = re.compile(
    r"^(?:(?P<mon1>[a-z]+)\s+(?P<day1>\d{1,2})|(?P<day2>\d{1,2})\s+(?P<mon2>[a-z]+))"
    r"(?:,?\s*(?P<year>\d{4}))?\b[\s,]*(?:at\s+)?(?P<rest>.*)$", re.I)
# Hour to use for a date given with no clock time ("June 20" -> 9am).
_DEFAULT_HOUR = 9

# A relative duration anywhere in the phrase: "10 minutes", "in 10 min", "1 hour 30 minutes",
# "90 seconds", "10m", "an hour". Requires an explicit unit, so a bare clock time ('7') or a
# named date never lands here. This is the kitchen-timer path ("set a 10 minute timer").
_DUR_UNIT = {"h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
             "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
             "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}
# A numeric quantity may sit right against the unit ("10m"). A word quantity ("a"/"an") MUST
# be space-separated AND use a spelled-out unit — otherwise "7:05 am" reads as "a minute" and
# "as"/"ah" would too. Single-letter units are numeric-only for the same reason.
_DUR_NUM = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s)\b", re.I)
_DUR_WORD = re.compile(r"\ban?\s+(hours?|hrs?|hr|minutes?|mins?|min|seconds?|secs?|sec)\b", re.I)


def _duration(s: str, now: dt.datetime) -> dt.datetime | None:
    """A relative duration ('10 minutes', 'in 1 hour 30 min', '90 seconds', 'an hour') ->
    now + that span, summing every unit found. None if no unit appears (so clock times and
    named dates fall through to their own parsers)."""
    total = 0.0
    for num, unit in _DUR_NUM.findall(s):
        total += float(num) * _DUR_UNIT[unit.lower()]
    for unit in _DUR_WORD.findall(s):
        total += _DUR_UNIT[unit.lower()]
    return now + dt.timedelta(seconds=round(total)) if total > 0 else None


def _clock(s: str) -> tuple[int, int] | None:
    """A time phrase -> (hour, minute): a fuzzy daypart or an explicit clock time, else None."""
    s = s.strip()
    if not s:
        return None
    if s in _DAYPART:
        return _DAYPART[s], 0
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
    return hour, minute


def _named_date(s: str, now: dt.datetime) -> dt.datetime | None:
    """A named calendar date ('July 1, 2026 at 7:05 am', 'June 20') -> datetime, else None.

    Time defaults to 9am when omitted. A bare month+day with no year that has already
    passed this year rolls to next year, so 'June 20' always means the next June 20."""
    m = _NAMED_DATE_RE.match(s)
    if not m:
        return None
    mon = (m.group("mon1") or m.group("mon2")).lower()
    if mon not in _MONTHS:
        return None
    day = int(m.group("day1") or m.group("day2"))
    year = int(m.group("year")) if m.group("year") else now.year
    clk = _clock(m.group("rest")) if m.group("rest").strip() else (_DEFAULT_HOUR, 0)
    if clk is None:
        return None
    try:
        cand = dt.datetime(year, _MONTHS[mon], day, clk[0], clk[1])
    except ValueError:  # e.g. Feb 30
        return None
    if not m.group("year") and cand < now:  # no year named and already past -> next year
        try:
            cand = cand.replace(year=year + 1)
        except ValueError:
            return None
    return cand


def _parse_when(when: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """Resolve a when-phrase to an absolute future datetime, or None if unreadable.

    All the date arithmetic lives here — never in the model, which is unreliable at it.
    Handles: a full ISO datetime; a named calendar date ('July 1, 2026 at 7:05 am',
    'June 20'); a bare clock time ('7:00', '9pm') -> next occurrence; an optional leading
    'today/tonight/tomorrow'; and a fuzzy daypart ('tomorrow morning', 'this evening')."""
    now = now or dt.datetime.now()
    s = (when or "").strip().lower()
    if not s:
        return None
    # 1) ISO datetime — a machine date the model or caller may pass directly.
    try:
        d = dt.datetime.fromisoformat(s.replace("z", "").strip())
        if d.year > 1900 and ("t" in s or " " in s or "-" in s):
            return d
    except ValueError:
        pass
    # 2) a relative duration ('10 minutes', 'in an hour', '1 hour 30 min') -> now + span.
    dur = _duration(s, now)
    if dur is not None:
        return dur
    # 3) a named calendar date ('July 1, 2026 at 7:05 am', 'June 20').
    named = _named_date(s, now)
    if named is not None:
        return named
    # 4) optional relative-day prefix -> a day offset + the remaining time phrase.
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
    # 5) a fuzzy daypart or explicit clock time.
    clk = _clock(s)
    if clk is None:
        return None
    hour, minute = clk
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + dt.timedelta(days=plus)
    if cand <= now:  # time already passed today and no explicit 'tomorrow' -> next day
        cand += dt.timedelta(days=1)
    return cand


def _fmt(d: dt.datetime) -> str:
    return d.strftime("%a %b %-d at %-I:%M %p")


def execute(action: str, text: str | None = None, when: str | None = None,
            id: int | None = None) -> str:
    if action == "create":
        # A bare "set a 10 minute timer" carries no label — default to "Timer" rather than
        # stopping to ask, so a hands-busy kitchen timer just works.
        label = (text or "").strip() or "Timer"
        due = _parse_when(when or "")
        if not due:
            return (f"I couldn't read the time '{when}'. Try a duration like '10 minutes', a "
                    f"time like '7:00', or a full date and time.")
        rid = store.add(due.isoformat(timespec="seconds"), label)
        return f"Reminder #{rid} set — I'll ping your phone {_fmt(due)}: {label}"
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
