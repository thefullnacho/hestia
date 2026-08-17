"""Records tool — model-supplied numbers/strings are validated at the tool edge before
they reach the store. `limit=-1` is sqlite's 'no limit' (one bad arg dumps the whole
event log into context), a non-positive harvest qty would pollute permanent season
totals, and a malformed attrs string was silently dropped while the event logged
anyway. Store-level behavior is pinned in test_records_store.py; these are tool-level."""
from __future__ import annotations

import tools.records as records


def test_recent_limit_is_capped(db):
    """The tool advertises max 100; a huge model-supplied limit must not pull more."""
    for i in range(150):
        db.log_event("note", subject=f"thing-{i}")
    out = records.execute("recent", limit=10000)
    assert out.startswith("Recent records (100):")


def test_recent_limit_garbage_defaults(db):
    for i in range(25):
        db.log_event("note", subject=f"thing-{i}")
    out = records.execute("recent", limit="lots")
    assert out.startswith("Recent records (20):")


def test_harvest_rejects_nonpositive_qty(db):
    out = records.execute("harvest", bed="Bed 1", crop="Tomatoes", qty=-2)
    assert "positive" in out
    assert db.harvest_totals() == []


def test_harvest_rejects_zero_qty(db):
    out = records.execute("harvest", bed="Bed 1", crop="Tomatoes", qty=0)
    assert "positive" in out
    assert db.harvest_totals() == []


def test_malformed_attrs_refused_not_dropped(db):
    out = records.execute("log", kind="sighting", subject="deer", did="observed",
                          attrs="{not json")
    assert "Error" in out and "attrs" in out
    assert db.recent_events() == []


def test_non_object_attrs_refused(db):
    """A valid-JSON non-dict ('[1,2]') used to flow a list into the store as attrs."""
    out = records.execute("remember", name="Momo", kind="pet", attrs="[1, 2]")
    assert "Error" in out and "attrs" in out
    assert db.entity_profile("Momo") is None


def test_valid_attrs_still_recorded(db):
    out = records.execute("remember", name="Momo", kind="pet", attrs={"breed": "Lhasa Apso"})
    assert "Remembered Momo" in out
    assert db.entity_profile("Momo")["attrs"]["breed"] == "Lhasa Apso"
