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
