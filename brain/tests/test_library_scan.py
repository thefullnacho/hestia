"""Documents a phone read (library_store.add_scanned, POST /v1/phone/library): text only, no model.

The phone runs Apple's on-device text recognition and sends the page texts; the box files them
through the same index_doc as the PDF shelf. Pinned here: the device token, the shape limits,
that a resend replaces, that the folder ingest never removes them, that the phone removes only
its own, and that an embedder failure is reported instead of hidden.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import hestia
import library_store as lib
import phone

DOC_ID = "6F1C2B7E-4A2D-4C1A-9F00-3B5D7E2A1C44"
PAGES = ["Dishwasher warranty: parts and labor are covered for two years from purchase. " * 4,
         "Register the serial number within 30 days to keep the extended warranty. " * 4]


def fake_embed(texts):
    return [[1.0, 0.0] for _ in texts]


@pytest.fixture
def box(db, monkeypatch):
    monkeypatch.setattr(lib, "embed", fake_embed)
    lib._vectors.clear()
    token = phone.add_device("sister-iphone")
    return {"client": TestClient(hestia.app), "auth": {"Authorization": f"Bearer {token}"}}


def post(box, body, auth=True):
    return box["client"].post("/v1/phone/library", content=json.dumps(body),
                              headers=box["auth"] if auth else {})


def test_a_phone_files_a_document_and_it_is_searchable(box):
    r = post(box, {"id": DOC_ID, "title": "Dishwasher manual", "pages": PAGES})
    assert r.status_code == 200
    out = r.json()
    assert out["passages"] >= 2 and out["embedded"] and out["pages"] == 2
    hits = lib.keyword_search("dishwasher warranty serial")
    assert hits and hits[0]["title"] == "Dishwasher manual"


def test_the_device_token_is_required(box):
    assert post(box, {"id": DOC_ID, "title": "x", "pages": PAGES}, auth=False).status_code == 401
    assert box["client"].get("/v1/phone/library").status_code == 401
    assert box["client"].delete(f"/v1/phone/library/{DOC_ID}").status_code == 401
    assert lib.docs() == []


@pytest.mark.parametrize("body", [
    {"id": "../../etc", "title": "x", "pages": ["a"]},
    {"id": "short", "title": "x", "pages": ["a"]},
    {"id": DOC_ID, "title": "", "pages": ["a"]},
    {"id": DOC_ID, "title": "x" * 201, "pages": ["a"]},
    {"id": DOC_ID, "title": "x", "pages": []},
    {"id": DOC_ID, "title": "x", "pages": "one long string"},
    {"id": DOC_ID, "title": "x", "pages": [1, 2]},
    {"id": DOC_ID, "title": "x", "pages": ["a"] * 301},
    [1, 2],
])
def test_bad_shapes_are_refused_and_nothing_is_filed(box, body):
    assert post(box, body).status_code == 400
    assert lib.docs() == []


def test_a_resend_replaces_instead_of_duplicating(box):
    post(box, {"id": DOC_ID, "title": "Dishwasher manual", "pages": PAGES})
    post(box, {"id": DOC_ID, "title": "Dishwasher manual (rescan)", "pages": PAGES[:1]})
    [doc] = lib.docs()
    assert doc["title"] == "Dishwasher manual (rescan)" and doc["pages"] == 1 and doc["scanned"]


def test_the_folder_ingest_never_removes_what_a_phone_filed(box, tmp_path):
    post(box, {"id": DOC_ID, "title": "Dishwasher manual", "pages": PAGES})
    report = lib.ingest(folder=tmp_path)   # an empty PDF folder
    assert report["removed"] == 0 and [d["title"] for d in lib.docs()] == ["Dishwasher manual"]


def test_the_phone_lists_and_removes_only_its_own(box):
    lib.index_doc("garlic.pdf", ["Plant garlic cloves in October. " * 5], "Growing Garlic | Ext")
    post(box, {"id": DOC_ID, "title": "Dishwasher manual", "pages": PAGES})
    listed = box["client"].get("/v1/phone/library", headers=box["auth"]).json()
    assert [d["id"] for d in listed["scanned"]] == [DOC_ID]
    assert [d["title"] for d in listed["shelf"]] == ["Growing Garlic"] and "id" not in listed["shelf"][0]
    assert box["client"].delete("/v1/phone/library/garlic.pdf", headers=box["auth"]).status_code == 404
    assert box["client"].delete(f"/v1/phone/library/{DOC_ID}", headers=box["auth"]).status_code == 200
    assert [d["title"] for d in lib.docs()] == ["Growing Garlic"]
    assert box["client"].delete(f"/v1/phone/library/{DOC_ID}", headers=box["auth"]).status_code == 404


def test_an_embedder_failure_is_reported_and_the_document_still_filed(box, monkeypatch):
    def down(texts):
        raise ConnectionError("ollama is not answering")
    monkeypatch.setattr(lib, "embed", down)
    out = post(box, {"id": DOC_ID, "title": "Dishwasher manual", "pages": PAGES}).json()
    assert out["embedded"] is False and "keyword search still finds it" in out["note"]
    assert lib.keyword_search("dishwasher warranty")


def test_blank_pages_file_nothing_searchable_and_say_so(box):
    out = post(box, {"id": DOC_ID, "title": "Blurry photo", "pages": ["", "   "]}).json()
    assert out["passages"] == 0 and "No text was found" in out["note"]


@pytest.mark.parametrize("question", [
    "when does the dishwasher warranty run out",
    "what does the furnace manual say about the filter",
    "what model number is the fridge",
])
def test_questions_about_the_households_papers_reach_the_library(question):
    import routing
    assert "library" in {s["function"]["name"] for s in routing.request_schemas(question)}
