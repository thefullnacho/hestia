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
