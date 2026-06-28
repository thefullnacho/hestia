"""Memory store — keyword recall scoring, id de-duplication, and the type whitelist."""
from __future__ import annotations


def test_write_then_recall_by_keyword(mem):
    mem.write("I like the porch light dim in the evening", type="preference")
    hits = mem.recall("porch light")
    assert hits and "porch light" in hits[0]["body"]


def test_recall_ranks_more_overlap_first(mem):
    mem.write("trash goes out on Tuesday night", type="routine")
    mem.write("the porch light should be dim", type="preference")
    hits = mem.recall("dim porch light")
    assert "porch light" in hits[0]["body"]


def test_recall_empty_for_no_overlap(mem):
    mem.write("trash goes out Tuesday", type="routine")
    assert mem.recall("quantum chromodynamics") == []


def test_duplicate_content_gets_distinct_ids(mem):
    a = mem.write("trash goes out Tuesday", type="routine")
    b = mem.write("trash goes out Tuesday", type="routine")
    assert a != b
    assert (mem.MEMORY_DIR / f"{a}.md").exists()
    assert (mem.MEMORY_DIR / f"{b}.md").exists()


def test_unknown_type_coerces_to_preference(mem):
    # documents current behavior: an out-of-whitelist type falls back rather than erroring
    rid = mem.write("some fact", type="not_a_real_type")
    record = next(r for r in mem._all() if r["id"] == rid)
    assert record["meta"]["type"] == "preference"


def test_pinned_breaks_ties_toward_pinned(mem):
    mem.write("blue car", type="reference", pinned=False)
    mem.write("blue car", type="reference", pinned=True)
    top = mem.recall("blue car")[0]
    assert top["meta"]["pinned"] is True
