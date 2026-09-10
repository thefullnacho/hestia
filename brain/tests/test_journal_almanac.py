"""Journal + almanac: the deterministic parts (fact gathering, entry storage, page
render, year-over-year). Narration is the model's job and isn't tested here."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import almanac
import journal
import pest_watch

DAY = dt.date(2026, 7, 1)


@pytest.fixture
def season(db, tmp_path, monkeypatch):
    """A tiny season: one planting, one sighting, one journal entry, live pest state."""
    db.log_event("note", subject="Tomatoes", action="planted", detail="6 starts in",
                 ts="2026-05-11T10:00:00")
    db.log_event("sighting", subject="Chickadee", action="observed", detail="5 on patrol",
                 ts="2026-07-01T08:00:00")
    db.log_event("note", subject="Journal", action="journal", detail="A fine day.",
                 ts="2026-06-30T23:59:00")
    state = tmp_path / "pest_state.json"
    state.write_text(json.dumps({"season": "2026", "biofix": "2026-04-21",
                                 "cumulative_gdd": 909.0, "alerted": {"tomato:hornworm": "2026"}}))
    monkeypatch.setattr(pest_watch, "STATE_PATH", state)
    zones = tmp_path / "frost-zones.json"
    zones.write_text(json.dumps({"zones": {"7a": {
        "lastSpringFrost": "03-05", "firstFallFrost": "11-20", "frostFreeDays": 260,
        "lastFrostVarianceDays": 10, "firstFrostVarianceDays": 10}}}))
    monkeypatch.setattr(almanac, "FROST_ZONES", zones)
    monkeypatch.setattr(almanac, "ALMANAC_DIR", tmp_path / "almanac")
    monkeypatch.setattr(almanac.weather, "history_days",
                        lambda s, e: [{"date": "2026-04-21", "hi": 50, "lo": 30, "rain": 0.0}])
    return db


def test_journal_events_exclude_prior_entries(season):
    facts = journal._events_today(DAY)
    assert any("Chickadee" in f for f in facts)
    assert not any("A fine day" in f for f in facts)   # yesterday's journal is not a fact


def test_journal_write_entry_is_canonical_in_records(season, tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journal")
    journal.write_entry(DAY, "Quiet day; beets pulled.", ["note: Beets — harvested"])
    md = (tmp_path / "journal" / "2026-07-01.md").read_text()
    assert "Quiet day; beets pulled." in md and "Beets" in md
    evs = season.recent_events(subject="Journal", limit=5)
    assert evs[0]["detail"] == "Quiet day; beets pulled." and evs[0]["ts"].startswith("2026-07-01")
    assert journal.already_written(DAY)                      # nightly timer won't double-write
    assert not journal.already_written(DAY + dt.timedelta(days=1))


def test_almanac_page_sections(season):
    page = almanac.generate(2026, today=DAY)
    assert "Apr 21" in page and "47 days later" in page          # observed 32F vs normal
    assert "909 accumulated" in page
    assert "hornworm" in page
    assert "**May 11** — Tomatoes: planted" in page
    assert "Chickadee" in page
    assert "May 11" not in page.split("## Wildlife")[1]          # sightings only in Wildlife
    assert (almanac.ALMANAC_DIR / "2026.json").exists()


def test_almanac_year_over_year(season):
    almanac.ALMANAC_DIR.mkdir(parents=True, exist_ok=True)
    (almanac.ALMANAC_DIR / "2025.json").write_text(json.dumps({
        "year": 2025, "last_frost32": "2025-04-05", "gdd": 850,
        "species_seen": {"Chickadee": {"first": "2025-08-10", "sightings": 3}}}))
    page = almanac.generate(2026, today=DAY)
    assert "## Year over year" in page
    assert "last frost Apr 5 vs Apr 21" in page
    assert "returning species: Chickadee" in page


# ----- water: the season's other half of the story -------------------------------------

def _rain(monkeypatch, inches):
    monkeypatch.setattr(almanac.weather, "history_days",
                        lambda s, e: [{"date": "2026-04-21", "hi": 50, "lo": 30, "rain": inches}])


def test_water_section_reports_volume_and_rain_over_the_same_span(season, monkeypatch):
    _rain(monkeypatch, 2.5)
    season.log_watering("Strawberries", 900, source="zone3", sprinkler="hi-rise",
                        ts="2026-06-02T07:00:00")
    season.log_watering("Strawberries", 900, source="zone3", sprinkler="hi-rise",
                        ts="2026-06-20T07:00:00")
    page = almanac.render(almanac.snapshot(2026, DAY))
    assert "**1 places, 2 run(s), 113 gal applied** since Jun 2" in page
    assert "**2.5 in of rain** fell over the same span" in page


def test_rain_is_added_to_each_place_and_depths_never_sum(season, monkeypatch):
    _rain(monkeypatch, 2.0)
    season.log_watering("Strawberries", 900, source="zone3", sprinkler="hi-rise")
    season.log_watering("Peach", 900, source="zone3", sprinkler="hi-rise")
    page = almanac.render(almanac.snapshot(2026, DAY))
    # Each place got 0.4 in of its own plus the same 2 in of rain. Nowhere got 0.8.
    assert "Strawberries 0.4+2=2.4" in page and "Peach 0.4+2=2.4" in page
    assert "depths across places never sum" in page


def test_a_bed_with_no_known_rate_is_listed_not_dropped(season, monkeypatch):
    _rain(monkeypatch, 0.0)
    season.log_watering("Raised Bed 2", 1200, source="zone4")
    page = almanac.render(almanac.snapshot(2026, DAY))
    assert "Raised Bed 2 20min" in page
    assert "Applied, inches:" not in page   # nothing measured, so nothing claimed


def test_the_span_starts_at_the_earliest_run_not_the_wettest_place(season, monkeypatch):
    _rain(monkeypatch, 1.0)
    # The wettest place is watered later; the span must still open on the first run.
    season.log_watering("Peach", 900, sprinkler="hi-rise", ts="2026-05-02T07:00:00")
    season.log_watering("Meadow", 1800, sprinkler="hi-rise", ts="2026-06-15T07:00:00")
    snap = almanac.snapshot(2026, DAY)
    assert snap["water"][0]["place"] == "Meadow"      # sorted by volume
    assert snap["water_span"][0] == "2026-05-02"      # but the span is chronological


def test_a_dry_season_says_so_rather_than_showing_nothing(season, monkeypatch):
    _rain(monkeypatch, 0.0)
    page = almanac.render(almanac.snapshot(2026, DAY))
    assert "## Water" in page and "(no watering logged yet)" in page


def test_year_over_year_compares_water_and_rain(season, monkeypatch, tmp_path):
    _rain(monkeypatch, 3.0)
    prior = tmp_path / "almanac"
    prior.mkdir(exist_ok=True)
    (prior / "2025.json").write_text(json.dumps({
        "year": 2025, "rain_in": 6.0,
        "water": [{"place": "Strawberries", "runs": 4, "gallons": 226.4, "inches": 1.6,
                   "minutes": 60, "unmeasured": 0, "sources": ["zone3"],
                   "first": "2025-06-01T07:00:00", "last": "2025-08-01T07:00:00"}]}))
    season.log_watering("Strawberries", 900, source="zone3", sprinkler="hi-rise")
    page = almanac.render(almanac.snapshot(2026, DAY))
    assert "water 226 → 57 gal (-75%)" in page
    assert "rain 6 → 3 in" in page
