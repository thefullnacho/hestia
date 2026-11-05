"""The box's end of the phone: the household's model, reached over a socket the phone opens.

PHONE-APP.md has the design. The app dials the box when it comes to the foreground; the box
sends prompt text down that socket and the phone streams tokens back. The phone never listens,
never sees messages or tools (chat_format.py renders and parses on the box), and only runs while
the app is open, so model work that a timer starts waits in model_jobs until a phone connects.

Whatever answers on this socket sees every prompt and writes every tool call, so unlike the rest
of the brain it takes a device token, issued here and kept in the phone's Keychain:

    python phone.py add-device <name>     # prints a token once; only its hash is stored
    python phone.py list                  # devices, and the model file phones are served

Wire, one JSON object per WebSocket message:
    phone -> box  {"type": "hello", "device": "...", "model_sha256": "..."}
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
    if sys.argv[1:] == ["list"]:
        print("devices:", ", ".join(_devices()) or "none")
        print("model:", model_info() or "HESTIA_PHONE_MODEL is not set or missing")
        return 0
    print(__doc__.split("\n\n")[2])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
