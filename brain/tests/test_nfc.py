"""NFC capture — the no-LLM logging path (nfc.py). A tag scan must never claim success without
a real write: the chat agent did exactly that on 2026-09-01 (silent tool-scoping miss), which
this endpoint exists to be immune to. Covers the pure logging helpers and the token/subject/kind
validation at the /nfc + /nfc/log routes."""
from __future__ import annotations

import pytest


# ----- nfc.py helpers: real writes, no FastAPI -------------------------------------------

def test_log_harvest_tag_writes_and_confirms(db):
    db.upsert_entity("place", "Bed 2")
    import nfc
    body, status = nfc.log_harvest_tag("Bed 2", "Zucchini", "7", "lb")
    assert status == 200
    assert "Logged 7 lb of Zucchini" in body
    rows = db.harvest_totals(bed="Bed 2")
    assert rows and rows[0]["crop"] == "Zucchini"


def test_log_harvest_tag_warns_on_new_bed(db):
    import nfc
    body, status = nfc.log_harvest_tag("Mystery Bed", "Kale", "2", "lb")
    assert status == 200
    assert "wasn't a known entity" in body  # loud, not silent


def test_log_harvest_tag_rejects_missing_crop(db):
    import nfc
    body, status = nfc.log_harvest_tag("Bed 2", "", "7", "lb")
    assert status == 400
    assert "Crop is required" in body


def test_log_harvest_tag_rejects_bad_qty(db):
    import nfc
    body, status = nfc.log_harvest_tag("Bed 2", "Zucchini", "not-a-number", "lb")
    assert status == 400
    body2, status2 = nfc.log_harvest_tag("Bed 2", "Zucchini", "-3", "lb")
    assert status2 == 400


def test_log_use_tag_writes_minutes(db):
    db.upsert_entity("asset", "Weedwhacker")
    import nfc
    body, status = nfc.log_use_tag("Weedwhacker", "45", "front + back yard")
    assert status == 200
    assert "Logged 45 min run" in body


def test_log_use_tag_rejects_bad_minutes(db):
    import nfc
    body, status = nfc.log_use_tag("Weedwhacker", "not-a-number", "")
    assert status == 400
    body2, status2 = nfc.log_use_tag("Weedwhacker", "0", "")
    assert status2 == 400


def test_log_use_tag_does_not_affect_due_assets(db):
    # 'use' is a metric log, not a service event — must never satisfy an interval_days reminder.
    db.upsert_entity("asset", "Weedwhacker", attrs={"interval_days": 30})
    import nfc
    nfc.log_use_tag("Weedwhacker", "45", "")
    assert any(a["name"] == "Weedwhacker" for a in db.due_assets())


def test_log_service_tag_resets_due_clock(db):
    db.upsert_entity("asset", "Furnace Filter", attrs={"interval_days": 90})
    import nfc
    # Overdue before any service is logged.
    assert any(a["name"] == "Furnace Filter" for a in db.due_assets())
    body, status = nfc.log_service_tag("Furnace Filter", "")
    assert status == 200
    assert "Logged service" in body
    assert not any(a["name"] == "Furnace Filter" for a in db.due_assets())


# ----- routes: token + validation, via FastAPI TestClient --------------------------------

@pytest.fixture
def client(monkeypatch, db):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    import hestia
    monkeypatch.setattr(hestia, "NFC_TOKEN", "test-token")
    return fastapi_testclient.TestClient(hestia.app)


def test_capture_form_rejects_bad_token(client):
    r = client.get("/nfc", params={"token": "wrong", "kind": "harvest", "subject": "Bed 2"})
    assert r.status_code == 401


def test_capture_form_requires_subject(client):
    r = client.get("/nfc", params={"token": "test-token", "kind": "harvest", "subject": ""})
    assert r.status_code == 400


def test_capture_form_renders_locked_subject(client):
    r = client.get("/nfc", params={"token": "test-token", "kind": "harvest", "subject": "Bed 2"})
    assert r.status_code == 200
    assert "Bed 2" in r.text
    assert 'action="/nfc/log"' in r.text


def test_log_route_end_to_end(client, db):
    db.upsert_entity("place", "Bed 2")
    r = client.post("/nfc/log", data={"token": "test-token", "kind": "harvest",
                                      "subject": "Bed 2", "crop": "Cucumber",
                                      "qty": "6", "unit": "lb"})
    assert r.status_code == 200
    assert "Logged 6 lb of Cucumber" in r.text
    assert db.harvest_totals(bed="Bed 2")


def test_log_route_bad_token(client):
    r = client.post("/nfc/log", data={"token": "nope", "kind": "harvest",
                                      "subject": "Bed 2", "crop": "X", "qty": "1", "unit": "lb"})
    assert r.status_code == 401
