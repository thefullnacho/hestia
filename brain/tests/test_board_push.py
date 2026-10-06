import datetime as dt
import struct
import subprocess

import board
import board_push

NOW = dt.datetime(2026, 9, 26, 22, 50)


def ev(etype, code, value):
    return struct.pack("<IIHHi", 0, 0, etype, code, value)


def test_touch_parser_reports_a_tap_where_the_finger_lifts():
    p = board_push.TouchParser()
    stream = (ev(3, 0x35, 50) + ev(3, 0x36, 951) + ev(1, 0x14A, 1)
              + ev(3, 0x35, 49) + ev(3, 0x36, 942) + ev(1, 0x14A, 0))
    # delivered in awkward chunks, as a pipe would
    assert p.feed(stream[:21]) == []
    assert p.feed(stream[21:]) == [(49, 942)]


def test_panel_coordinates_map_onto_the_rotated_board():
    # the three calibration taps from the real panel: top-left, bottom-right, the HOME label
    assert board.from_panel(50, 951) == (73, 50)
    assert board.from_panel(712, 48) == (976, 712)
    bx, by = board.from_panel(167, 313)
    assert 688 <= bx <= 780 and 150 <= by <= 180


class FakeBoard(board_push.Board):
    def __init__(self, items, rc=0):
        self.sent = []
        self.closed = []

        def render(device, now, selected, note, pages=None):
            self.frame = (selected, note)
            self.frame_pages = dict(pages or {})
            # a page foot reports the page the board is showing, as the real render does
            hits = [{"box": (0, 100 * i, 300, 100 * i + 90),
                     "item": {**it, "at": (pages or {}).get(it["page"], 0)} if it.get("page") else it}
                    for i, it in enumerate(items)]
            return repr((selected, note, pages)).encode(), hits

        def send(png, flash):
            self.sent.append(flash)
            return subprocess.CompletedProcess([], rc, b"", b"ssh: connect refused")

        def close(item, choice=None):
            self.closed.append((item["key"], choice) if choice else item["key"])
            return f"Done: {item['title']}"
        super().__init__(send=send, render=render, close=close)


ITEMS = [{"key": "queue:a", "title": "Seed bag", "done": "queue"},
         {"key": "home:b", "title": "Hot Peppers not reporting", "detail": "Soil sensor Hot Peppers is not reporting."}]


def test_select_then_confirm_closes_the_item(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard(ITEMS)
    b.draw(NOW)
    assert b.tap(10, 10, 100.0).startswith("select")
    assert b.note == "Tap again to mark it done"
    b.tap(10, 10, 105.0)
    assert b.closed == ["queue:a"] and b.note == "Done: Seed bag" and b.selected is None


def test_confirm_after_the_window_selects_again_instead_of_closing(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard(ITEMS)
    b.draw(NOW)
    b.tap(10, 10, 100.0)
    b.tap(10, 10, 100.0 + board_push.CONFIRM_S + 1)
    assert b.closed == [] and b.selected["key"] == "queue:a"


def test_alerts_explain_themselves_and_never_close(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard(ITEMS)
    b.draw(NOW)
    b.tap(10, 110, 100.0)
    assert b.note == "Soil sensor Hot Peppers is not reporting."
    b.tap(10, 110, 101.0)
    assert b.closed == [] and b.note is None


def test_tap_elsewhere_clears_and_stale_selection_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard(ITEMS)
    b.draw(NOW)
    b.tap(10, 10, 100.0)
    assert b.tap(900, 700, 101.0) == "clear" and b.selected is None
    b.tap(10, 10, 200.0)
    assert not b.expire(210.0)
    assert b.expire(200.0 + board_push.CONFIRM_S + 1) and b.selected is None


def test_hourly_flash_and_unchanged_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard(ITEMS)
    assert b.draw(NOW) == "flashed"
    assert b.draw(NOW + dt.timedelta(minutes=5)) == "unchanged"
    assert b.draw(NOW + dt.timedelta(minutes=5), force=True) == "drawn"
    assert b.draw(NOW + dt.timedelta(hours=1)) == "flashed"
    assert b.sent == [True, False, True]


def test_failed_push_is_not_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard(ITEMS, rc=255)
    assert b.draw(NOW).startswith("push failed (255)")
    assert not (tmp_path / "s.json").exists()


def test_memory_proposal_answers_with_a_button_not_a_second_tap(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    mem = {"key": "memory:x", "title": "Bodhi is the sire", "detail": "Bodhi is the sire.",
           "memory": "x", "choices": board.MEMORY_CHOICES}
    b = FakeBoard([mem])
    b.draw(NOW)
    b.tap(10, 10, 100.0)
    assert b.note == "Bodhi is the sire."
    # the band's buttons land in the hit map ahead of the items
    b.hits.insert(0, {"box": (800, 700, 950, 756), "item": {"key": "choice:discard", "choice": "discard",
                                                           "target": mem, "title": "Discard"}})
    b.tap(870, 720, 101.0)
    assert b.closed == [("memory:x", "discard")] and b.selected is None


def test_link_logs_going_dark_and_coming_back_once(capsys):
    link = board_push.Link()
    assert link.result("drawn", 0) is True
    assert not link.result("push failed (255): refused", 10)
    assert not link.result("push failed (255): refused", 40)
    assert not link.result("kindle did not answer", 70)
    assert link.result("flashed", 100)
    out = capsys.readouterr().out.splitlines()
    assert out == ["board-push: drawn",
                   "board-push: kindle dark: push failed (255): refused",
                   "board-push: kindle back after 90s, flashed"]


FOOT = {"key": "page:SCREEN", "page": "SCREEN", "at": 0, "pages": 3, "title": "more"}


def test_a_column_foot_turns_the_page_wraps_and_clears_the_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard([ITEMS[0], FOOT])
    b.draw(NOW)
    b.tap(10, 10, 100.0)
    assert b.selected is not None
    assert b.tap(10, 110, 101.0) == "page SCREEN 2/3"
    assert b.pages == {"SCREEN": 1} and b.selected is None and b.note is None
    b.draw(NOW, force=True)
    assert b.frame_pages == {"SCREEN": 1}  # the next frame is drawn from the turned page
    b.tap(10, 110, 102.0)
    b.draw(NOW, force=True)
    assert b.tap(10, 110, 103.0) == "page SCREEN 1/3"  # the last page turns back to the first
    assert b.pages == {"SCREEN": 0} and b.closed == []


def test_turned_pages_snap_back_after_a_quiet_spell_and_taps_keep_them(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    b = FakeBoard([ITEMS[0], FOOT])
    b.draw(NOW)
    b.tap(10, 110, 100.0)
    assert b.next_expiry() == 100.0 + board_push.PAGE_S + 0.5
    b.tap(10, 10, 100.0 + board_push.PAGE_S - 5)  # still working on that page: the timer restarts
    assert not b.expire(100.0 + board_push.PAGE_S + 1)
    assert b.expire(100.0 + 2 * board_push.PAGE_S) and b.pages == {}


def test_later_button_answers_without_a_second_tap(monkeypatch, tmp_path):
    monkeypatch.setattr(board_push, "STATE_PATH", tmp_path / "s.json")
    row = {"key": "queue:a", "title": "Seed bag", "done": "queue", "choices": board.LATER_CHOICES,
           "detail": "Seed bag. Tap again to mark it done, or send it Later."}
    b = FakeBoard([row])
    b.draw(NOW)
    b.tap(10, 10, 100.0)
    assert b.note == row["detail"]
    b.hits.insert(0, {"box": (800, 700, 950, 756), "item": {"key": "choice:later", "choice": "later",
                                                           "target": row, "title": "Later"}})
    b.tap(870, 720, 101.0)
    assert b.closed == [("queue:a", "later")] and b.selected is None
