"""House docs: the model proposes, code checks, a person keeps, and the library files it.

Covers the store's checks (kind, a real entity, only passages the library returned), Keep into the
library with the one-way .md copy, revisions replacing what they revise, notes pinned to a page
arriving with that page, the folder ingest leaving house docs alone, the Today API's guard, the
tool, routing and the MCP server.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import config
import docs_store
import hestia
import library_store as lib
import mcp_server
import records_store
import routing
import tools
from tools import doc as doc_tool
from tools import library

H = {"Content-Type": "application/json", "X-Hestia-Docs": "1"}
WORDS = ["generator", "choke", "radio", "gmrs", "channel", "cold"]


def fake_embed(texts):
    out = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in WORDS] + [0.01]
        n = sum(x * x for x in v) ** 0.5
        out.append([x / n for x in v])
    return out


@pytest.fixture
def house(db, tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "embed", fake_embed)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    lib._vectors.clear()
    lib.index_doc("gen.pdf", ["Start the generator with the choke closed when cold. " * 5,
                              "Change the generator oil every 50 hours. " * 5], "Generator Manual | Maker")
    lib.index_doc("gmrs.pdf", ["GMRS radio channel plan for families. " * 5], "GMRS Rules | FCC")
    lib.embed_missing()
    records_store.upsert_entity("asset", "Generator")
    cite = library.cite(lib.search("generator choke cold", 1)[0])
    return {"cite": cite, "client": TestClient(hestia.app)}


def test_code_checks_what_the_model_cant_be_trusted_with(house):
    ok = dict(title="Choke", body="Tap the choke twice below 20F.")
    with pytest.raises(ValueError, match="kind"):
        docs_store.propose("memo", **ok)
    with pytest.raises(ValueError, match="note must be about"):
        docs_store.propose("note", **ok)
    with pytest.raises(ValueError, match="no record named"):
        docs_store.propose("note", about="Tractor", **ok)
    with pytest.raises(ValueError, match="not a passage the library returned"):
        docs_store.propose("guide", sources=["Made Up Book, 2020, p.4"], allowed=[house["cite"]], **ok)
    with pytest.raises(ValueError, match="must be one of the library passages"):
        docs_store.propose("note", about="Generator", page="Generator Manual, p.9", allowed=[house["cite"]], **ok)
    with pytest.raises(ValueError, match="too long"):
        docs_store.propose("guide", title="x", body="y" * (docs_store.MAX_BODY + 1))
    assert docs_store.pending() == []


def test_a_proposal_waits_and_keep_files_it_with_a_file_copy(house):
    out = docs_store.propose("guide", "GMRS card", "Channel 20 is ours.\n\nCall signs: WRXX123.",
                             sources=[], allowed=[])
    assert [d["title"] for d in docs_store.pending()] == ["GMRS card"]
    assert not any(d["title"].startswith("House doc") for d in lib.docs())     # not filed yet
    msg = docs_store.keep(out["id"], body="Channel 20 is ours.\n\nCall signs: WRXX123 and WRXX124.")
    assert msg.startswith("Kept 'GMRS card'")
    filed = [d for d in lib.docs() if d["title"] == "House doc: GMRS card"]
    assert filed and filed[0]["source"] == docs_store.SOURCE
    md = list((config.DATA_DIR / "house-docs").glob("*.md"))
    assert len(md) == 1 and "WRXX124" in md[0].read_text() and md[0].name.startswith("gmrs-card-")
    assert docs_store.pending() == []
    with pytest.raises(ValueError, match="already kept"):
        docs_store.keep(out["id"])


def test_a_note_pinned_to_a_page_comes_with_that_page(house):
    out = docs_store.propose("note", "Cold start", "Ours needs the choke tapped twice below 20F.",
                             about="Generator", page=house["cite"], allowed=[house["cite"]])
    docs_store.keep(out["id"])
    answer = library.execute("how do I start the generator when it's cold with the choke")
    assert "House notes on these pages" in answer and "tapped twice" in answer
    assert "tapped twice" in tools.dispatch("records", {"action": "entity", "name": "Generator"})


def test_a_new_note_on_the_same_thing_is_a_revision_that_replaces_it(house):
    first = docs_store.propose("note", "Choke", "Tap it twice.", about="Generator")
    docs_store.keep(first["id"])
    second = docs_store.propose("note", "Choke, updated", "Tap it three times now.", about="Generator")
    assert second["revises"] == "Choke"
    assert docs_store.pending()[0]["revises_title"] == "Choke"
    assert "replaces 'Choke'" in docs_store.keep(second["id"])
    titles = [d["title"] for d in lib.docs()]
    assert "House doc: Choke, updated" in titles and "House doc: Choke" not in titles
    assert docs_store.get(first["id"])["status"] == "replaced"
    assert len(list((config.DATA_DIR / "house-docs").glob("*.md"))) == 1


def test_the_folder_ingest_never_removes_house_docs(house, tmp_path):
    docs_store.keep(docs_store.propose("guide", "Outage card", "Flip the transfer switch.")["id"])
    shelf = tmp_path / "shelf"
    shelf.mkdir()
    report = lib.ingest(shelf)
    assert report["removed"] == 2                      # the two PDFs that aren't in this folder
    assert [d["title"] for d in lib.docs()] == ["House doc: Outage card"]


def test_discard_and_the_today_guard(house):
    c = house["client"]
    a = docs_store.propose("guide", "Keep me", "Body one.")["id"]
    b = docs_store.propose("guide", "Drop me", "Body two.")["id"]
    assert [d["title"] for d in c.get("/api/docs/proposed").json()["docs"]] == ["Keep me", "Drop me"]
    for headers in ({"Content-Type": "application/json"}, {**H, "Origin": "http://elsewhere.example"}):
        assert c.post(f"/api/docs/{a}/keep", content="{}", headers=headers).status_code == 409
    r = c.post(f"/api/docs/{a}/keep", content=json.dumps({"title": "Kept, renamed", "body": "Edited."}), headers=H)
    assert r.status_code == 200 and r.json()["left"] == 1
    assert docs_store.get(a)["title"] == "Kept, renamed"
    assert c.post(f"/api/docs/{b}/discard", content="{}", headers=H).json()["left"] == 0
    assert docs_store.get(b)["status"] == "dropped"
    assert c.post("/api/docs/999/keep", content="{}", headers=H).status_code == 404
    assert c.post(f"/api/docs/{b}/keep", content="{}", headers=H).status_code == 409


def test_the_tool_cites_only_what_the_library_returned_this_turn(house):
    args = {"action": "propose", "kind": "guide", "title": "Cold starts", "body": "Close the choke.",
            "sources": [house["cite"]]}
    assert "not a passage the library returned" in tools.dispatch("doc", args)
    token = doc_tool.CITATIONS.set([house["cite"]])
    try:
        out = tools.dispatch("doc", args)
    finally:
        doc_tool.CITATIONS.reset(token)
    assert out.startswith("Proposed house doc #") and "Today" in out
    assert docs_store.pending()[0]["sources"] == [house["cite"]]
    from tool_contract import mutation, receipt
    assert mutation("doc", args) and receipt("doc", args, out).status == "succeeded"


def test_routing_offers_doc_with_the_shelf_and_counts_it_as_asked():
    for text in ("write up what happened with the generator in the storm",
                 "make me a GMRS card for the family", "add a note to the generator: tap the choke"):
        names = {s["function"]["name"] for s in routing.request_schemas(text)}
        assert {"doc", "library", "records"} <= names, text
        assert "doc" in routing.asked_writes(text), text
    for text in ("the page was long", "what's on the card"):
        assert "doc" not in routing.asked_writes(text)


def test_mcp_offers_doc_only_with_writes_on(monkeypatch):
    monkeypatch.delenv("HESTIA_MCP_WRITES", raising=False)
    assert "doc" not in {t["name"] for t in mcp_server._tool_list()}
    monkeypatch.setenv("HESTIA_MCP_WRITES", "1")
    assert "doc" in {t["name"] for t in mcp_server._tool_list()}


def test_a_note_without_about_takes_the_one_records_name_it_mentions(house):
    # Gemma looked the generator up, then left `about` out of the note (2026-11-23).
    out = docs_store.propose("note", "Generator choke", "Tap it twice below 20F.")
    assert docs_store.get(out["id"])["about"] == "Generator"
    records_store.upsert_entity("asset", "Generator shed")
    with pytest.raises(ValueError, match="Candidates: Generator shed, Generator"):
        docs_store.propose("note", "Generator shed and generator", "Both leak.")
    assert "Error" in tools.dispatch("doc", {"action": "propose", "kind": "note", "title": "Choke",
                                             "body": "Tap it twice."})
    from tool_contract import receipt
    assert receipt("doc", {"action": "propose"}, "Error: a note must be about...").status == "failed"
