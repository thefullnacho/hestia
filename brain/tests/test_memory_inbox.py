"""GET /memory/inbox — the note-taker queue as JSON, for the Glance dashboard tile.

The endpoint exists so the review queue stops being invisible between CLI runs. Two things
matter and are pinned here: it serves the SAME list `review_notes.py list` prints (one reader,
so the tile and the CLI can't disagree about depth), and it is READ-ONLY — nothing here can
promote a fact into live memory.
"""
from __future__ import annotations

import datetime as dt

import pytest

import review_notes


def _proposal(path, rid, body, created, type="household", confidence=0.9):
    path.write_text(
        f"---\nid: {rid}\nstatus: proposed\ntype: {type}\nconfidence: {confidence}\n"
        f"source: note-taker@{created[:10]}\ncreated: {created}\n---\n{body}\n")


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """A temp inbox with three proposals of known ages, wired into review_notes."""
    d = tmp_path / "inbox"
    d.mkdir()
    today = dt.date.today()
    ages = {"oldest": 30, "middle": 10, "newest": 0}
    for rid, days in ages.items():
        stamp = (dt.datetime.now() - dt.timedelta(days=days)).replace(microsecond=0).isoformat()
        _proposal(d / f"{rid}.md", rid, f"Fact {rid}.", stamp)
    monkeypatch.setattr(review_notes, "INBOX_DIR", d)
    return d


@pytest.fixture
def client(queue):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    import hestia
    return fastapi_testclient.TestClient(hestia.app)


def test_lists_the_queue_newest_first(client):
    r = client.get("/memory/inbox")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 3
    assert [p["id"] for p in data["proposals"]] == ["newest", "middle", "oldest"]


def test_carries_the_fields_a_tile_renders(client):
    p = client.get("/memory/inbox").json()["proposals"][0]
    assert p["body"] == "Fact newest."
    assert p["type"] == "household"
    assert p["confidence"] == 0.9
    assert p["age_days"] == 0
    assert p["source"].startswith("note-taker@")


def test_age_days_is_precomputed_for_the_template(client):
    ages = {p["id"]: p["age_days"] for p in client.get("/memory/inbox").json()["proposals"]}
    assert ages == {"newest": 0, "middle": 10, "oldest": 30}


def test_limit_caps_rows_but_count_stays_the_true_depth(client):
    data = client.get("/memory/inbox", params={"limit": 2}).json()
    assert data["count"] == 3  # the tile must still be able to say "3 waiting"
    assert [p["id"] for p in data["proposals"]] == ["newest", "middle"]


def test_empty_inbox_is_a_clean_zero_not_an_error(client, queue):
    for f in queue.glob("*.md"):
        f.unlink()
    data = client.get("/memory/inbox").json()
    assert data == {"count": 0, "dir": str(queue), "proposals": []}


def test_missing_inbox_dir_is_survivable(client, queue, monkeypatch):
    monkeypatch.setattr(review_notes, "INBOX_DIR", queue.parent / "gone")
    r = client.get("/memory/inbox")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_endpoint_and_cli_read_the_same_list(client, capsys):
    """The whole point of review_notes.proposals() being public."""
    review_notes.cmd_list()
    printed = capsys.readouterr().out
    for p in client.get("/memory/inbox").json()["proposals"]:
        assert p["id"] in printed


def test_queue_is_read_only_over_http(client, queue):
    """No HTTP verb promotes or discards — review stays a human act at the CLI."""
    for verb in (client.post, client.put, client.delete, client.patch):
        assert verb("/memory/inbox").status_code == 405
    assert len(list(queue.glob("*.md"))) == 3


# --- the app's Keep and Not this (memory_api.py): writes, behind their own guard ------------------

H = {"X-Hestia-Memory": "1", "Content-Type": "application/json"}


def _records(mem):
    """Live memory records (the store also keeps an INDEX.md beside them)."""
    return [p for p in mem.MEMORY_DIR.glob("*.md") if p.name != "INDEX.md"]


def _keep(client, rid, body=None, headers=H):
    import json
    return client.post(f"/api/memory/inbox/{rid}/keep", content=json.dumps(body or {}), headers=headers)


def test_keep_makes_it_live_memory_and_empties_that_slot(client, mem, queue):
    r = _keep(client, "middle")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["left"] == 2
    assert not (queue / "middle.md").exists()
    [rec] = [mem._parse(p) for p in _records(mem)]
    assert rec["body"] == "Fact middle." and rec["meta"]["source"].startswith("reviewed@")


def test_keep_in_the_persons_own_words(client, mem):
    r = _keep(client, "newest", {"text": "  The well pump   breaker is #14. "})
    assert r.status_code == 200
    [rec] = [mem._parse(p) for p in _records(mem)]
    assert rec["body"] == "The well pump breaker is #14."


def test_edited_text_gets_the_note_takers_own_filter(client, mem, queue):
    r = _keep(client, "newest", {"text": "Alex wants a reminder to call the vet."})
    assert r.status_code == 409 and "wish or task" in r.json()["error"]
    assert _keep(client, "newest", {"text": "   "}).status_code == 409
    assert (queue / "newest.md").exists() and not _records(mem)


def test_not_this_drops_it_without_remembering(client, mem, queue):
    import json
    r = client.post("/api/memory/inbox/oldest/discard", content=json.dumps({}), headers=H)
    assert r.status_code == 200 and r.json()["left"] == 2
    assert not (queue / "oldest.md").exists() and not _records(mem)


def test_a_second_tap_finds_nothing_and_writes_nothing_twice(client, mem):
    assert _keep(client, "middle").status_code == 200
    assert _keep(client, "middle").status_code == 404
    assert len(_records(mem)) == 1


def test_the_guard_stops_requests_not_from_the_app(client, mem, queue):
    import json
    assert _keep(client, "middle", headers={"Content-Type": "application/json"}).status_code == 409
    assert _keep(client, "middle", headers={**H, "Origin": "http://elsewhere.example"}).status_code == 409
    assert client.post("/api/memory/inbox/middle/keep", data={"x": "1"},
                       headers={"X-Hestia-Memory": "1"}).status_code == 409   # a form post
    assert client.post("/api/memory/inbox/middle/discard", content=json.dumps({}),
                       headers={"Content-Type": "application/json"}).status_code == 409
    assert (queue / "middle.md").exists() and not _records(mem)


def test_an_id_is_never_a_path(client, mem, tmp_path):
    (tmp_path / "secret.md").write_text("---\nid: secret\ntype: household\n---\nnot a proposal\n")
    for rid in ("..%2Fsecret", "../secret", "MIDDLE", "middle.md"):
        assert _keep(client, rid).status_code in (404, 405), rid
    assert (tmp_path / "secret.md").exists() and not _records(mem)


def test_the_terminal_and_the_app_share_one_path(queue, mem):
    review_notes.promote("newest")
    with pytest.raises(LookupError):
        review_notes.discard("newest")
    assert review_notes.main(["discard", "oldest"]) == 0 and not (queue / "oldest.md").exists()
