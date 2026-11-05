"""The box's end of the phone: the household's model, reached over a socket the phone opens.

PHONE-APP.md has the design. The app dials the box when it comes to the foreground; the box
sends prompt text down that socket and the phone streams tokens back. The phone never listens,
never sees messages or tools (chat_format.py renders and parses on the box), and only runs while
the app is open, so model work that a timer starts waits in model_jobs until a phone connects.

Whatever answers on this socket sees every prompt and writes every tool call, so unlike the rest
of the brain it takes a device token, issued here and kept in the phone's Keychain:

    python phone.py add-device <name>     # prints a token once; only its hash is stored
    python phone.py tie <name> <person>   # this phone gets <person>'s reminders ("-" unties it)
    python phone.py list                  # devices, who each is tied to, and the model file

Wire, one JSON object per WebSocket message:
    phone -> box  {"type": "hello", "device": "...", "model_sha256": "...",
                   "push_token": "<APNs device token, hex>"?}   (push.py: the content-free ping)
    box -> phone  {"type": "ready", "model_sha256": "...", "model_ok": true|false}
    box -> phone  {"type": "complete", "id": "...", "prompt": "...", "n_predict": N,
                   "temperature": T, "stop": [...], "json_schema": {...}?}
    phone -> box  {"type": "delta", "id": "...", "text": "..."}            (zero or more)
    phone -> box  {"type": "done", "id": "...", "stop_type": "eos"|"limit"|"word",
                   "tokens_evaluated": N, "tokens_predicted": N}
    phone -> box  {"type": "error", "id": "...", "message": "..."}
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import json
import os
import secrets
import sys
from pathlib import Path

import config

DEVICES = config.DATA_DIR / "phone_devices.json"           # {name: sha256 of its token}
PEOPLE = config.DATA_DIR / "phone_people.json"             # {device name: person it is tied to}
MODEL_FILE = os.environ.get("HESTIA_PHONE_MODEL", "")       # the GGUF phones download
REPLY_TIMEOUT = float(os.environ.get("HESTIA_PHONE_TIMEOUT", "120"))


class NoPhone(RuntimeError):
    """No phone is connected, or the one that was dropped mid-answer."""


# --- devices -----------------------------------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _devices() -> dict[str, str]:
    try:
        return json.loads(DEVICES.read_text())
    except (OSError, ValueError):
        return {}


def add_device(name: str) -> str:
    """Issue a device token. Only its hash is kept; the token is shown once."""
    token = secrets.token_urlsafe(32)
    devices = {**_devices(), name: _digest(token)}
    DEVICES.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(DEVICES, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(devices, f, indent=1)
    return token


def device_for(token: str | None) -> str | None:
    if not token:
        return None
    digest = _digest(token)
    return next((name for name, known in _devices().items() if hmac.compare_digest(known, digest)), None)


def bearer(header: str | None) -> str | None:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


# --- whose reminders a phone schedules -----------------------------------------------------------
# A reminder's `person` says who it is for; null is the household default (reminders_tick sends
# it to REMINDER_NOTIFY). A device is tied to a person by the operator (`phone.py tie`), never
# guessed from its name. A tied phone gets its person's reminders. An untied phone is a default
# phone: it gets the null-person reminders, and those of any person with no phone tied, the same
# fallback reminders_tick uses so a reminder for someone without a target still lands somewhere.

REMINDER_DAYS = 14          # default sync horizon
REMINDER_MAX_DAYS = 31      # the most a phone may ask for
REMINDER_LIMIT = 64         # iOS keeps at most 64 pending local notifications per app


def _people() -> dict[str, str]:
    try:
        people = json.loads(PEOPLE.read_text())
    except (OSError, ValueError):
        return {}
    return {d: p for d, p in people.items() if isinstance(p, str)} if isinstance(people, dict) else {}


def _valid_person(person: str) -> bool:
    """The same shape config.person() accepts, so a tie can match a reminder's person column."""
    return bool(person) and person.isascii() and person.replace("_", "").isalnum() \
        and person == person.lower() and len(person) <= 32


def tie(device: str, person: str | None) -> None:
    """Tie a device to a person (lowercase, as HESTIA_PERSON spells it), or untie it with None."""
    if device not in _devices():
        raise ValueError(f"no device named {device!r}")
    if person is not None and not _valid_person(person):
        raise ValueError("a person is a short lowercase name, letters, digits and _ only")
    people = {d: p for d, p in _people().items() if d != device}
    if person is not None:
        people[device] = person
    PEOPLE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PEOPLE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(people, f, indent=1)


def person_for(device: str) -> str | None:
    return _people().get(device)


def _local(due_at: str) -> dt.datetime | None:
    """A stored due time (ISO, local time of the box when naive) as an aware datetime."""
    try:
        when = dt.datetime.fromisoformat(due_at)
    except (TypeError, ValueError):
        return None
    return when.astimezone() if when.tzinfo is None else when


def reminders_for(device: str, days: int = REMINDER_DAYS, now: dt.datetime | None = None) -> dict:
    """The reminders this phone should have scheduled with iOS: unfired, due after now and within
    `days`, for this phone's person, soonest first, at most REMINDER_LIMIT. Reminders are one-shot
    rows (reminders_store), so each is one notification. `sync` hashes the whole list: when it
    differs from the app's last one, the app replaces its scheduled set. `complete_until` is the
    instant the list is whole up to; when the cap cuts it short, that is the last one returned,
    and the app should sync again before then."""
    import reminders_store
    days = max(1, min(int(days), REMINDER_MAX_DAYS))
    now = (now or dt.datetime.now()).astimezone()
    until = now + dt.timedelta(days=days)
    tied = person_for(device)
    if tied is not None:
        mine = lambda p: p == tied  # noqa: E731
    else:
        covered = set(_people().values())
        mine = lambda p: p is None or p not in covered  # noqa: E731
    rows = []
    for r in reminders_store.unfired():
        when = _local(r["due_at"])
        if when is None or not (now < when <= until) or not mine(r.get("person")):
            continue
        key = hashlib.sha256(f"{r['id']}|{r['created_at']}".encode()).hexdigest()[:12]
        rows.append({"id": f"reminder-{r['id']}-{key}", "text": r["text"],
                     "due_at": when.isoformat(timespec="seconds"), "person": r.get("person")})
    rows.sort(key=lambda r: _local(r["due_at"]))
    truncated = len(rows) > REMINDER_LIMIT
    rows = rows[:REMINDER_LIMIT]
    complete_until = rows[-1]["due_at"] if truncated else until.isoformat(timespec="seconds")
    sync = hashlib.sha256(json.dumps([[r["id"], r["text"], r["due_at"]] for r in rows]).encode()).hexdigest()
    return {"device": device, "person": tied, "reminders": rows, "sync": sync,
            "truncated": truncated, "complete_until": complete_until, "days": days,
            "generated_at": now.isoformat(timespec="seconds")}


# --- the model file ----------------------------------------------------------------------------

_sha_cache: dict[tuple, str] = {}


def model_info() -> dict | None:
    """The model phones should run: name, size and sha256 (computed once per file version)."""
    if not MODEL_FILE or not Path(MODEL_FILE).is_file():
        return None
    path = Path(MODEL_FILE)
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()
    return {"name": path.name, "size": stat.st_size, "sha256": _sha_cache[key]}


# --- the hub -----------------------------------------------------------------------------------

class Phone:
    """One connected phone. It runs one request at a time, so requests take turns on its lock."""

    def __init__(self, name: str, send):
        self.name = name
        self.send = send                       # async callable taking a dict
        self.lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Queue] = {}

    def deliver(self, msg: dict) -> None:
        queue = self.pending.get(str(msg.get("id")))
        if queue is not None:
            queue.put_nowait(msg)

    def drop(self) -> None:
        for queue in self.pending.values():
            queue.put_nowait({"type": "error", "message": "the phone disconnected"})


class Hub:
    def __init__(self):
        self.phones: list[Phone] = []
        self._ids = itertools.count(1)

    def connected(self) -> bool:
        return bool(self.phones)

    def attach(self, phone: Phone) -> None:
        self.phones.append(phone)

    def detach(self, phone: Phone) -> None:
        if phone in self.phones:
            self.phones.remove(phone)
        phone.drop()

    async def complete(self, body: dict, sink=None) -> dict:
        """Run one completion on the most recently connected phone. `body` is a llama.cpp-style
        /completion request; the answer comes back in the same shape (content, stop_type, token
        counts). `sink` receives text as it streams."""
        if not self.phones:
            raise NoPhone("no phone is connected")
        phone = self.phones[-1]
        async with phone.lock:
            rid = str(next(self._ids))
            queue: asyncio.Queue = asyncio.Queue()
            phone.pending[rid] = queue
            try:
                await phone.send({"type": "complete", "id": rid, **body})
                parts = []
                while True:
                    msg = await asyncio.wait_for(queue.get(), timeout=REPLY_TIMEOUT)
                    kind = msg.get("type")
                    if kind == "delta":
                        text = str(msg.get("text") or "")
                        parts.append(text)
                        if sink and text:
                            await sink(text)
                    elif kind == "done":
                        return {"content": "".join(parts), "stop_type": msg.get("stop_type"),
                                "tokens_evaluated": msg.get("tokens_evaluated", 0),
                                "tokens_predicted": msg.get("tokens_predicted", 0)}
                    else:
                        raise NoPhone(str(msg.get("message") or "the phone could not answer"))
            except asyncio.TimeoutError:
                raise NoPhone(f"the phone did not answer within {REPLY_TIMEOUT:.0f}s") from None
            finally:
                phone.pending.pop(rid, None)


hub = Hub()


async def drain_waiting_work() -> dict:
    """Run model_jobs' queue through the phone that just connected (in a worker thread, so the
    finishers' file and network work stays off the event loop)."""
    import backend
    import model_jobs
    loop = asyncio.get_running_loop()

    def run(request: dict) -> dict:
        path, body = backend.text_request(request["messages"], **request.get("options", {}))
        answer = asyncio.run_coroutine_threadsafe(hub.complete(body), loop).result(REPLY_TIMEOUT + 10)
        return {"content": answer["content"], "truncated": answer["stop_type"] == "limit"}

    if not model_jobs.pending():
        return {"done": 0, "failed": 0, "retry": 0}
    return await asyncio.to_thread(model_jobs.drain, run)


async def serve(ws) -> None:
    """The socket's whole life: token, hello, then route answers until the app goes away."""
    name = device_for(bearer(ws.headers.get("authorization")))
    if not name:
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=15)
    except Exception:  # noqa: BLE001 - no hello, no phone
        await ws.close(code=4400)
        return
    await asyncio.to_thread(_hello_seen, name, hello)
    info = await asyncio.to_thread(model_info)   # hashing a 1 GB file stays off the event loop
    model_ok = bool(info) and hello.get("model_sha256") == info["sha256"]
    await ws.send_json({"type": "ready", "model_sha256": info["sha256"] if info else None, "model_ok": model_ok})
    phone = Phone(name, ws.send_json)
    if model_ok:
        hub.attach(phone)
        asyncio.get_running_loop().create_task(_drain_logged())
    try:
        while True:
            phone.deliver(await ws.receive_json())
    except Exception:  # noqa: BLE001 - the app went to the background or the network dropped
        pass
    finally:
        hub.detach(phone)
        await asyncio.to_thread(_gone, name)


_open: dict[str, int] = {}   # device -> sockets open in this process


def _hello_seen(name: str, hello: dict) -> None:
    """Mark the app as up (push.py won't ping it) and keep its APNs token, if it sent one."""
    import push
    _open[name] = _open.get(name, 0) + 1
    try:
        push.seen(name, True)
        if hello.get("push_token") is not None and not push.register(name, hello["push_token"]):
            print(f"[phone] {name} sent a push token that is not an APNs token; ignored", flush=True)
    except Exception as e:  # noqa: BLE001 - bookkeeping must not stop the socket
        print(f"[phone] push bookkeeping failed: {type(e).__name__}", flush=True)


def _gone(name: str) -> None:
    import push
    _open[name] = max(0, _open.get(name, 0) - 1)
    if not _open[name]:
        try:
            push.seen(name, False)
        except Exception as e:  # noqa: BLE001
            print(f"[phone] push bookkeeping failed: {type(e).__name__}", flush=True)


async def _drain_logged() -> None:
    try:
        counts = await drain_waiting_work()
        if any(counts.values()):
            print(f"[phone] drained waiting model work: {counts}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[phone] draining waiting work failed: {type(e).__name__}", flush=True)


def main() -> int:
    config.load_secrets()
    if len(sys.argv) == 3 and sys.argv[1] == "add-device":
        print(add_device(sys.argv[2]))
        print("Shown once. Put it in the app; only its hash is kept.", file=sys.stderr)
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "tie":
        try:
            tie(sys.argv[2], None if sys.argv[3] == "-" else sys.argv[3])
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if sys.argv[1:] == ["list"]:
        people = _people()
        print("devices:", ", ".join(f"{d} ({people.get(d, 'default')})" for d in _devices()) or "none")
        print("model:", model_info() or "HESTIA_PHONE_MODEL is not set or missing")
        return 0
    print(__doc__.split("\n\n")[2])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
