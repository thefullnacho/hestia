"""Photo intake — the silent-junk guard. A photo whose subject matches no existing entity
used to mint a new junk record with no signal; now `attach_photo` reports `created`, and the
/ingest/photo endpoint takes several comma-separated subjects and warns loudly on a new mint.
Regression for the 'Carrots and Beets harvest' compound-subject mis-file."""
from __future__ import annotations

import io

import pytest


# ----- records level: the `created` flag --------------------------------------

def test_attach_photo_reports_created_then_matched(db):
    # First photo for a brand-new bed -> minted, created=True.
    r1 = db.attach_photo("Carrots Round Bed", "/x/1.jpg", "harvest", "garden")
    assert r1["created"] is True
    # Same subject again -> resolves to the existing entity, created=False.
    r2 = db.attach_photo("Carrots Round Bed", "/x/2.jpg", "more", "garden")
    assert r2["created"] is False
    # A different subject -> a new mint again.
    r3 = db.attach_photo("Beets Round Bed", "/x/3.jpg", None, "garden")
    assert r3["created"] is True


def test_attach_photo_matches_existing_entity(db):
    db.upsert_entity("place", "Bed 3", attrs={"plant": "Potatoes"})
    rec = db.attach_photo("Bed 3", "/x/4.jpg", "potato", "garden")
    assert rec["created"] is False  # filed onto the existing bed, nothing minted


# ----- endpoint: multiple subjects + the loud warning -------------------------

@pytest.fixture
def client(tmp_path, monkeypatch, db):
    """A TestClient for the brain app, with a temp photo dir + known ingest token, writing to
    the fresh records DB from the `db` fixture (which patched records_store.DB_PATH)."""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    import hestia
    monkeypatch.setattr(hestia, "PHOTO_DIR", tmp_path / "photos")
    monkeypatch.setattr(hestia, "INGEST_TOKEN", "test-token")
    return fastapi_testclient.TestClient(hestia.app)


def _post(client, subject):
    return client.post(
        "/ingest/photo",
        data={"subject": subject, "domain": "garden", "token": "test-token"},
        files={"file": ("harvest.jpg", io.BytesIO(b"fakejpegbytes"), "image/jpeg")})


def test_ingest_splits_multiple_subjects(client):
    r = _post(client, "Carrots Round Bed, Beets Round Bed")
    assert r.status_code == 200
    body = r.json()
    filed = body["filed"]
    assert [f["subject"] for f in filed] == ["Carrots Round Bed", "Beets Round Bed"]
    # Both were new in the empty DB, so each saved to its own bed folder and the warning fires.
    assert all(f["created"] for f in filed)
    assert "carrots-round-bed" in filed[0]["saved"] and "beets-round-bed" in filed[1]["saved"]
    assert body["warning"] and "NEW entities" in body["warning"]


def test_ingest_no_warning_when_subject_matches(client, db):
    db.upsert_entity("place", "Carrots Round Bed")
    body = _post(client, "Carrots Round Bed").json()
    assert body["filed"][0]["created"] is False
    assert body["warning"] is None


def test_ingest_missing_subject_is_a_clear_400(client):
    r = client.post("/ingest/photo",
                    data={"subject": "  ,  ", "domain": "garden", "token": "test-token"},
                    files={"file": ("x.jpg", io.BytesIO(b"z"), "image/jpeg")})
    assert r.status_code == 400
    assert any("subject" in m for m in r.json()["missing"])
