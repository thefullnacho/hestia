"""Reminder parsing — the date math lives in the tool (never the model), so this is where
it's pinned. Regression guard for the bug where a named calendar date ('July 1, 2026 at
7:05 am') returned None and the tool said "I couldn't read the time" no matter how it was
phrased. `_parse_when` takes an explicit `now`, so every case resolves deterministically."""
from __future__ import annotations

import datetime as dt

import pytest

import tools.reminder as reminder

# A fixed "now": Sun 2026-06-28 12:00, so "already passed today" and "no year, already
# passed this year" both have a stable answer.
NOW = dt.datetime(2026, 6, 28, 12, 0)


@pytest.mark.parametrize("phrase, expected", [
    # Named calendar dates — the reported failure and its variants.
    ("July 1, 2026 at 7:05 am", dt.datetime(2026, 7, 1, 7, 5)),
    ("July 1 2026 7:05am",      dt.datetime(2026, 7, 1, 7, 5)),
    ("1 July 2026 at 9pm",      dt.datetime(2026, 7, 1, 21, 0)),   # day-first
    ("Dec 25 at 8am",           dt.datetime(2026, 12, 25, 8, 0)),  # no year, still ahead
    ("June 20",                 dt.datetime(2027, 6, 20, 9, 0)),   # no year+time: rolls, 9am default
    # ISO datetimes a caller may pass directly.
    ("2026-07-01T07:05",        dt.datetime(2026, 7, 1, 7, 5)),
    ("2026-07-01 07:05",        dt.datetime(2026, 7, 1, 7, 5)),
    # Relative clock times -> next occurrence from NOW (12:00).
    ("9pm",                     dt.datetime(2026, 6, 28, 21, 0)),  # still ahead today
    ("7am",                     dt.datetime(2026, 6, 29, 7, 0)),   # passed -> tomorrow
    ("at 7:00",                 dt.datetime(2026, 6, 29, 7, 0)),
    ("noon",                    dt.datetime(2026, 6, 29, 12, 0)),  # == now -> tomorrow
    # Relative days + fuzzy dayparts.
    ("tomorrow at 7",           dt.datetime(2026, 6, 29, 7, 0)),
    ("tomorrow morning",        dt.datetime(2026, 6, 29, 9, 0)),
    ("tonight",                 dt.datetime(2026, 6, 28, 21, 0)),
])
def test_parse_when_resolves(phrase, expected):
    assert reminder._parse_when(phrase, NOW) == expected


@pytest.mark.parametrize("phrase", [
    "",            # empty
    "now",         # not a clock/date the tool resolves
    "in an hour",  # relative durations are unsupported
    "Feb 30 at 9am",  # impossible calendar date
    "someday",     # gibberish
    "at 99:00",    # out-of-range time
])
def test_parse_when_unreadable_returns_none(phrase):
    assert reminder._parse_when(phrase, NOW) is None


def test_create_files_a_row_and_lists_it(db):
    """End-to-end through execute(): a named-date create lands a row the list action shows."""
    out = reminder.execute("create", text="take out the trash", when="July 1, 2026 at 7:05 am")
    assert out.startswith("Reminder #")
    assert "Jul 1" in out and "7:05 AM" in out
    listed = reminder.execute("list")
    assert "take out the trash" in listed


def test_create_rejects_unreadable_time(db):
    out = reminder.execute("create", text="do a thing", when="someday")
    assert "couldn't read the time" in out
