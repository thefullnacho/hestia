"""The library tool: passages by meaning, keyword when the embedder is down, citations and the
medication referral written by code."""
from __future__ import annotations

import asyncio
import re

import pytest

import backend  # noqa: E402
import hestia
import library_store as lib
from tools import library

WORDS = ["blueberry", "prune", "garlic", "plant", "insulin", "cold", "power", "tick"]


def fake_embed(texts):
    """A bag-of-words vector over a tiny vocabulary: enough to rank by shared meaning words."""
    out = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in WORDS] + [0.01]
        n = sum(x * x for x in v) ** 0.5
        out.append([x / n for x in v])
    return out


@pytest.fixture
def shelf(db, monkeypatch):
    monkeypatch.setattr(lib, "embed", fake_embed)
    lib._vectors.clear()
    lib.index_doc("blue.pdf", ["Prune blueberry bushes in late winter. " * 5], "Blueberry Pruning Guide | Ext")
    lib.index_doc("garlic.pdf", ["Plant garlic cloves in October. " * 5], "Growing Garlic | Ext")
    lib.index_doc("prep.pdf", ["Keep insulin cold when the power is out; plan a supply. " * 5],
                  "Disaster Preparedness | Ext")
    lib.embed_missing()
    return lib


def test_vector_search_ranks_by_meaning(shelf):
    hits = lib.search("when should I prune my blueberry bushes", 3)
    assert hits[0]["file"] == "blue.pdf" and hits[0]["mode"] == "vector" and hits[0]["score"] > 0.5


def test_embedder_down_falls_back_to_keyword(shelf, monkeypatch):
    def down(texts):
        raise ConnectionError("ollama restarting")
    monkeypatch.setattr(lib, "embed", down)
    hits = lib.search("garlic cloves", 3)
    assert hits[0]["file"] == "garlic.pdf" and hits[0]["mode"] == "keyword"
    assert "Keyword search" in library.execute("garlic cloves")


def test_partial_vectors_are_never_used(shelf):
    lib.index_doc("new.pdf", ["Ticks on dogs. " * 5], "Tick Control | Ext")   # no vector yet
    assert lib._load_vectors() == {}
    assert lib.search("tick", 2)[0]["mode"] == "keyword"


def test_tool_cites_every_passage_and_adds_the_medication_rule(shelf):
    out = library.execute("how do I keep insulin cold in a power outage")
    assert re.search(r"^\[1\] Disaster Preparedness, p\.1$", out, re.M)
    assert library.MED_NOTE in out
    assert library.MED_NOTE not in library.execute("when do I plant garlic")


def test_empty_library_says_so(db):
    lib._vectors.clear()
    assert library.execute("how to grow figs").startswith("Nothing in the household library")


def test_addendum_adds_the_source_only_when_the_answer_names_none():
    trace = hestia.TurnTrace(citations=["Blueberry Pruning Guide, 2026, p.2", "Growing Garlic, p.1"])
    assert hestia._library_addendum("Prune in late winter.", trace, "how to prune") == \
        "Source: Blueberry Pruning Guide, 2026, p.2."
    assert hestia._library_addendum("The Blueberry Pruning Guide says late winter.", trace, "how") == ""
    assert hestia._library_addendum("Late winter.", hestia.TurnTrace(), "how to prune") == ""
    assert hestia._library_addendum("Sorry, the library doesn't contain information on that.",
                                    trace, "raspberry varieties?") == ""


def test_dose_questions_always_get_the_pharmacist_line():
    trace = hestia.TurnTrace(citations=["Disaster Preparedness, 2026, p.2"])
    out = hestia._library_addendum("Disaster Preparedness says keep a week's supply.", trace,
                                   "How much insulin should she take if we're rationing?")
    assert out == hestia.MED_REFERRAL
    assert hestia._library_addendum("Disaster Preparedness covers supply; ask her doctor.", trace,
                                   "Can she skip a dose of her meds?") == ""
    assert hestia.MED_REFERRAL not in hestia._library_addendum("Keep it cold.", trace,
                                                               "How do I store her medication?")


def test_library_routing_words():
    pattern = hestia._INTENT_TOOLS["library"]
    assert re.search(pattern, "how do i prune blueberries")
    assert re.search(pattern, "what's eating my zinnias")
    assert not re.search(pattern, "turn off the kitchen lights")


def test_loop_appends_the_source_when_the_model_leaves_it_out(shelf, monkeypatch):
    async def prompt(_):
        return ""
    monkeypatch.setattr(hestia, "_build_system_prompt", prompt)
    replies = [{"content": "", "tool_calls": [{"function": {"name": "library",
                                                            "arguments": {"query": "prune blueberry"}}}]},
               {"content": "Prune them in late winter."}]

    async def fake_chat(convo, schemas):
        return replies.pop(0)
    monkeypatch.setattr(backend, "ollama_chat", fake_chat)
    out = asyncio.run(hestia.run_agent([{"role": "user", "content": "when do I prune blueberries?"}]))
    assert out == "Prune them in late winter.\nSource: Blueberry Pruning Guide, p.1."


def test_nothing_close_enough_is_reported_as_not_in_the_library(shelf, monkeypatch):
    monkeypatch.setattr(library, "FLOOR", 1.01)   # above any cosine
    assert library.execute("prune blueberry").startswith("Nothing in the household library")
    monkeypatch.setattr(library, "FLOOR", 0.0)
    assert library.execute("prune blueberry").startswith("Library passages")


def test_edibility_questions_get_the_in_person_identification_line(shelf):
    assert library.EDIBLE_NOTE in library.execute("are wild garlic plants edible")
    assert library.EDIBLE_NOTE not in library.execute("when do I plant garlic")
    trace = hestia.TurnTrace(citations=["Forgotten Foods, p.2"])
    out = hestia._library_addendum("Forgotten Foods says purslane is a common wild green.", trace,
                                   "Is this purslane safe to eat?")
    assert out == hestia.EDIBLE_REFERRAL
    assert hestia._library_addendum("Forgotten Foods says confirm it in person with an expert.", trace,
                                    "Is it edible?") == ""


def test_new_subjects_route_to_the_library():
    pattern = hestia._INTENT_TOOLS["library"]
    for q in ("how long do I boil water at altitude", "what headspace for jelly jars",
              "how do i shut down the evaporator", "snake bite first aid", "how do I bug in"):
        assert re.search(pattern, q), q
