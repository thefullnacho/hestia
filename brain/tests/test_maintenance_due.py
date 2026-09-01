"""GET /maintenance/due — the JSON feed behind the Glance due-maintenance tile. Must report
the exact same data records_store.due_assets() gives the `records due` chat action, so the
dashboard and the spoken answer never disagree."""
from __future__ import annotations

import pytest


@pytest.fixture
def client(db):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    import hestia
    return fastapi_testclient.TestClient(hestia.app)


def test_due_empty_when_nothing_overdue(client):
    r = client.get("/maintenance/due")
    assert r.status_code == 200
    assert r.json() == {"count": 0, "assets": []}


def test_due_lists_overdue_assets(client, db):
    db.upsert_entity("asset", "Furnace", attrs={"interval_days": 365})
    r = client.get("/maintenance/due")
    body = r.json()
    assert body["count"] == 1
    assert body["assets"][0]["name"] == "Furnace"
    assert body["assets"][0]["last"] == "never"


def test_due_excludes_recently_serviced(client, db):
    db.upsert_entity("asset", "Furnace", attrs={"interval_days": 365})
    db.log_event("service", subject="Furnace", action="serviced", detail="serviced",
                subject_kind="asset", strict_subject=True)
    r = client.get("/maintenance/due")
    assert r.json() == {"count": 0, "assets": []}
