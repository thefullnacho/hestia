import datetime as dt
import json

import pytest

import board

TODAY = dt.date(2026, 9, 26)


@pytest.fixture(autouse=True)
def _later_state(tmp_path, monkeypatch):
    """Keep and Later write a state file: never the real one the live board reads."""
    monkeypatch.setattr(board.config, "BOARD_LATER_STATE", tmp_path / "later.json")

QUEUE = """# queue

## Open

| Added | Project | Item | Days open | Done |
|---|---|---|---|---|
| 2026-09-01 | hestia | **12:00 or 15:00: reply in the forum thread** about languages | 0 | |
| 2026-09-20 | garden | **Morning, greenhouse: check the marigold seed bag** for the type | 0 | |
| 2026-09-20 | garden | **Morning: pot the rosemary** | 0 | 2026-09-21 (done) |
| 2026-09-20 | blog | **12:00 or 15:00 on or after 2026-10-04: check the submission** | 0 | |
| 2026-09-20 | blog | **Late November, morning: photograph the winterizing** | 0 | |
| 2026-08-10 | hestia | Tailscale ACL restricting port 8730 — own devices only | 0 | |

## Shipped

| Done | Project | What |
|---|---|---|
| 2026-09-02 | hestia | **Morning: something finished** |
"""


def test_queue_reads_only_open_unfinished_current_rows():
    items = board.queue_items(QUEUE, TODAY)
    titles = [i["title"] for i in items]
    assert titles == ["Tailscale ACL restricting port 8730",
                      "Reply in the forum thread",
                      "Check the marigold seed bag"]


def test_slot_prefix_is_stripped_and_column_follows_it():
    items = {i["title"]: i for i in board.queue_items(QUEUE, TODAY)}
    assert items["Reply in the forum thread"]["column"] == "screen"
    assert items["Check the marigold seed bag"]["column"] == "hands"
    assert items["Tailscale ACL restricting port 8730"]["column"] == "screen"


def test_age_is_counted_from_added():
    items = board.queue_items(QUEUE, TODAY)
    assert items[0]["age"] == 47


def test_deferred_rows_come_back_on_their_date():
    later = board.queue_items(QUEUE, dt.date(2026, 11, 20))
    titles = [i["title"] for i in later]
    assert "Check the submission" in titles
    assert "Photograph the winterizing" in titles


def test_hands_only_means_nobody_else_not_physical():
    assert board.column_of("Set up sudo on the boxes. Root on each, so it is hands-only",
                           "Set up sudo on the boxes") == "screen"


def test_mood_follows_the_most_urgent_home_item():
    noon = dt.datetime(2026, 9, 26, 12, 0)
    night = dt.datetime(2026, 9, 26, 23, 0)
    urgent = [{"title": "Box: no reading", "level": 2}]
    today = [{"title": "Weigh the pups", "level": 1}]
    assert board.mood(urgent, night) == "concerned"   # urgent wakes it
    assert board.mood(today, noon) == "attentive"
    assert board.mood(today, night) == "asleep"
    assert board.mood([], noon) == "content"
    assert board.headline(urgent, "concerned") == "Box: no reading"


def test_render_sizes_for_the_kindle_panel():
    now = dt.datetime(2026, 9, 26, 9, 0)
    img = board.render(board.queue_items(QUEUE, TODAY),
                       [{"title": "Box 84°F", "sub": "in band", "level": 0}], now)
    assert img.size == board.SIZE
    kindle = board.for_device(img, "kindle")
    assert kindle.size == (758, 1024)
    assert {v for _, v in kindle.getcolors(256)} <= {v * 17 for v in range(16)}


def test_close_queue_row_moves_it_to_shipped_in_the_shipped_shape(tmp_path):
    q = tmp_path / "queue.md"
    q.write_text(QUEUE)
    row = next(i for i in board.queue_items(QUEUE, TODAY) if i["title"] == "Check the marigold seed bag")
    assert board.close_queue_row(row["line"], TODAY, q) == "Check the marigold seed bag"
    text = q.read_text()
    assert row["line"] not in text
    shipped = text.split("## Shipped")[1]
    assert "| 2026-09-26 | garden | Check the marigold seed bag (closed from the board) |" in shipped
    # a stale frame can't close a row that already moved
    try:
        board.close_queue_row(row["line"], TODAY, q)
        raise AssertionError("closed a row that was gone")
    except ValueError:
        pass


def test_memory_column_appears_only_with_proposals_and_buttons_hit_first():
    now = dt.datetime(2026, 9, 26, 9, 0)
    mem = [{"title": "Bodhi is the sire", "sub": "episodic", "level": 1, "key": "memory:x",
            "memory": "x", "detail": "Bodhi is the sire.", "choices": board.MEMORY_CHOICES}]
    hits = []
    board.render([], [], now, hits=hits)
    assert hits == []
    board.render([], [], now, selected="memory:x", note="Bodhi is the sire.", hits=hits, memory=mem)
    assert [h["item"]["key"] for h in hits[:2]] == ["choice:promote", "choice:discard"]
    assert hits[2]["item"]["key"] == "memory:x"


def test_old_rows_ask_keep_done_trash_and_keep_snoozes(tmp_path, monkeypatch):
    q = tmp_path / "queue.md"
    q.write_text(QUEUE)
    monkeypatch.setattr(board, "BOARD_QUEUE", q)
    monkeypatch.setattr(board.config, "BOARD_REVIEW_STATE", tmp_path / "review.json")
    items = {i["title"]: i for i in board.read_queue(TODAY)}
    old, young = items["Tailscale ACL restricting port 8730"], items["Check the marigold seed bag"]
    assert old["choices"] == board.QUEUE_CHOICES and young["choices"] == board.LATER_CHOICES
    assert (old["status"], young["status"]) == ("review", "open")

    now = dt.datetime(2026, 9, 26, 9, 0)
    assert board.complete(old, "keep", now).startswith("Kept")
    kept = {i["title"]: i for i in board.read_queue(TODAY)}["Tailscale ACL restricting port 8730"]
    assert kept["choices"] == board.LATER_CHOICES and kept["status"] == "later"  # not asked again yet
    later = TODAY + dt.timedelta(days=board.REVIEW_DAYS)
    back = {i["title"]: i for i in board.read_queue(later)}["Tailscale ACL restricting port 8730"]
    assert back["choices"] == board.QUEUE_CHOICES and "later" not in back  # the question and the place return together


def test_trash_deletes_without_a_shipped_entry(tmp_path, monkeypatch):
    q = tmp_path / "queue.md"
    q.write_text(QUEUE)
    monkeypatch.setattr(board, "BOARD_QUEUE", q)
    monkeypatch.setattr(board.config, "BOARD_REVIEW_STATE", tmp_path / "review.json")
    monkeypatch.setattr(board, "TRASH_LOG", tmp_path / "trashed.tsv")
    old = {i["title"]: i for i in board.read_queue(TODAY)}["Tailscale ACL restricting port 8730"]
    board.complete(old, "trash", dt.datetime(2026, 9, 26, 9, 0))
    assert old["line"] in (tmp_path / "trashed.tsv").read_text()  # a misfire can be put back
    text = q.read_text()
    assert old["line"] not in text and "Tailscale" not in text.split("## Shipped")[1]


def test_queue_page_is_read_only_and_escapes(tmp_path, monkeypatch):
    q = tmp_path / "queue.md"
    q.write_text(QUEUE.replace("pot the rosemary", "pot <b>rosemary</b>"))
    monkeypatch.setattr(board, "BOARD_QUEUE", q)
    monkeypatch.setattr(board.config, "BOARD_REVIEW_STATE", tmp_path / "review.json")
    monkeypatch.setattr(board, "home_items", lambda now: [{"title": "Box <hot>", "sub": "day 5", "level": 2}])
    monkeypatch.setattr(board, "read_memory", lambda: [])
    page = board.queue_page(dt.datetime(2026, 9, 26, 9, 0).astimezone())
    assert "Box &lt;hot&gt;" in page and "<form" not in page and "<button" not in page
    assert "MEMORY" not in page  # empty inbox, no section
    snap = board.snapshot(dt.datetime(2026, 9, 26, 9, 0).astimezone())
    assert [r["title"] for r in snap["columns"]["screen"]][:1] == ["Tailscale ACL restricting port 8730"]


def test_ids_are_stable_across_edits_to_the_rest_of_the_row():
    a = board.queue_items(QUEUE, TODAY)
    edited = QUEUE.replace("for the type", "for the type and the sprout days")
    b = board.queue_items(edited, TODAY)
    ids = lambda rows: {r["title"]: r["id"] for r in rows}
    assert ids(a) == ids(b)
    assert len(set(ids(a).values())) == len(a)


def test_act_on_queue_by_id_and_status(tmp_path, monkeypatch):
    q = tmp_path / "queue.md"
    q.write_text(QUEUE)
    monkeypatch.setattr(board, "BOARD_QUEUE", q)
    monkeypatch.setattr(board.config, "BOARD_REVIEW_STATE", tmp_path / "review.json")
    now = dt.datetime(2026, 9, 26, 9, 0)
    rows = {r["title"]: r for r in board.read_queue(TODAY)}
    assert rows["Tailscale ACL restricting port 8730"]["status"] == "review"
    assert rows["Check the marigold seed bag"]["status"] == "open"
    assert board.act_on_queue(rows["Check the marigold seed bag"]["id"], "done", now).startswith("Done")
    try:
        board.act_on_queue(rows["Check the marigold seed bag"]["id"], "done", now)
        raise AssertionError("closed twice")
    except LookupError:
        pass
    try:
        board.act_on_queue(rows["Tailscale ACL restricting port 8730"]["id"], "delete", now)
        raise AssertionError("accepted an unknown action")
    except ValueError:
        pass


def test_page_shows_done_buttons_only_with_a_token(tmp_path, monkeypatch):
    q = tmp_path / "queue.md"
    q.write_text(QUEUE)
    monkeypatch.setattr(board, "BOARD_QUEUE", q)
    monkeypatch.setattr(board.config, "BOARD_REVIEW_STATE", tmp_path / "review.json")
    monkeypatch.setattr(board, "home_items", lambda now: [])
    monkeypatch.setattr(board, "read_memory", lambda: [])
    now = dt.datetime(2026, 9, 26, 9, 0).astimezone()
    assert "<button" not in board.queue_page(now)
    assert board.queue_page(now, token="t").count("<button") == 3


# ── later and paging ─────────────────────────────────────────────────────────────────────

NOW = dt.datetime(2026, 9, 26, 9, 0)


def _queue(n):
    """n open rows, Job n the oldest at n days and Job 1 the newest, all under the review age."""
    rows = "\n".join(f"| {(TODAY - dt.timedelta(days=i)).isoformat()} | p | **Job {i}** | 0 | |"
                     for i in range(n, 0, -1))
    return f"## Open\n\n| Added | Project | Item | Days open | Done |\n|---|---|---|---|---|\n{rows}\n\n## Shipped\n\n| Done | Project | What |\n|---|---|---|\n"


def _scratch(tmp_path, monkeypatch, text):
    q = tmp_path / "queue.md"
    q.write_text(text)
    monkeypatch.setattr(board, "BOARD_QUEUE", q)
    monkeypatch.setattr(board.config, "BOARD_REVIEW_STATE", tmp_path / "review.json")
    return q


def test_later_serves_a_row_last_and_never_touches_the_queue_file(tmp_path, monkeypatch):
    q = _scratch(tmp_path, monkeypatch, _queue(5))
    before = q.read_text()
    rows = board.read_queue(TODAY)
    assert [r["title"] for r in rows] == ["Job 5", "Job 4", "Job 3", "Job 2", "Job 1"]
    assert board.complete(rows[0], "later", NOW) == "Later: Job 5. Back of the queue until Oct 3"
    served = board.read_queue(TODAY)
    assert [r["title"] for r in served] == ["Job 4", "Job 3", "Job 2", "Job 1", "Job 5"]
    assert (served[0]["status"], served[-1]["status"]) == ("open", "later")
    # waved past again: behind the rest, and oldest-first among the waved
    board.complete(served[0], "later", NOW)
    assert [r["title"] for r in board.read_queue(TODAY)] == ["Job 3", "Job 2", "Job 1", "Job 5", "Job 4"]
    assert q.read_text() == before  # the table is the operator's: only the state file knows
    # and the date runs out: oldest-first again
    spent = TODAY + dt.timedelta(days=board.LATER_DAYS)
    assert [r["title"] for r in board.read_queue(spent)][:2] == ["Job 5", "Job 4"]


def test_spent_and_garbled_later_entries_are_dropped(tmp_path):
    path = tmp_path / "later.json"
    path.write_text(json.dumps({"a": "2026-09-25", "b": "2026-09-27", "c": "nonsense", "d": 7}))
    assert board._laters(TODAY) == {"b": "2026-09-27"}
    board.later_row("e", TODAY)
    assert set(json.loads(path.read_text())) == {"b", "e"}  # rewritten whole, spent ones gone
    path.write_text("[1, 2]")
    assert board._laters(TODAY) == {}
    path.write_text("{not json")
    assert board._laters(TODAY) == {}


def test_later_by_id_for_the_phone_and_snapshot_says_so(tmp_path, monkeypatch):
    _scratch(tmp_path, monkeypatch, _queue(3))
    monkeypatch.setattr(board, "home_items", lambda now: [])
    monkeypatch.setattr(board, "read_memory", lambda: [])
    rows = board.read_queue(TODAY)
    assert board.act_on_queue(rows[0]["id"], "later", NOW).startswith("Later: Job 3")
    last = board.snapshot(NOW.astimezone())["columns"]["screen"][-1]
    assert (last["title"], last["status"]) == ("Job 3", "later") and last["sub"].endswith("· later")


def test_paginate_fits_one_page_or_reserves_room_on_every_page():
    assert board._paginate([88] * 5, 440) == [[0, 1, 2, 3, 4]]
    assert board._paginate([], 440) == [[]]
    pages = board._paginate([88] * 12, 440)
    assert [i for p in pages for i in p] == list(range(12))  # every row, in order, once
    assert len(pages) == 3 and all(88 * len(p) <= 440 - board.PAGE_RESERVE for p in pages)
    assert board._paginate([900, 50], 440) == [[0], [1]]     # a page always holds something


def test_a_long_column_is_paged_oldest_first_and_its_foot_turns_the_page(tmp_path, monkeypatch):
    _scratch(tmp_path, monkeypatch, _queue(13))
    rows = board.read_queue(TODAY)
    front, second, short, stale = [], [], [], []
    board.render(rows, [], NOW, hits=front)
    board.render(rows, [], NOW, hits=second, pages={"SCREEN": 1})
    keys = lambda hits: [h["item"]["key"] for h in hits if h["item"].get("key", "").startswith("queue:")]
    assert keys(front) + keys(second) == [r["key"] for r in rows]  # each row on one page, oldest first
    foot = next(h["item"] for h in front if h["item"].get("page"))
    assert (foot["page"], foot["at"], foot["pages"]) == ("SCREEN", 0, 2)
    board.render(rows[:3], [], NOW, hits=short)
    assert not any(h["item"].get("page") for h in short)           # no foot when it all fits
    board.render(rows, [], NOW, hits=stale, pages={"SCREEN": 9})
    assert keys(stale) == keys(second)                             # a page that is gone shows the last


def test_the_selection_band_hides_the_page_feet_it_covers(tmp_path, monkeypatch):
    _scratch(tmp_path, monkeypatch, _queue(13))
    rows = board.read_queue(TODAY)
    hits = []
    board.render(rows, [], NOW, selected=rows[0]["key"], note=rows[0]["detail"], hits=hits)
    assert not any(h["item"].get("page") for h in hits)
    assert hits[0]["item"]["choice"] == "later"


# ── rhythm prefixes ("Peak", "Off-peak") are a slot, not part of the title ───────────────

def test_a_rhythm_prefix_is_stripped_from_the_title_in_every_shape_it_is_written():
    cases = {
        "**Peak, 30 min: write the about page.** Some detail after it.": "Write the about page",
        "**Off-peak, 2 min, voice or phone: log one thing.** More detail.": "Log one thing",
        "**Off peak, no rush, after a few days of use: decide on writes.**": "Decide on writes",
        "**peak: build the box**": "Build the box",
        "**OFF-PEAK, 5 min: check the sensor**": "Check the sensor",
        "Off-peak, 5 min: reply to the thread. More words after the stop.": "Reply to the thread",
    }
    for item, title in cases.items():
        assert board.title_of(item) == title, item


def test_a_title_that_merely_starts_with_the_word_is_left_alone():
    assert board.title_of("**Peak District trip: plan the route**") == "Peak District trip: plan the route"
    assert board.title_of("**Peaking early: tune the sensor**") == "Peaking early: tune the sensor"
    assert board.title_of("**Peak** is the word we use for the window") == "Peak"  # nothing after it to strip


def test_the_older_slot_prefixes_still_strip():
    assert board.title_of("**Morning, hands: pot the rosemary**") == "Pot the rosemary"
    assert board.title_of("**12:00 or 15:00: reply in the thread**") == "Reply in the thread"
    assert board.title_of("**Late November, morning: photograph the winterizing**") == "Photograph the winterizing"


def test_the_rhythm_word_does_not_pick_the_column_the_verb_and_the_job_do():
    text = """## Open

| Added | Project | Item | Days open | Done |
|---|---|---|---|---|
| 2026-09-20 | p | **Off-peak, 5 min: decide on the write mode** | - | |
| 2026-09-20 | p | **Peak, 20 min: pot the rosemary** | - | |
| 2026-09-20 | p | **Off-peak, 2 min: pot the cuttings** | - | |
| 2026-09-20 | p | **Peak, 20 min: reply to the forum thread** | - | |
"""
    cols = {i["title"]: i["column"] for i in board.queue_items(text, TODAY)}
    assert cols == {"Decide on the write mode": "screen", "Pot the rosemary": "hands",
                    "Pot the cuttings": "hands", "Reply to the forum thread": "screen"}
