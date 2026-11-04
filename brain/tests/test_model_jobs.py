"""model_jobs: model work that waits for a phone, and the jobs that queue it."""
import datetime as dt
import json

import pytest

import backend
import briefing
import briefing_store
import feed
import feed_store
import memory_store
import model_jobs
import note_taker


@pytest.fixture
def phone(monkeypatch):
    monkeypatch.setattr(backend, "BACKEND", "phone")


def _req(text="hi"):
    return {"messages": [{"role": "user", "content": text}], "options": {"temperature": 0}}


def _status(db, job_id):
    with db._conn() as c:
        return c.execute("SELECT status FROM model_jobs WHERE id=?", (job_id,)).fetchone()[0]


def test_a_newer_job_with_the_same_key_replaces_the_waiting_one(db):
    old = model_jobs.enqueue("briefing", _req("monday"), {"identifier": "a"}, key="briefing")
    new = model_jobs.enqueue("briefing", _req("tuesday"), {"identifier": "b"}, key="briefing")
    note = model_jobs.enqueue("note", _req("x"), {})
    assert [j["id"] for j in model_jobs.pending()] == [new, note]
    assert _status(db, old) == "superseded"


def test_expired_work_is_dropped_not_delivered_stale(db):
    job = model_jobs.enqueue("note", _req(), {}, ttl_hours=-1)
    assert model_jobs.pending() == []
    assert _status(db, job) == "expired"


def test_unknown_kinds_are_refused(db):
    with pytest.raises(ValueError):
        model_jobs.enqueue("mystery", _req(), {})


def test_drain_runs_oldest_first_and_hands_each_answer_to_its_finisher(db, monkeypatch):
    seen = []
    monkeypatch.setattr(model_jobs, "_finisher", lambda kind: lambda text, ctx: seen.append((text, ctx)))
    a = model_jobs.enqueue("note", _req("first"), {"n": 1})
    b = model_jobs.enqueue("note", _req("second"), {"n": 2})
    counts = model_jobs.drain(lambda r: {"content": r["messages"][0]["content"].upper(), "truncated": False})
    assert counts == {"done": 2, "failed": 0, "retry": 0}
    assert seen == [("FIRST", {"n": 1}), ("SECOND", {"n": 2})]
    assert _status(db, a) == _status(db, b) == "done" and model_jobs.pending() == []



def test_a_failing_job_waits_for_the_next_drain_then_gives_up(db):
    job = model_jobs.enqueue("note", _req(), {})
    def broken(_):
        raise ConnectionError("no phone")
    for _ in range(model_jobs.MAX_ATTEMPTS - 1):
        assert model_jobs.drain(broken)["retry"] == 1
    assert model_jobs.drain(broken)["failed"] == 1
    assert _status(db, job) == "failed" and model_jobs.pending() == []


def test_a_cut_off_answer_is_not_finished(db):
    model_jobs.enqueue("note", _req(), {})
    assert model_jobs.drain(lambda r: {"content": "[", "truncated": True})["retry"] == 1


def test_a_direct_call_in_phone_mode_fails_loudly(phone):
    with pytest.raises(RuntimeError):
        backend.complete([{"role": "user", "content": "x"}])


# --- the jobs, in phone mode -------------------------------------------------------------------

def test_the_briefing_keeps_plain_wording_now_and_takes_the_phones_later(db, phone, tmp_path, monkeypatch):
    monkeypatch.setattr(briefing_store.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(briefing, "build_facts", lambda: ["Rain after 3 pm.", "Trash goes out tonight."])
    monkeypatch.setattr(briefing, "push", lambda _: pytest.fail("no push in phone mode yet"))
    monkeypatch.setattr("sys.argv", ["briefing.py"])
    assert briefing.main() == 0
    entry = briefing_store.recent()[0]
    assert entry["narration"] == "queued" and "Rain after 3 pm." in entry["text"]
    job, = model_jobs.pending()
    assert job["kind"] == "briefing" and "Trash goes out tonight." in job["request"]["messages"][0]["content"]
    model_jobs.drain(lambda r: {"content": "Rain this afternoon, and the trash goes out tonight.", "truncated": False})
    entry = briefing_store.recent()[0]
    assert entry["narration"] == "model" and entry["text"].startswith("Rain this afternoon")


def test_the_feed_queues_its_ranking_and_saves_the_picks_when_answered(db, phone, monkeypatch):
    cands = [{"title": "Overwintering dahlias", "source": "ext.example", "url": "https://ext.example/d",
              "description": "", "excerpt": "", "image_url": ""}]
    monkeypatch.setattr(feed, "gather", lambda person, seen: cands)
    monkeypatch.setattr(feed, "fetch_candidates", lambda raw, client: raw)
    monkeypatch.setattr(feed, "cache_image", lambda client, url, d: None)
    person = {"id": "nacho", "rubric": "", "queries": []}
    assert feed.run_person(person, dt.date(2026, 11, 4), client=None) == 0
    job, = model_jobs.pending()
    assert job["key"] == "nacho" and job["context"]["cands"] == cands
    saved = []
    monkeypatch.setattr(feed_store, "save_day", lambda pid, day, picks: saved.append((pid, day, picks)) or len(picks))
    model_jobs.drain(lambda r: {"content": json.dumps({"picks": [{"n": 1, "why": "you grow dahlias"}]}),
                                "truncated": False})
    assert saved and saved[0][0] == "nacho" and saved[0][2][0]["title"] == "Overwintering dahlias"


def test_the_note_taker_queues_an_exchange_and_proposes_when_answered(db, phone, tmp_path, monkeypatch):
    monkeypatch.setattr(note_taker, "INBOX_DIR", tmp_path / "inbox")
    (tmp_path / "memory").mkdir()
    monkeypatch.setattr(memory_store, "MEMORY_DIR", tmp_path / "memory")
    msgs = [{"role": "user", "content": "By the way, the trash goes out on Tuesday nights now."}]
    assert note_taker.run(msgs, "Got it.") == []
    job, = model_jobs.pending()
    assert job["kind"] == "note" and "Tuesday" in job["request"]["messages"][0]["content"]
    model_jobs.drain(lambda r: {"content": '[{"content": "Trash goes out Tuesday nights", "type": "routine", '
                                           '"confidence": 0.9}]', "truncated": False})
    assert len(list((tmp_path / "inbox").glob("*.md"))) == 1
