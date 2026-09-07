"""Calendar tool: range arithmetic, all-day vs timed creation, and the briefing section,
with HA stubbed by a recorder. HA is the single source of truth; the tool only computes
the range or the start/end it files, and never lets the model do that math."""
from __future__ import annotations

import datetime as dt

import briefing
import tools.calendar as calendar

NOW = dt.datetime(2026, 9, 7, 12, 0)  # Mon Sep 7 2026, noon
ENT = "calendar.dna"

RAW = [
    {"start": "2026-09-08", "end": "2026-09-09", "summary": "Pop's Birthday", "description": ""},
    {"start": "2026-09-08T08:00:00-04:00", "end": "2026-09-08T20:00:00-04:00",
     "summary": "Take Out the Trash", "description": "", "location": "Home"},
    {"start": "2026-09-09T08:00:00-04:00", "end": "2026-09-09T12:00:00-04:00",
     "summary": "Fridge Repair", "description": ""},
    {"start": "2026-09-14T09:30:00-04:00", "end": "2026-09-14T10:00:00-04:00",
     "summary": "Lyla Swim", "description": ""},
]


class FakeHA:
    """Answers get_events from a fixed list (filtered by the requested range, like HA does)
    and records create_event bodies."""

    def __init__(self, raw=None):
        self.raw = list(RAW if raw is None else raw)
        self.created = []
        self.ranges = []

    def get_events(self, entity, start, end):
        self.ranges.append((start, end))
        out = []
        for e in self.raw:
            s = dt.datetime.fromisoformat(e["start"])
            s = s.astimezone().replace(tzinfo=None) if s.tzinfo else s
            en = dt.datetime.fromisoformat(e["end"])
            en = en.astimezone().replace(tzinfo=None) if en.tzinfo else en
            if s < end and en > start:
                out.append(e)
        return out

    def create(self, body):
        self.created.append(body)
        title = body["summary"]
        if "start_date" in body:
            self.raw.append({"start": body["start_date"], "end": body["end_date"], "summary": title})
        else:
            self.raw.append({"start": body["start_date_time"], "end": body["end_date_time"], "summary": title})


def _ha(monkeypatch, raw=None):
    ha = FakeHA(raw)
    monkeypatch.setattr(calendar, "_get_events", ha.get_events)
    monkeypatch.setattr(calendar, "_create", ha.create)
    monkeypatch.setattr(calendar, "entities", lambda: [ENT])
    monkeypatch.setattr(calendar.dt, "datetime", _FrozenNow)
    return ha


class _FrozenNow(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


# ---------- ranges (pure) ----------

def test_ranges_are_computed_by_the_tool():
    assert calendar._range(None, NOW)[2] == "the next 7 days"
    s, e, label = calendar._range("today", NOW)
    assert (s, e, label) == (dt.datetime(2026, 9, 7), dt.datetime(2026, 9, 8), "today")
    s, e, _ = calendar._range("this week", NOW)          # Mon -> next Mon
    assert (s, e) == (dt.datetime(2026, 9, 7), dt.datetime(2026, 9, 14))
    s, e, _ = calendar._range("next week", NOW)
    assert (s, e) == (dt.datetime(2026, 9, 14), dt.datetime(2026, 9, 21))
    s, e, _ = calendar._range("this weekend", NOW)
    assert (s, e) == (dt.datetime(2026, 9, 12), dt.datetime(2026, 9, 14))
    s, e, label = calendar._range("on saturday", NOW)    # weekday via the shared parser
    assert (s, e) == (dt.datetime(2026, 9, 12), dt.datetime(2026, 9, 13))
    s, e, _ = calendar._range("September 14", NOW)
    assert (s, e) == (dt.datetime(2026, 9, 14), dt.datetime(2026, 9, 15))
    assert calendar._range("whenever", NOW) is None


def test_weekend_on_a_sunday_is_still_this_weekend():
    sunday = dt.datetime(2026, 9, 13, 10, 0)
    s, e, _ = calendar._range("weekend", sunday)
    assert (s, e) == (dt.datetime(2026, 9, 12), dt.datetime(2026, 9, 14))


# ---------- show ----------

def test_show_groups_by_day_all_day_first(monkeypatch):
    _ha(monkeypatch)
    out = calendar.execute("show", when="this week")
    lines = out.splitlines()
    assert lines[0] == "Calendar for the rest of this week (3 events):"
    assert lines[1] == "Tue Sep 8: Pop's Birthday (all day); Take Out the Trash 8am-8pm at Home"
    assert lines[2] == "Wed Sep 9: Fridge Repair 8am-12pm"
    assert "Lyla" not in out  # Sep 14 is next week


def test_show_empty_day(monkeypatch):
    _ha(monkeypatch)
    assert calendar.execute("show", when="today") == "Nothing on the calendar for today."


def test_show_unreadable_range_asks_instead_of_guessing(monkeypatch):
    ha = _ha(monkeypatch)
    out = calendar.execute("show", when="whenever")
    assert out.startswith("I couldn't read 'whenever'")
    assert ha.ranges == []


# ---------- add ----------

def test_add_timed_event_defaults_to_an_hour(monkeypatch):
    ha = _ha(monkeypatch)
    out = calendar.execute("add", title="Vet for Lily", when="Tuesday at 2pm")
    body = ha.created[0]
    assert body["entity_id"] == ENT and body["summary"] == "Vet for Lily"
    assert body["start_date_time"].startswith("2026-09-08T14:00:00")
    assert body["end_date_time"].startswith("2026-09-08T15:00:00")
    assert "start_date" not in body
    assert out.startswith("Added to the calendar: Vet for Lily, Tue Sep 8 at 2pm (60 min).")
    assert "Also that day: Pop's Birthday (all day); Take Out the Trash" in out


def test_add_without_a_clock_time_is_all_day(monkeypatch):
    ha = _ha(monkeypatch)
    out = calendar.execute("add", title="Dentist", when="September 14", notes="bring the card")
    body = ha.created[0]
    assert body["start_date"] == "2026-09-14" and body["end_date"] == "2026-09-15"
    assert body["description"] == "bring the card"
    assert "start_date_time" not in body
    assert out.startswith("Added to the calendar: Dentist, Mon Sep 14 (all day).")


def test_add_duration_is_honoured(monkeypatch):
    ha = _ha(monkeypatch)
    calendar.execute("add", title="Swim", when="fri 10am", duration_minutes=30)
    body = ha.created[0]
    assert body["start_date_time"].startswith("2026-09-11T10:00:00")
    assert body["end_date_time"].startswith("2026-09-11T10:30:00")


def test_add_unreadable_date_files_nothing(monkeypatch):
    ha = _ha(monkeypatch)
    out = calendar.execute("add", title="Vet", when="sometime soon")
    assert out.startswith("I couldn't read the date")
    assert ha.created == []


def test_add_needs_title_and_when(monkeypatch):
    ha = _ha(monkeypatch)
    assert calendar.execute("add", when="tomorrow").startswith("Error: 'add' needs a title")
    assert calendar.execute("add", title="Vet").startswith("Error: 'add' needs 'when'")
    assert ha.created == []


def test_add_with_no_calendar_in_ha_is_an_error(monkeypatch):
    ha = _ha(monkeypatch)
    monkeypatch.setattr(calendar, "entities", lambda: [])
    assert "no calendar" in calendar.execute("add", title="Vet", when="tomorrow")
    assert ha.created == []


def test_discovery_finds_every_calendar_entity(monkeypatch):
    monkeypatch.setattr(calendar, "ENTITIES", [])
    monkeypatch.setattr(calendar, "_discovered", (0.0, []))
    monkeypatch.setattr(calendar, "_states", lambda: [
        {"entity_id": "light.kitchen"}, {"entity_id": "calendar.dna"}, {"entity_id": "calendar.abc"}])
    assert calendar.entities() == ["calendar.abc", "calendar.dna"]


# ---------- briefing ----------

def test_briefing_lists_today_and_tomorrow(monkeypatch):
    _ha(monkeypatch)
    facts = briefing._calendar_facts(dt.datetime(2026, 9, 8, 7, 10))
    assert facts == [
        "On the calendar today: Pop's Birthday (all day).",
        "On the calendar today at 8:00 am: Take Out the Trash at Home.",
        "On the calendar tomorrow at 8:00 am: Fridge Repair.",
    ]


def test_briefing_quiet_day_adds_nothing(monkeypatch):
    _ha(monkeypatch)
    assert briefing._calendar_facts(dt.datetime(2026, 9, 20, 7, 10)) == []
