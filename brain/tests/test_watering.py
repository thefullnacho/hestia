"""Watering logging — the one garden input that never left a trace.

Rain, soil moisture, heat and yield were all already captured; water was not, so nothing
could be put against anything. These pin the parts that make the record honest: a zone
number is not a place, a duration always survives, and a depth is only ever reported when
an application rate is actually known.
"""
from __future__ import annotations

import pytest

import records_store


# ----- applied depth and volume -----------------------------------------------

def test_a_quarter_hour_of_the_hi_rise():
    # 1.6 in/hr for 15 minutes over a 17 ft circle.
    assert records_store.water_applied(900, "hi-rise") == (0.4, 56.6)


def test_an_unknown_rate_invents_nothing():
    assert records_store.water_applied(900) == (None, None)
    assert records_store.water_applied(900, "soaker hose") == (None, None)
    assert records_store.water_applied(0, "hi-rise") == (None, None)


def test_a_known_rate_without_coverage_still_gives_depth():
    inches, gallons = records_store.water_applied(1800, rate_in_hr=1.6)
    assert inches == 0.8 and gallons is None


def test_a_measured_rate_overrides_the_spec_sheet():
    assert records_store.water_applied(900, "hi-rise", rate_in_hr=2.4)[0] == 0.6


# ----- the event ---------------------------------------------------------------

def test_a_sprinkler_run_records_depth_volume_and_its_basis(db):
    db.log_watering("Back Fence", 900, source="zone3", sprinkler="hi-rise")
    event = db.recent_events(kind="watering")[0]
    assert event["attrs"]["inches"] == 0.4
    assert event["attrs"]["gallons"] == 56.6
    assert event["attrs"]["basis"] == "spec"
    assert event["attrs"]["source"] == "zone3"
    assert "15 min" in event["detail"]


def test_a_drip_bed_logs_its_minutes_and_claims_no_depth(db):
    db.log_watering("Raised Bed 2", 1200, source="zone4")
    attrs = db.recent_events(kind="watering")[0]["attrs"]
    assert attrs["seconds"] == 1200
    assert attrs["inches"] is None and attrs["gallons"] is None
    # No rate means no basis to claim, rather than a spec that was never applied.
    assert attrs["basis"] is None


def test_a_catch_cup_reading_is_marked_as_measured(db):
    db.log_watering("Back Fence", 900, sprinkler="hi-rise", rate_in_hr=2.1, basis="measured")
    attrs = db.recent_events(kind="watering")[0]["attrs"]
    assert attrs["basis"] == "measured" and attrs["inches"] == 0.525


@pytest.mark.parametrize("seconds", [0, -60])
def test_a_run_with_no_duration_is_refused(db, seconds):
    with pytest.raises(ValueError):
        db.log_watering("Back Fence", seconds)


def test_water_and_yield_land_on_the_same_bed(db):
    db.log_harvest("Raised Bed 2", "beans", 3, "lb")
    db.log_watering("Raised Bed 2", 900, source="zone4")
    profile = db.entity_profile("Raised Bed 2")
    kinds = {e["kind"] for e in profile["recent"]}
    assert {"harvest", "watering"} <= kinds


def test_the_same_spot_watered_twice_is_one_place(db):
    first = db.log_watering("Back Fence", 900, sprinkler="hi-rise")
    second = db.log_watering("back fence", 900, sprinkler="hi-rise")
    assert first["created"] is True and second["created"] is False


# ----- season totals -----------------------------------------------------------

def test_totals_group_by_place_wettest_first(db):
    db.log_watering("Back Fence", 900, source="zone3", sprinkler="hi-rise")
    db.log_watering("Back Fence", 900, source="zone3", sprinkler="hi-rise")
    db.log_watering("Side Yard", 900, source="spigot", sprinkler="hi-rise")
    totals = db.water_totals()
    assert [t["place"] for t in totals] == ["Back Fence", "Side Yard"]
    assert totals[0]["runs"] == 2
    assert totals[0]["minutes"] == 30.0
    assert totals[0]["inches"] == 0.8
    assert totals[0]["gallons"] == 113.2
    assert totals[0]["sources"] == ["zone3"]


def test_an_unmeasured_bed_keeps_its_minutes_and_says_so(db):
    db.log_watering("Raised Bed 2", 1200, source="zone4")
    total = db.water_totals(place="Raised Bed 2")[0]
    assert total["runs"] == 1 and total["unmeasured"] == 1
    assert total["minutes"] == 20.0 and total["gallons"] == 0.0


def test_totals_are_scoped_to_one_season(db):
    db.log_watering("Back Fence", 900, sprinkler="hi-rise", ts="2025-07-04T06:00:00")
    db.log_watering("Back Fence", 900, sprinkler="hi-rise")
    assert len(db.water_totals(year=2025)) == 1
    assert db.water_totals(year=2025)[0]["runs"] == 1
