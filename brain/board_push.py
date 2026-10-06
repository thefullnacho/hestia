"""Board push: draw the board on the Kindle and listen for taps, over SSH, from this box.

The brain listens on the tailnet only and the Kindle lives on the LAN, so the Kindle never
calls in. This box calls out: a long-lived SSH session streams the touch panel's raw events
up, and each frame goes down in its own short session (multiplexed over one connection). The
Kindle stays a dumb panel and the brain's network boundary does not move.

Each push also takes the screen: it stops the stock Kindle UI (whose status bar otherwise
draws over the board) and holds off the screensaver. Both are idempotent and both come back
on any reboot, so a restarted Kindle is simply reclaimed on the next push.

Taps, deterministic end to end:
  - tap an item        → it is drawn inverted, and the foot of the board says what a second
                         tap will do (or, for a watch alert, the full reason)
  - tap it again       → within CONFIRM_S it is closed: a queue row moves to Shipped, a due
                         asset logs a service. Watch alerts can't be closed, only read.
  - tap anywhere else  → the selection clears; with nothing selected, it just redraws now
  - Later / Keep       → a queue row stays open and goes to the back of the served order for a
                         while (BOARD_LATER_DAYS, or REVIEW_DAYS for Keep), so a reminder
                         already seen stops holding the front page. Nothing is written to the queue
  - tap a column's foot → a column too long for the screen is paged, oldest first; the foot turns
                         to the next page and the last page turns back to the first. Pages snap
                         back to the front PAGE_S after the last tap

Refresh: e-ink ghosts under partial updates, so a frame is a quiet partial refresh except the
first of each hour, which flashes the panel clean.

`BOARD_KINDLE_HOST` is an SSH host alias (default `kindle-board`) that lives in the
operator's ~/.ssh/config with its key and address, so no LAN address is written here.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import selectors
import struct
import subprocess
import sys
import time

import config  # puts brain/ on sys.path + owns paths

config.load_secrets()
import board  # noqa: E402

HOST = os.environ.get("BOARD_KINDLE_HOST", "kindle-board")
STATE_PATH = config.BOARD_PUSH_STATE
TIMEOUT_S = int(os.environ.get("BOARD_PUSH_TIMEOUT_S", "45"))
EVERY_S = int(os.environ.get("BOARD_PUSH_EVERY_S", "300"))   # ambient, not an alarm
RETRY_S = int(os.environ.get("BOARD_RETRY_S", "30"))         # while the Kindle is dark
CONFIRM_S = int(os.environ.get("BOARD_CONFIRM_S", "60"))     # a selection this old is dropped
NOTE_S = int(os.environ.get("BOARD_NOTE_S", "20"))           # how long "Done: …" stays up
PAGE_S = int(os.environ.get("BOARD_PAGE_S", "90"))           # a turned page returns to the front
TOUCH_DEV = "/dev/input/event0"                               # cyttsp on the Paperwhite 1

# ServerAlive on every session: a WiFi drop must kill a dead connection in under a minute,
# not leave a push or the shared master hanging on it.
_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        "-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/cm-board-%r@%h:%p",
        "-o", "ControlPersist=600"]

# Runs on the Kindle's busybox sh. /tmp is tmpfs, so frames don't wear the flash.
_REMOTE = r"""
export PATH="$PATH:/sbin:/usr/sbin:/mnt/us/usbnet/bin"
cat > /tmp/board.png || exit 3
if initctl status lab126_gui 2>/dev/null | grep -q running; then
    stop lab126_gui >/dev/null 2>&1
    sleep 3
    FLASH=1
fi
lipc-set-prop com.lab126.powerd preventScreenSaver 1 >/dev/null 2>&1
if [ "$FLASH" = 1 ] || [ "{flash}" = 1 ]; then
    fbink -q -f -c -g file=/tmp/board.png
else
    fbink -q -g file=/tmp/board.png
fi
"""

# struct input_event on 32-bit ARM: timeval (2 x u32), type u16, code u16, value s32
_EVENT = struct.Struct("<IIHHi")
EV_KEY, EV_ABS, BTN_TOUCH = 1, 3, 0x14A
ABS_X, ABS_Y, ABS_MT_X, ABS_MT_Y = 0x00, 0x01, 0x35, 0x36


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def should_flash(prev: dict, now: dt.datetime) -> bool:
    """The first push in each hour clears the ghosting that partial refreshes leave behind."""
    return prev.get("hour") != now.strftime("%Y-%m-%dT%H")


def push(png: bytes, flash: bool) -> subprocess.CompletedProcess:
    return subprocess.run([*_SSH, HOST, _REMOTE.replace("{flash}", "1" if flash else "0")],
                          input=png, capture_output=True, timeout=TIMEOUT_S)


class TouchParser:
    """Raw panel events in, completed taps (panel coordinates) out. A tap is the finger
    lifting: BTN_TOUCH going to 0, at the last position reported."""

    def __init__(self) -> None:
        self.buf = b""
        self.x = self.y = None

    def feed(self, data: bytes) -> list[tuple[int, int]]:
        self.buf += data
        taps = []
        while len(self.buf) >= _EVENT.size:
            _, _, etype, code, value = _EVENT.unpack_from(self.buf)
            self.buf = self.buf[_EVENT.size:]
            if etype == EV_ABS and code in (ABS_X, ABS_MT_X):
                self.x = value
            elif etype == EV_ABS and code in (ABS_Y, ABS_MT_Y):
                self.y = value
            elif etype == EV_KEY and code == BTN_TOUCH and value == 0 and self.x is not None:
                taps.append((self.x, self.y))
        return taps


class Board:
    """What is on the panel right now, and what a tap on it means."""

    def __init__(self, send=push, render=board.frame, close=board.complete) -> None:
        self.send, self.render, self.close = send, render, close
        self.hits: list[dict] = []
        self.selected: dict | None = None
        self.selected_at = 0.0
        self.note: str | None = None
        self.note_at = 0.0
        self.pages: dict[str, int] = {}   # column label -> the page it shows; absent is the front
        self.page_at = 0.0

    def draw(self, now: dt.datetime | None = None, force: bool = False) -> str:
        now = now or dt.datetime.now().astimezone()
        png, hits = self.render("kindle", now, self.selected and self.selected["key"], self.note,
                                self.pages)
        digest = hashlib.sha256(png).hexdigest()
        prev = _load_state()
        flash = should_flash(prev, now)
        if digest == prev.get("digest") and not flash and not force:
            self.hits = hits
            return "unchanged"
        try:
            result = self.send(png, flash)
        except subprocess.TimeoutExpired:
            return "kindle did not answer"
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip().splitlines()
            return f"push failed ({result.returncode}): {err[-1] if err else 'no output'}"
        self.hits = hits
        _save_state({"digest": digest, "hour": now.strftime("%Y-%m-%dT%H"), "at": now.isoformat()})
        return "flashed" if flash else "drawn"

    def expire(self, clock: float) -> bool:
        """Drop a selection nobody confirmed, a result line once it has been read, and turned
        pages once nobody is touching them. True when the board needs a redraw."""
        stale = False
        if self.selected and clock - self.selected_at > CONFIRM_S:
            self.selected, self.note, stale = None, None, True
        elif not self.selected and self.note and clock - self.note_at > NOTE_S:
            self.note, stale = None, True
        if self.pages and clock - self.page_at > PAGE_S:
            self.pages, stale = {}, True
        return stale

    def next_expiry(self) -> float | None:
        due = []
        if self.selected:
            due.append(self.selected_at + CONFIRM_S + 0.5)
        elif self.note:
            due.append(self.note_at + NOTE_S + 0.5)
        if self.pages:
            due.append(self.page_at + PAGE_S + 0.5)
        return min(due) if due else None

    def tap(self, bx: int, by: int, clock: float) -> str:
        self.note_at = clock
        if self.pages:  # still working through the pages: the front is not coming back yet
            self.page_at = clock
        item = board.hit_at(self.hits, bx, by)
        if item and item.get("page"):  # a column's foot: turn it, wrapping to the first page
            self.pages[item["page"]] = (item["at"] + 1) % item["pages"]
            self.page_at = clock
            self.selected, self.note = None, None  # the rows under a selection just moved
            return f"page {item['page']} {self.pages[item['page']] + 1}/{item['pages']}"
        if item and item.get("choice"):  # a button in the band: the answer, no second tap
            target, self.selected = item["target"], None
            try:
                self.note = self.close(target, item["choice"])
            except Exception as e:  # noqa: BLE001 — say what went wrong, on the board
                self.note = f"Couldn't do that: {e}"
            return f"{item['choice']} {target['key'][:60]!r}: {self.note}"
        if self.selected and item and item["key"] == self.selected["key"] \
                and clock - self.selected_at <= CONFIRM_S:
            self.selected = None
            if item.get("done"):
                try:
                    self.note = self.close(item)
                except Exception as e:  # noqa: BLE001 — say what went wrong, on the board
                    self.note = f"Couldn't close it: {e}"
            else:
                self.note = None
            return f"confirm {item['key'][:60]!r}: {self.note}"
        if item:
            self.selected, self.selected_at = item, clock
            self.note = (item["detail"] if item.get("choices")
                         else "Tap again to mark it done" if item.get("done")
                         else item.get("detail") or item.get("sub") or item["title"])
            return f"select {item['key'][:60]!r}"
        self.selected, self.note = None, None
        return "clear"


def _open_stream() -> subprocess.Popen:
    return subprocess.Popen([*_SSH, HOST, f"exec cat {TOUCH_DEV}"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


class Link:
    """Whether the Kindle is reachable, logged only when that changes. A night of refusals is
    one line going dark and one coming back, not a hundred identical ones."""

    def __init__(self) -> None:
        self.dark_since: float | None = None

    def result(self, outcome: str, clock: float) -> bool:
        ok = outcome in ("drawn", "flashed", "unchanged")
        if ok and self.dark_since is not None:
            print(f"board-push: kindle back after {int(clock - self.dark_since)}s, {outcome}", flush=True)
            self.dark_since = None
        elif not ok and self.dark_since is None:
            print(f"board-push: kindle dark: {outcome}", flush=True)
            self.dark_since = clock
        elif ok and outcome != "unchanged":
            print(f"board-push: {outcome}", flush=True)
        return ok


def serve() -> int:
    b, parser, link = Board(), TouchParser(), Link()
    stream, sel = None, selectors.DefaultSelector()
    next_draw, retry_at = 0.0, 0.0
    while True:
        clock = time.monotonic()
        if stream is None and clock >= retry_at:
            stream = _open_stream()
            sel.register(stream.stdout, selectors.EVENT_READ)
            parser = TouchParser()
        if clock >= next_draw or b.expire(clock):
            # a dark Kindle is retried every RETRY_S, so a reboot is reclaimed within a minute
            ok = link.result(b.draw(force=link.dark_since is not None), clock)
            next_draw = clock + (EVERY_S if ok else RETRY_S)
        deadlines = [next_draw, b.next_expiry() or next_draw]
        if stream is None:
            deadlines.append(retry_at)
        wait = max(0.5, min(deadlines) - clock)
        for key, _ in sel.select(timeout=wait):
            data = os.read(key.fileobj.fileno(), 4096)
            if not data:  # the Kindle went away (reboot, WiFi, USB mode): reconnect shortly
                sel.unregister(stream.stdout)
                stream.kill()
                stream.wait()
                stream, retry_at = None, time.monotonic() + RETRY_S
                break
            for px, py in parser.feed(data):
                print(f"board-push: tap {board.from_panel(px, py)} -> "
                      f"{b.tap(*board.from_panel(px, py), time.monotonic())}", flush=True)
                print(f"board-push: {b.draw(force=True)}", flush=True)


def main() -> int:
    if "--once" in sys.argv:
        print(f"board-push: {Board().draw()}")
        return 0
    return serve()


if __name__ == "__main__":
    sys.exit(main())
