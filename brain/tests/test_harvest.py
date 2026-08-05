"""Harvest logging — the yield track record the beds never had.

"harvested the snow peas today" used to land as a free-text note and the amount was lost.
These pin the three things that make it worth having: the quantity survives, mixed units
total honestly rather than being fudged into one number, and the shopping list knows.
"""
from __future__ import annotations

import datetime as dt

import pytest

import records_store
import tools


# ----- unit normalisation ----------------------------------------------------

@pytest.mark.parametrize("unit,cls,canon", [
    ("lb", "weight", "lb"), ("lbs", "weight", "lb"), ("pounds", "weight", "lb"),
    ("oz", "weight", "oz"), ("ounces", "weight", "oz"), ("kg", "weight", "kg"),
    (None, "count", "each"), ("", "count", "each"), ("each", "count", "each"),
    ("bunches", "count", "each"), ("pints", "volume", "pint"),
])
def test_units_normalise(unit, cls, canon):
    assert records_store.normalize_unit(unit) == (cls, canon)


def test_unknown_unit_keeps_its_own_class_rather_than_vanishing(db):
    cls, canon = records_store.normalize_unit("pecks")
    assert (cls, canon) == ("other", "pecks")


# ----- logging ---------------------------------------------------------------

def test_harvest_keeps_the_quantity(db):
    db.log_harvest("Bed 4", "Tomatoes", 4, "lb")
    ev = db.recent_events(kind="harvest")[0]
    assert ev["subject"] == "Bed 4"
    assert ev["attrs"]["crop"] == "Tomatoes"
    assert ev["attrs"]["qty"] == 4
    assert ev["attrs"]["unit"] == "lb"
    assert round(ev["attrs"]["grams"]) == 1814


def test_counts_carry_no_fabricated_weight(db):
    """A zucchini has no honest gram value — grams must stay None, not be guessed."""
    db.log_harvest("Bed 2", "Zucchini", 6)
    assert db.recent_events(kind="harvest")[0]["attrs"]["grams"] is None


def test_bed_is_the_subject_so_yields_roll_up_per_bed(db):
    db.upsert_entity("place", "Bed 4")
    db.log_harvest("Bed 4", "Tomatoes", 2, "lb")
    prof = db.entity_profile("Bed 4")
    assert any(e["kind"] == "harvest" for e in prof["recent"])


# ----- totals ----------------------------------------------------------------

def test_mixed_weight_units_total_together(db):
    db.log_harvest("Bed 4", "Tomatoes", 1, "lb")
    db.log_harvest("Bed 4", "Tomatoes", 16, "oz")     # another pound
    db.log_harvest("Bed 1", "Tomatoes", 453.592, "g")  # and another
    t = [r for r in db.harvest_totals() if r["crop"] == "Tomatoes"][0]
    assert t["lb"] == 3.0
    assert t["pickings"] == 3
    assert t["beds"] == ["Bed 1", "Bed 4"]


def test_weights_and_counts_stay_separate(db):
    """The honest answer is two totals, never one invented number."""
    db.log_harvest("Bed 2", "Cucumbers", 3, "lb")
    db.log_harvest("Bed 2", "Cucumbers", 5)
    rows = [r for r in db.harvest_totals() if r["crop"] == "Cucumbers"]
    assert len(rows) == 2
    by = {r["unit_class"]: r for r in rows}
    assert by["weight"]["lb"] == 3.0
    assert by["count"]["qty"] == 5
    assert by["count"]["lb"] is None


def test_totals_filter_by_crop_and_bed(db):
    db.log_harvest("Bed 4", "Tomatoes", 2, "lb")
    db.log_harvest("Bed 2", "Zucchini", 4)
    assert len(db.harvest_totals(crop="tomato")) == 1      # singular still matches
    assert len(db.harvest_totals(bed="Bed 2")) == 1


def test_totals_are_scoped_to_the_season(db):
    db.log_harvest("Bed 4", "Tomatoes", 9, "lb", ts="2025-08-01T10:00:00")
    db.log_harvest("Bed 4", "Tomatoes", 2, "lb")
    this_year = int(dt.date.today().year)
    assert [r["lb"] for r in db.harvest_totals(year=this_year)] == [2.0]
    assert [r["lb"] for r in db.harvest_totals(year=2025)] == [9.0]


# ----- the shopping cross-check ----------------------------------------------

def test_harvested_recently_matches_singular_and_plural(db):
    db.log_harvest("Bed 4", "Tomatoes", 4, "lb")
    assert db.harvested_recently("tomato")["total_lb"] == 4.0
    assert db.harvested_recently("Tomatoes")["crop"] == "Tomatoes"


def test_harvested_recently_ignores_old_pickings(db):
    old = (dt.datetime.now() - dt.timedelta(days=40)).isoformat(timespec="seconds")
    db.log_harvest("Bed 4", "Tomatoes", 4, "lb", ts=old)
    assert db.harvested_recently("tomatoes", days=10) is None


def test_harvested_recently_does_not_match_a_different_crop(db):
    db.log_harvest("Bed 3", "Potatoes", 5, "lb")
    assert db.harvested_recently("tomatoes") is None
    assert db.harvested_recently("eggs") is None


def test_short_queries_never_match(db):
    """Two-letter items would match nearly anything — refuse rather than misfire."""
    db.log_harvest("Bed 3", "Potatoes", 5, "lb")
    assert db.harvested_recently("po") is None


# ----- through the tool ------------------------------------------------------

def test_tool_logs_and_reports_the_running_season_total(db):
    tools.dispatch("records", {"action": "harvest", "bed": "Bed 4",
                               "crop": "Tomatoes", "qty": 4, "unit": "lb"})
    out = tools.dispatch("records", {"action": "harvest", "bed": "Bed 4",
                                     "crop": "Tomatoes", "qty": 2, "unit": "lb"})
    assert "Logged 2 lb of Tomatoes from Bed 4" in out
    assert "6.0 lb" in out and "2 picking(s)" in out


def test_tool_warns_when_the_bed_is_new(db):
    """A brand-new bed on a harvest is nearly always a mishear, not a new bed."""
    db.upsert_entity("place", "Bed 4")
    known = tools.dispatch("records", {"action": "harvest", "bed": "Bed 4",
                                       "crop": "Tomatoes", "qty": 1, "unit": "lb"})
    assert "⚠" not in known
    new = tools.dispatch("records", {"action": "harvest", "bed": "Bed Fortyfour",
                                     "crop": "Tomatoes", "qty": 1, "unit": "lb"})
    assert "⚠" in new and "wasn't a known bed" in new


def test_tool_requires_the_amount(db):
    out = tools.dispatch("records", {"action": "harvest", "bed": "Bed 4", "crop": "Tomatoes"})
    assert out.startswith("Error:") and "quantity" in out


def test_yield_action_reads_back_the_season(db):
    db.log_harvest("Bed 4", "Tomatoes", 4, "lb")
    db.log_harvest("Bed 2", "Zucchini", 6)
    out = tools.dispatch("records", {"action": "yield"})
    assert "Tomatoes: 4.0 lb" in out and "Zucchini: 6" in out


def test_yield_is_honest_when_nothing_is_logged(db):
    assert "No harvests logged" in tools.dispatch("records", {"action": "yield"})


# ----- the shopping list knows -----------------------------------------------
# _recently_grown is tested directly: it's the pure half, so these need no Home Assistant.

def test_shopping_flags_produce_you_already_grew(db):
    from tools import shopping
    db.log_harvest("Bed 4", "Tomatoes", 4, "lb")
    note = shopping._recently_grown(["tomatoes", "milk"])
    assert "tomatoes" in note and "4.0 lb" in note and "Bed 4" in note
    assert "milk" not in note          # only what we actually grew


def test_shopping_says_nothing_when_nothing_was_grown(db):
    from tools import shopping
    assert shopping._recently_grown(["milk", "bread"]) == ""


def test_shopping_check_phrases_recency_naturally(db):
    from tools import shopping
    db.log_harvest("Bed 2", "Zucchini", 3)
    assert "today" in shopping._recently_grown(["zucchini"])
    y = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    db.log_harvest("Bed 2", "Cucumbers", 2, ts=y)
    assert "yesterday" in shopping._recently_grown(["cucumbers"])


def test_shopping_check_never_breaks_the_add(db, monkeypatch):
    """A records hiccup must degrade to silence, never block putting milk on the list."""
    from tools import shopping
    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(records_store, "harvested_recently", boom)
    assert shopping._recently_grown(["tomatoes"]) == ""
