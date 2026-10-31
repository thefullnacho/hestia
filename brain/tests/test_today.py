"""/today: the app's model-free Today view. Every section is a plain store read, and one
backend being down blanks only its own section."""
from __future__ import annotations

import datetime as dt

import pytest

import backend  # noqa: E402
import hestia
import reminders_store


@pytest.fixture
def client(db, monkeypatch):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setattr(hestia.tools.shopping, "_items",
                        lambda: [{"summary": "milk"}, {"summary": "eggs", "status": "completed"}])
    monkeypatch.setattr(hestia.tools.calendar, "events", lambda s, e: [
        {"summary": "Vet", "start": s.replace(hour=9), "end": s.replace(hour=10), "all_day": False,
         "location": "", "description": "", "calendar": "calendar.family"}])
    monkeypatch.setattr(hestia.board, "snapshot", lambda: {"columns": {"hands": []}})
    return fastapi_testclient.TestClient(hestia.app)


def test_today_bundles_every_section(client):
    now = dt.datetime.now()
    reminders_store.add((now - dt.timedelta(minutes=5)).isoformat(timespec="seconds"), "feed pups")
    reminders_store.add((now + dt.timedelta(days=3)).isoformat(timespec="seconds"), "later")
    body = client.get("/today").json()
    assert body["date"] == now.date().isoformat()
    assert [(r["text"], r["overdue"]) for r in body["reminders"]["items"]] == [("feed pups", True)]
    assert body["shopping"] == {"items": ["milk"]}
    assert body["calendar"]["events"][0]["summary"] == "Vet"
    assert body["calendar"]["events"][0]["start"].endswith("09:00")
    assert body["queue"] == {"columns": {"hands": []}}
    assert body["maintenance"] == {"count": 0, "assets": []}


def test_one_backend_down_blanks_only_its_section(client, monkeypatch):
    def down():
        raise ConnectionError("HA unreachable")
    monkeypatch.setattr(hestia.tools.shopping, "_items", down)
    r = client.get("/today")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body["shopping"]
    assert body["calendar"]["events"] and body["queue"]


def test_today_never_calls_the_model(client, monkeypatch):
    async def forbidden(*_a, **_k):
        raise AssertionError("the Today view must not reach the model")
    monkeypatch.setattr(backend, "ollama_chat", forbidden)
    assert client.get("/today").status_code == 200
