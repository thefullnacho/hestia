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


# ----- watering tags: one prefilled field between a wet hand and a logged run -------------

def test_log_watering_tag_writes_depth_and_shows_it_as_an_estimate(db):
    db.upsert_entity("place", "Back Fence")
    import nfc
    body, status = nfc.log_watering_tag("Back Fence", "15", "zone3", "hi-rise")
    assert status == 200
    assert "Logged 15 min watering" in body
    assert "0.4 in, 56.6 gal (estimated)" in body
    total = db.water_totals(place="Back Fence")[0]
    assert total["minutes"] == 15.0 and total["sources"] == ["zone3"]


def test_log_watering_tag_without_a_sprinkler_claims_no_depth(db):
    db.upsert_entity("place", "Raised Bed 2")
    import nfc
    body, status = nfc.log_watering_tag("Raised Bed 2", "20", "zone4")
    assert status == 200 and "estimated" not in body
    total = db.water_totals(place="Raised Bed 2")[0]
    assert total["minutes"] == 20.0 and total["unmeasured"] == 1


@pytest.mark.parametrize("minutes", ["", "soon", "0", "-5"])
def test_log_watering_tag_rejects_a_run_with_no_duration(db, minutes):
    import nfc
    body, status = nfc.log_watering_tag("Back Fence", minutes, "zone3", "hi-rise")
    assert status == 400 and not db.water_totals()


def test_log_watering_tag_refuses_a_sprinkler_it_has_no_rate_for(db):
    import nfc
    body, status = nfc.log_watering_tag("Back Fence", "15", "zone3", "impulse")
    # Better to refuse than to log a run with a silently dropped rate.
    assert status == 400 and "impulse" in body and not db.water_totals()


def test_log_watering_tag_warns_on_a_place_it_has_never_seen(db):
    import nfc
    body, status = nfc.log_watering_tag("Somewhere New", "15", "zone3", "hi-rise")
    assert status == 200 and "wasn't a known entity" in body


def test_watering_form_asks_only_for_minutes_when_the_tag_knows_its_sprinkler(client):
    r = client.get("/nfc", params={"token": "test-token", "kind": "watering",
                                   "subject": "Back Fence", "source": "zone3",
                                   "sprinkler": "hi-rise"})
    assert r.status_code == 200
    assert 'value="15"' in r.text and "Back Fence" in r.text
    assert '<select id="sprinkler"' not in r.text


def test_watering_form_offers_the_choice_when_the_tag_does_not_know(client):
    r = client.get("/nfc", params={"token": "test-token", "kind": "watering",
                                   "subject": "Back Fence"})
    assert '<select id="sprinkler"' in r.text
    assert "None (drip or soaker)" in r.text and "Hi-Rise" in r.text


def test_watering_form_rejects_a_tag_naming_an_unknown_sprinkler(client):
    r = client.get("/nfc", params={"token": "test-token", "kind": "watering",
                                   "subject": "Back Fence", "sprinkler": "impulse"})
    assert "unknown sprinkler" in r.text


def test_watering_log_route_end_to_end(client, db):
    db.upsert_entity("place", "Back Fence")
    r = client.post("/nfc/log", data={"token": "test-token", "kind": "watering",
                                      "subject": "Back Fence", "minutes": "15",
                                      "source": "zone3", "sprinkler": "hi-rise"})
    assert r.status_code == 200 and "Logged 15 min watering" in r.text
    assert db.water_totals(place="Back Fence")[0]["gallons"] == 56.6


def test_an_unknown_kind_still_names_the_kinds_that_work(client):
    r = client.get("/nfc", params={"token": "test-token", "kind": "flooding",
                                   "subject": "Back Fence"})
    assert "kind=watering" in r.text
