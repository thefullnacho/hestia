"""A content-free ping through Apple's push service: something is waiting in Hestia, never what.

PHONE-APP.md ("Notifications") and SECURITY.md ("Phone notifications") have the design. An
iPhone app can only be woken from outside through APNs, so this ping leaves the house. What it
carries is fixed here, in code: one of three canned sentences, picked by a reason word from a
fixed set. No caller can put text in it. Opening the ping brings the app up; the app connects
its socket, the box drains the model work that waited (model_jobs) and the app syncs the rest.

Two deterministic triggers call notify(), nothing else does, and no model decides:
  - model work is queued while no phone is connected (model_jobs.enqueue)
  - a reminder is made from another window while that phone's app is closed (tools/reminder.py)
Each device gets at most one ping per HESTIA_PUSH_DEBOUNCE_MIN minutes, unless its app has
connected since the last one.

Config, all environment (secrets/push.env, template at deploy/push.env.example):
    HESTIA_APNS_KEY_FILE   the .p8 signing key; a relative path is read under secrets/
    HESTIA_APNS_KEY_ID     the key's 10-character id
    HESTIA_APNS_TEAM_ID    the Apple developer team id
    HESTIA_APNS_BUNDLE_ID  the app's bundle id (the push topic)
    HESTIA_APNS_SANDBOX    1 for development builds (api.sandbox.push.apple.com)
    HESTIA_PUSH_DEBOUNCE_MIN  default 30

With no key configured a ping is a logged dry run. Without a key and without HESTIA_BACKEND=phone
this module does nothing at all. A failed send is logged, never raised: the reminder or the job
that asked for the ping has already been saved.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

import config
import records_store as store  # shared hestia.db: the phone_push table

# reason -> the only words a ping can say. Anything else reads as "waiting".
MESSAGES = {
    "waiting": "Something is waiting in Hestia.",
    "briefing": "Your briefing is ready in Hestia.",
    "reminder": "A new reminder is waiting in Hestia.",
}
TITLE = "Hestia"
COLLAPSE_ID = "hestia-waiting"   # several pings show as one on the phone
EXPIRY = dt.timedelta(hours=24)  # Apple stops retrying a ping the phone has not taken by then
JWT_REFRESH_S = 50 * 60          # Apple: refresh no sooner than 20 minutes, no later than 60
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
PRODUCTION = "https://api.push.apple.com"
SANDBOX = "https://api.sandbox.push.apple.com"
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64,200}$")
_GONE = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}

_transport: httpx.BaseTransport | None = None   # tests put an httpx.MockTransport here


@dataclass(frozen=True)
class Settings:
    key_file: Path
    key_id: str
    team_id: str
    bundle_id: str
    sandbox: bool

    @property
    def host(self) -> str:
        return SANDBOX if self.sandbox else PRODUCTION


def settings() -> Settings | None:
    """The APNs config from the environment, or None when any part is missing (dry run)."""
    env = {k: os.environ.get(f"HESTIA_APNS_{k}", "").strip()
           for k in ("KEY_FILE", "KEY_ID", "TEAM_ID", "BUNDLE_ID")}
    if not all(env.values()):
        return None
    key_file = Path(env["KEY_FILE"])
    if not key_file.is_absolute():
        key_file = config.SECRETS_DIR / key_file
    return Settings(key_file, env["KEY_ID"], env["TEAM_ID"], env["BUNDLE_ID"],
                    os.environ.get("HESTIA_APNS_SANDBOX", "").strip() in ("1", "true", "yes"))


def active() -> bool:
    """Push matters only on a box whose model is a phone, or one given an APNs key."""
    if settings() is not None:
        return True
    import backend  # lazy: backend pulls in the tool registry
    return backend.deferred()


def debounce() -> dt.timedelta:
    try:
        return dt.timedelta(minutes=float(os.environ.get("HESTIA_PUSH_DEBOUNCE_MIN", "30")))
    except ValueError:
        return dt.timedelta(minutes=30)


# --- the provider token (ES256 JWT) ------------------------------------------------------------

_jwt_cache: dict[tuple, tuple[str, float]] = {}
_jwt_lock = threading.Lock()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(cfg: Settings, issued: int) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(cfg.key_file.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("the APNs key must be a P-256 (ES256) key")
    header = _b64(json.dumps({"alg": "ES256", "kid": cfg.key_id}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iss": cfg.team_id, "iat": issued}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def provider_token(cfg: Settings, *, now: float | None = None, fresh: bool = False) -> str:
    """The signed JWT Apple wants on every request, reused until it is 50 minutes old."""
    now = time.time() if now is None else now
    cache_key = (str(cfg.key_file), cfg.key_id, cfg.team_id)
    with _jwt_lock:
        cached = _jwt_cache.get(cache_key)
        if cached and not fresh and now - cached[1] < JWT_REFRESH_S:
            return cached[0]
        token = _sign(cfg, int(now))
        _jwt_cache[cache_key] = (token, now)
        return token


# --- what goes to Apple ------------------------------------------------------------------------

def payload(reason: str) -> bytes:
    """The whole body of a ping. Built only from the fixed strings above."""
    body = MESSAGES.get(reason, MESSAGES["waiting"])
    return json.dumps({"aps": {"alert": {"title": TITLE, "body": body}, "sound": "default"}},
                      separators=(",", ":")).encode()


def _headers(cfg: Settings, jwt: str) -> dict:
    return {
        "authorization": f"bearer {jwt}",
        "apns-topic": cfg.bundle_id,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-expiration": str(int(time.time() + EXPIRY.total_seconds())),
        "apns-collapse-id": COLLAPSE_ID,
    }


def send(cfg: Settings, device_token: str, reason: str) -> str:
    """One ping to one device. Returns "sent", "gone" (Apple no longer knows the token) or
    "error: ..."; never raises."""
    try:
        with httpx.Client(http2=True, timeout=TIMEOUT, transport=_transport) as client:
            for attempt in (0, 1):
                resp = client.post(f"{cfg.host}/3/device/{device_token}", content=payload(reason),
                                   headers=_headers(cfg, provider_token(cfg, fresh=attempt > 0)))
                if resp.status_code == 200:
                    return "sent"
                why = _reason(resp)
                if resp.status_code == 410 or why in _GONE:
                    return "gone"
                if resp.status_code == 403 and why == "ExpiredProviderToken" and attempt == 0:
                    continue
                return f"error: {resp.status_code} {why}".strip()
    except Exception as e:  # noqa: BLE001 - a ping must never break its caller
        return f"error: {type(e).__name__}"
    return "error: unreachable"


def _reason(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("reason") or "")
    except ValueError:
        return ""


# --- devices: tokens, presence, debounce -------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def valid_token(token) -> bool:
    return isinstance(token, str) and bool(_TOKEN_RE.match(token))


def register(device: str, token: str) -> bool:
    """Keep a device's APNs token, as its app reported it in the socket hello."""
    if not valid_token(token):
        return False
    with store._conn() as c:
        c.execute("INSERT INTO phone_push(device, token, registered_at) VALUES(?,?,?) "
                  "ON CONFLICT(device) DO UPDATE SET token=excluded.token, "
                  "registered_at=excluded.registered_at",
                  (device, token.lower(), _now().isoformat()))
    return True


def forget(device: str) -> None:
    with store._conn() as c:
        c.execute("UPDATE phone_push SET token=NULL WHERE device=?", (device,))


def seen(device: str, connected: bool) -> None:
    """The app opened (or closed) its socket in this process. Other processes, the timed jobs and
    the MCP server, read this to know whether a phone is connected."""
    now = _now().isoformat()
    with store._conn() as c:
        c.execute("INSERT INTO phone_push(device, last_seen_at, connected_pid) VALUES(?,?,?) "
                  "ON CONFLICT(device) DO UPDATE SET last_seen_at=excluded.last_seen_at, "
                  "connected_pid=excluded.connected_pid",
                  (device, now, os.getpid() if connected else None))


def reset_connections() -> None:
    """At brain startup: no socket is open yet, and the brain is the only process that holds
    them, so a mark left by a crash or power loss (a pid that may be reused) is cleared."""
    with store._conn() as c:
        c.execute("UPDATE phone_push SET connected_pid=NULL WHERE connected_pid IS NOT NULL")


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def devices() -> list[dict]:
    with store._conn() as c:
        rows = c.execute("SELECT device, token, last_push_at, last_seen_at, connected_pid "
                         "FROM phone_push").fetchall()
    return [{**dict(r), "connected": _alive(r["connected_pid"])} for r in rows]


def _debounced(row: dict, now: dt.datetime) -> bool:
    last = row.get("last_push_at")
    if not last:
        return False
    last_push = dt.datetime.fromisoformat(last)
    seen_at = row.get("last_seen_at")
    if seen_at and dt.datetime.fromisoformat(seen_at) >= last_push:
        return False  # the app came up since: this is news again
    return now - last_push < debounce()


def _spawn(fn) -> None:
    """Run a ping beside the caller. Not a daemon: a timer job that queues work and exits still
    gets its ping out, bounded by TIMEOUT per device. Tests swap this for a direct call."""
    threading.Thread(target=fn, name="hestia-push").start()


def notify_later(reason: str = "waiting", *, every_device: bool = False,
                 only: set[str] | None = None) -> None:
    """notify(), off the caller's path: a slow or unreachable Apple (the WAN down, a blocked
    host) must never delay or fail the reminder or job that asked for the ping."""
    if active():
        _spawn(lambda: notify(reason, every_device=every_device, only=only))


def notify(reason: str = "waiting", *, every_device: bool = False, only: set[str] | None = None) -> dict:
    """Ping the phones that need waking. With `every_device` (a new reminder: each phone syncs
    its own) every closed app is pinged; otherwise (model work: one phone drains it) nobody is
    pinged while any phone is connected. `only` limits it to those devices (a reminder wakes
    the phones that sync it). Returns what happened, per device. Never raises."""
    try:
        if not active():
            return {}
        return _notify(reason if reason in MESSAGES else "waiting", every_device, only)
    except Exception as e:  # noqa: BLE001
        print(f"[push] failed: {type(e).__name__}: {e}", flush=True)
        return {}


def _notify(reason: str, every_device: bool, only: set[str] | None = None) -> dict:
    rows = [r for r in devices() if only is None or r["device"] in only]
    if not every_device and any(r["connected"] for r in rows):
        return {r["device"]: "connected" for r in rows}
    cfg = settings()
    now = _now()
    out = {}
    for row in rows:
        name = row["device"]
        if row["connected"]:
            out[name] = "connected"
        elif not row["token"]:
            out[name] = "no token"
        elif _debounced(row, now):
            out[name] = "debounced"
        else:
            out[name] = send(cfg, row["token"], reason) if cfg else "dry run"
            if out[name] == "gone":
                forget(name)
            elif out[name] in ("sent", "dry run"):
                with store._conn() as c:
                    c.execute("UPDATE phone_push SET last_push_at=? WHERE device=?",
                              (now.isoformat(), name))
    shown = {k: v for k, v in out.items() if v not in ("connected", "no token")}
    if shown:
        print(f"[push] {reason}: {shown}", flush=True)
    return out
