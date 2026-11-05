"""phone reminders: the list a phone schedules as iOS local notifications, and whose it is."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import hestia
import phone
import reminders_store


@pytest.fixture
def phones(tmp_path, db, monkeypatch):
    """Two devices on a throwaway DB: Alex's untied (the default phone), Dawn's tied to dawn."""
    monkeypatch.setattr(phone, "DEVICES", tmp_path / "devices.json")
    monkeypatch.setattr(phone, "PEOPLE", tmp_path / "people.json")
    tokens = {"alex": phone.add_device("alex-17pro"), "dawn": phone.add_device("dawn-15")}
    phone.tie("dawn-15", "dawn")
    return tokens


def at(hours: float) -> str:
    """A stored due time, as the reminder tool writes it: naive local ISO."""
    return (dt.datetime.now() + dt.timedelta(hours=hours)).isoformat(timespec="seconds")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def get(client, token, **params):
    return client.get("/v1/phone/reminders", headers=auth(token), params=params)


def texts(body):
    return [r["text"] for r in body["reminders"]]


def test_a_device_token_is_required_and_nothing_leaks(phones):
    reminders_store.add(at(1), "vet at ten")
    with TestClient(hestia.app) as client:
        for headers in ({}, auth("guess"), {"Authorization": "vet at ten"}):
            r = client.get("/v1/phone/reminders", headers=headers)
            assert r.status_code == 401 and "vet" not in r.text


def test_the_list_is_upcoming_unfired_soonest_first_with_an_aware_time(phones):
    soon, later = reminders_store.add(at(2), "move the sprinkler"), reminders_store.add(at(30), "worm the pups")
    reminders_store.add(at(1), "flip the compost")
    fired = reminders_store.add(at(3), "already sent")
    reminders_store.mark_fired(fired, at(0))
    reminders_store.add(at(-1), "an hour ago")
    with TestClient(hestia.app) as client:
        body = get(client, phones["alex"]).json()
    assert texts(body) == ["flip the compost", "move the sprinkler", "worm the pups"]
    first = body["reminders"][1]
    when = dt.datetime.fromisoformat(first["due_at"])
    assert when.tzinfo is not None
    stored = next(r["due_at"] for r in reminders_store.pending() if r["id"] == soon)
    assert when == dt.datetime.fromisoformat(stored).astimezone()
    assert first["id"].startswith(f"reminder-{soon}-") and later != soon
    assert body["device"] == "alex-17pro" and body["person"] is None and not body["truncated"]


def test_the_horizon_defaults_to_two_weeks_and_is_clamped(phones):
    reminders_store.add(at(24 * 3), "three days")
    reminders_store.add(at(24 * 20), "twenty days")
    reminders_store.add(at(24 * 40), "forty days")
    with TestClient(hestia.app) as client:
        assert texts(get(client, phones["alex"]).json()) == ["three days"]
        assert texts(get(client, phones["alex"], days=21).json()) == ["three days", "twenty days"]
        wide = get(client, phones["alex"], days=400).json()
        assert wide["days"] == phone.REMINDER_MAX_DAYS and "forty days" not in texts(wide)
        assert get(client, phones["alex"], days=0).json()["days"] == 1
        assert get(client, phones["alex"], days="soon").status_code == 400


def test_the_count_is_capped_at_what_ios_will_hold(phones):
    for i in range(phone.REMINDER_LIMIT + 6):
        reminders_store.add(at(1 + i), f"r{i}")
    with TestClient(hestia.app) as client:
        body = get(client, phones["alex"]).json()
    assert len(body["reminders"]) == phone.REMINDER_LIMIT and body["truncated"]
    assert texts(body)[0] == "r0"   # the soonest are kept
    # the list is whole only up to its last entry, so the app knows to sync before then
    assert body["complete_until"] == body["reminders"][-1]["due_at"]


def test_each_phone_gets_its_own_persons_reminders(phones):
    reminders_store.add(at(1), "household trash night")
    reminders_store.add(at(2), "dawn: call the farrier", person="dawn")
    reminders_store.add(at(3), "theo: feed the fish", person="theo")   # theo has no phone tied
    with TestClient(hestia.app) as client:
        alex = get(client, phones["alex"]).json()
        dawn = get(client, phones["dawn"]).json()
    assert texts(dawn) == ["dawn: call the farrier"] and dawn["person"] == "dawn"
    # the untied phone is the default phone: null-person reminders plus anyone with no phone
    assert texts(alex) == ["household trash night", "theo: feed the fish"]


def test_untying_a_phone_makes_it_a_default_phone_again(phones):
    reminders_store.add(at(1), "household")
    reminders_store.add(at(2), "for dawn", person="dawn")
    phone.tie("dawn-15", None)
    with TestClient(hestia.app) as client:
        assert texts(get(client, phones["dawn"]).json()) == ["household", "for dawn"]


def test_a_tie_needs_a_known_device_and_a_plain_name(phones):
    with pytest.raises(ValueError):
        phone.tie("nobody", "dawn")
    for bad in ("Dawn", "dawn smith", "", "x" * 33):
        with pytest.raises(ValueError):
            phone.tie("alex-17pro", bad)


def test_the_sync_hash_changes_with_the_list_and_answers_304_when_not(phones):
    rid = reminders_store.add(at(1), "vet at ten")
    with TestClient(hestia.app) as client:
        first = get(client, phones["alex"])
        etag = first.headers["etag"]
        assert etag == f'"{first.json()["sync"]}"'
        again = client.get("/v1/phone/reminders", headers={**auth(phones["alex"]), "If-None-Match": etag})
        assert again.status_code == 304 and not again.content
        reminders_store.add(at(2), "pick up feed")
        added = get(client, phones["alex"]).json()["sync"]
        assert added != first.json()["sync"]
        reminders_store.cancel(rid)
        assert get(client, phones["alex"]).json()["sync"] not in (added, first.json()["sync"])


def test_a_reused_row_id_gets_a_new_notification_id(phones, monkeypatch):
    """SQLite hands a deleted top row's id to the next insert; the phone must not mistake the new
    reminder for the old one."""
    rid = reminders_store.add(at(1), "old")
    before = phone.reminders_for("alex-17pro")["reminders"][0]["id"]
    reminders_store.cancel(rid)
    import records_store
    monkeypatch.setattr(records_store, "_now", lambda: "2099-01-01T00:00:00")
    assert reminders_store.add(at(1), "new") == rid
    assert phone.reminders_for("alex-17pro")["reminders"][0]["id"] != before
