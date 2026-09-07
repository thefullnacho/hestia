"""`calendar` tool, the household calendar, backed by Home Assistant's Local Calendar.

"Put the vet on the calendar for Tuesday at 2" files an event in HA, so the same calendar
shows in the HA app on every phone and survives brain restarts. The model never holds the
calendar and never does date math: the when-phrase is resolved by the reminder tool's
parser, recurrence lives in HA's .ics store, and "what's on this week" is a range the tool
computes. Edits and deletions happen in the HA app: HA exposes no delete service, and a
calendar the model could silently rewrite is worse than one it can only append to.

Which calendars: HESTIA_CALENDAR_ENTITIES (comma-separated entity ids) if set, otherwise
every `calendar.*` entity HA reports. New events go on the first one.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time

import httpx

from tools.reminder import parse_when

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
ENTITIES = [e.strip() for e in os.environ.get("HESTIA_CALENDAR_ENTITIES", "").split(",") if e.strip()]
_HDRS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
DEFAULT_MINUTES = 60
DISCOVERY_TTL = 600  # seconds; calendars are added about once a year

SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar",
        "description": ("The household calendar (shared, lives in Home Assistant). 'show' reads "
                        "what is scheduled: pass 'when' as the user said it ('today', 'tomorrow', "
                        "'this week', 'next week', 'this weekend', 'Saturday', 'September 14'), or "
                        "omit it for the next 7 days. 'add' puts an event on it: 'title' is what "
                        "it is and 'when' is the user's time phrase VERBATIM ('Tuesday at 2pm', "
                        "'tomorrow morning', 'Sept 14', 'next Saturday 10am'). The tool works out "
                        "the date itself; do NOT calculate or reformat it. A phrase with no clock "
                        "time makes an all-day event. Use this for appointments, birthdays, "
                        "visits, repairs, anything that happens on a date; use 'reminder' instead "
                        "when the user wants a phone ping at a time. Changing or removing an "
                        "event is done in the Home Assistant app, not here."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["show", "add"]},
                "when": {"type": "string",
                         "description": "for show: the range as the user said it ('today', 'this week', 'Saturday'); omit for the next 7 days. For add: the user's date/time phrase verbatim ('Tuesday at 2pm', 'Sept 14'). Never reformat it."},
                "title": {"type": "string", "description": "for add: what the event is, e.g. 'Vet for Lily'"},
                "notes": {"type": "string", "description": "for add: optional details, e.g. an address or what to bring"},
                "duration_minutes": {"type": "integer", "description": "for add: how long a timed event lasts; default 60"},
            },
            "required": ["action"],
        },
    },
}

# A phrase that names a clock time (or a duration from now) makes a timed event; anything
# else ('Sept 14', 'Saturday', 'tomorrow') is all-day.
_CLOCK_RE = re.compile(
    r"\d{1,2}:\d{2}|\d{1,2}\s*[ap]\.?m\b|\bat\s+\d|\b(?:morning|noon|midday|afternoon|evening|night|tonight)\b"
    r"|\d+\s*(?:hours?|hrs?|hr|h|minutes?|mins?|min|m)\b|\ban?\s+(?:hour|minute)\b|t\d{2}:", re.I)

_discovered: tuple[float, list[str]] = (0.0, [])


# ---------- HA seams (stubbed in tests) ----------

def _states() -> list[dict]:
    r = httpx.get(f"{HA_URL}/api/states", headers=_HDRS, timeout=10)
    r.raise_for_status()
    return r.json()


def _get_events(entity: str, start: dt.datetime, end: dt.datetime) -> list[dict]:
    body = {"entity_id": entity,
            "start_date_time": start.astimezone().isoformat(timespec="seconds"),
            "end_date_time": end.astimezone().isoformat(timespec="seconds")}
    r = httpx.post(f"{HA_URL}/api/services/calendar/get_events?return_response",
                   headers=_HDRS, json=body, timeout=10)
    r.raise_for_status()
    return (r.json().get("service_response", {}).get(entity, {}) or {}).get("events", [])


def _create(body: dict) -> None:
    r = httpx.post(f"{HA_URL}/api/services/calendar/create_event", headers=_HDRS,
                   json=body, timeout=10)
    r.raise_for_status()


# ---------- calendars ----------

def entities() -> list[str]:
    """Configured calendars, else every calendar HA reports (cached for DISCOVERY_TTL)."""
    global _discovered
    if ENTITIES:
        return list(ENTITIES)
    stamp, found = _discovered
    if found and time.monotonic() - stamp < DISCOVERY_TTL:
        return list(found)
    found = sorted(s["entity_id"] for s in _states() if s.get("entity_id", "").startswith("calendar."))
    _discovered = (time.monotonic(), found)
    return list(found)


# ---------- events ----------

def _local(stamp: str) -> dt.datetime:
    """HA's ISO stamp (with offset) -> naive local wall time."""
    d = dt.datetime.fromisoformat(stamp)
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def _normalize(raw: dict, entity: str) -> dict:
    all_day = len(raw.get("start", "")) == 10
    if all_day:
        start = dt.datetime.fromisoformat(raw["start"])
        end = dt.datetime.fromisoformat(raw["end"])
    else:
        start, end = _local(raw["start"]), _local(raw["end"])
    return {"summary": raw.get("summary") or "(untitled)", "start": start, "end": end,
            "all_day": all_day, "location": raw.get("location") or "",
            "description": raw.get("description") or "", "calendar": entity}


def events(start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Every event across the household calendars overlapping [start, end), sorted by
    day, all-day first, then start time. Naive local datetimes in and out."""
    out = []
    for ent in entities():
        out.extend(_normalize(e, ent) for e in _get_events(ent, start, end))
    out.sort(key=lambda e: (e["start"].date(), not e["all_day"], e["start"]))
    return out


# ---------- ranges ----------

def _day(d: dt.date) -> tuple[dt.datetime, dt.datetime]:
    s = dt.datetime.combine(d, dt.time())
    return s, s + dt.timedelta(days=1)


def _range(when: str | None, now: dt.datetime) -> tuple[dt.datetime, dt.datetime, str] | None:
    """A range phrase -> (start, end, label). Only the tool does this arithmetic."""
    s = (when or "").strip().lower()
    s = re.sub(r"^(?:for|on|in)\s+", "", s)
    today = now.date()
    if s in ("", "upcoming", "coming up", "soon", "next 7 days", "next few days", "week ahead"):
        return now, now + dt.timedelta(days=7), "the next 7 days"
    if s == "today":
        return (*_day(today), "today")
    if s in ("tomorrow", "tmrw"):
        return (*_day(today + dt.timedelta(days=1)), "tomorrow")
    if s in ("this week", "week", "the week", "rest of the week"):
        monday_next = today + dt.timedelta(days=7 - today.weekday())
        return dt.datetime.combine(today, dt.time()), dt.datetime.combine(monday_next, dt.time()), "the rest of this week"
    if s == "next week":
        monday_next = today + dt.timedelta(days=7 - today.weekday())
        return dt.datetime.combine(monday_next, dt.time()), dt.datetime.combine(monday_next + dt.timedelta(days=7), dt.time()), "next week"
    if s in ("weekend", "this weekend", "the weekend"):
        sat = today + dt.timedelta(days=(5 - today.weekday()) % 7)
        if today.weekday() == 6:
            sat = today - dt.timedelta(days=1)
        return dt.datetime.combine(sat, dt.time()), dt.datetime.combine(sat + dt.timedelta(days=2), dt.time()), "this weekend"
    if s in ("this month", "month", "the month"):
        first_next = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        return dt.datetime.combine(today, dt.time()), dt.datetime.combine(first_next, dt.time()), "the rest of this month"
    d = parse_when(s, now)
    if d is None:
        return None
    return (*_day(d.date()), d.strftime("%A %b %-d"))


# ---------- formatting ----------

def _clock(d: dt.datetime) -> str:
    return d.strftime("%-I:%M%p").lower().replace(":00", "")


def _line(e: dt.date, ev: dict) -> str:
    if ev["all_day"]:
        span = ev["end"].date() - ev["start"].date()
        tail = " (all day)" if span <= dt.timedelta(days=1) else f" (all day, through {(ev['end'] - dt.timedelta(days=1)).strftime('%a %b %-d')})"
        text = ev["summary"] + tail
    else:
        text = f"{ev['summary']} {_clock(ev['start'])}-{_clock(ev['end'])}"
    if ev["location"]:
        text += f" at {ev['location']}"
    return text


def format_events(evs: list[dict], label: str) -> str:
    if not evs:
        return f"Nothing on the calendar for {label}."
    days: dict[dt.date, list[dict]] = {}
    for ev in evs:
        days.setdefault(ev["start"].date(), []).append(ev)
    lines = [f"Calendar for {label} ({len(evs)} event{'s' if len(evs) != 1 else ''}):"]
    for day in sorted(days):
        lines.append(f"{day.strftime('%a %b %-d')}: " + "; ".join(_line(day, ev) for ev in days[day]))
    return "\n".join(lines)


# ---------- tool ----------

def execute(action: str, when: str | None = None, title: str | None = None,
            notes: str | None = None, duration_minutes: int | None = None) -> str:
    now = dt.datetime.now()
    try:
        if action == "show":
            rng = _range(when, now)
            if rng is None:
                return (f"I couldn't read '{when}' as a day or range. Try 'today', 'this week', "
                        f"'Saturday', or a date like 'September 14'.")
            start, end, label = rng
            return format_events(events(start, end), label)

        if action == "add":
            title = (title or "").strip()
            if not title:
                return "Error: 'add' needs a title (what the event is)."
            if not (when or "").strip():
                return "Error: 'add' needs 'when' (the user's date phrase, verbatim)."
            cals = entities()
            if not cals:
                return "Error: Home Assistant reports no calendar. Add the Local Calendar integration first."
            start = parse_when(when, now)
            if start is None:
                return (f"I couldn't read the date '{when}'. Try 'Tuesday at 2pm', 'tomorrow "
                        f"morning', or a date like 'September 14'.")
            body = {"entity_id": cals[0], "summary": title}
            if notes:
                body["description"] = notes.strip()
            if _CLOCK_RE.search(when):
                minutes = duration_minutes if duration_minutes and duration_minutes > 0 else DEFAULT_MINUTES
                end = start + dt.timedelta(minutes=minutes)
                body["start_date_time"] = start.astimezone().isoformat(timespec="seconds")
                body["end_date_time"] = end.astimezone().isoformat(timespec="seconds")
                stamp = f"{start.strftime('%a %b %-d')} at {_clock(start)} ({minutes} min)"
            else:
                body["start_date"] = start.date().isoformat()
                body["end_date"] = (start.date() + dt.timedelta(days=1)).isoformat()
                stamp = f"{start.strftime('%a %b %-d')} (all day)"
            _create(body)
            day_start, day_end = _day(start.date())
            others = [e for e in events(day_start, day_end) if e["summary"] != title]
            msg = f"Added to the calendar: {title}, {stamp}."
            if others:
                msg += " Also that day: " + "; ".join(_line(start.date(), e) for e in others) + "."
            return msg

        return f"Error: unknown action '{action}' (use show or add)."
    except httpx.HTTPError as e:
        return f"Calendar backend error (Home Assistant): {e}"
