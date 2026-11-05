"""push: the content-free ping through APNs, its token, its debounce and its two triggers.

Nothing here reaches Apple: every send goes through an httpx.MockTransport, and the signing key
is a throwaway P-256 key made in tmp_path."""
import base64
import datetime as dt
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

import backend
import hestia
import model_jobs
import phone
import push
from tools import reminder

TOKEN_A = "ab" * 32
TOKEN_B = "cd" * 32


@pytest.fixture
def key(tmp_path, monkeypatch):
    """A throwaway APNs key and the env that names it."""
    private = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "AuthKey_TEST123456.p8"
    path.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    for k, v in {"KEY_FILE": str(path), "KEY_ID": "TEST123456", "TEAM_ID": "TEAMID0001",
                 "BUNDLE_ID": "org.example.hestia", "SANDBOX": "1"}.items():
        monkeypatch.setenv(f"HESTIA_APNS_{k}", v)
    push._jwt_cache.clear()
    return private.public_key()


@pytest.fixture
def unconfigured(monkeypatch):
    for k in ("KEY_FILE", "KEY_ID", "TEAM_ID", "BUNDLE_ID", "SANDBOX"):
        monkeypatch.delenv(f"HESTIA_APNS_{k}", raising=False)


@pytest.fixture
def apple(monkeypatch):
    """A fake APNs: records each request, answers with whatever `replies` holds (default 200)."""
    seen, replies = [], []

    def handler(request: httpx.Request):
        seen.append(request)
        status, body = replies.pop(0) if replies else (200, None)
        return httpx.Response(status, json=body) if body else httpx.Response(status)
    monkeypatch.setattr(push, "_transport", httpx.MockTransport(handler))
    return {"seen": seen, "replies": replies}


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _closed_phone(name="alex-17pro", token=TOKEN_A):
    push.register(name, token)


# --- the provider token ------------------------------------------------------------------------

def test_the_jwt_is_es256_signed_by_the_key_and_names_team_and_key(key):
    jwt = push.provider_token(push.settings(), now=1_800_000_000)
    header, claims, sig = jwt.split(".")
    assert json.loads(_unb64(header)) == {"alg": "ES256", "kid": "TEST123456"}
    assert json.loads(_unb64(claims)) == {"iss": "TEAMID0001", "iat": 1_800_000_000}
    raw = _unb64(sig)
    assert len(raw) == 64  # JOSE r||s, not DER
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if forged


def test_the_jwt_is_reused_for_50_minutes_then_refreshed(key):
    cfg = push.settings()
    first = push.provider_token(cfg, now=1000)
    assert push.provider_token(cfg, now=1000 + 49 * 60) == first
    later = push.provider_token(cfg, now=1000 + 50 * 60)
    assert later != first and json.loads(_unb64(later.split(".")[1]))["iat"] == 1000 + 50 * 60


def test_a_relative_key_path_is_read_under_secrets(key, monkeypatch, tmp_path):
    monkeypatch.setenv("HESTIA_APNS_KEY_FILE", "apns/AuthKey.p8")
    monkeypatch.setattr(push.config, "SECRETS_DIR", tmp_path / "secrets")
    assert push.settings().key_file == tmp_path / "secrets" / "apns" / "AuthKey.p8"
    assert push.settings().host == push.SANDBOX


# --- what leaves the house ---------------------------------------------------------------------

def test_the_payload_is_fixed_and_carries_no_caller_text():
    secret = "Pick up Dawn's prescription at 4"
    for reason in ("briefing", "reminder", "waiting", secret, "", None):
        body = push.payload(reason)
        assert secret.encode() not in body
        aps = json.loads(body)["aps"]
        assert aps["alert"]["title"] == "Hestia" and aps["alert"]["body"] in push.MESSAGES.values()
        assert set(json.loads(body)) == {"aps"}


def test_a_reminder_ping_never_carries_the_reminders_words(key, apple, db):
    _closed_phone()
    out = reminder.execute("create", text="Call the vet about Lily's stitches", when="10 minutes")
    assert out.startswith("Reminder #")
    [req] = apple["seen"]
    assert b"Lily" not in req.content and b"vet" not in req.content
    assert json.loads(req.content)["aps"]["alert"]["body"] == push.MESSAGES["reminder"]
    assert req.url == f"{push.SANDBOX}/3/device/{TOKEN_A}"
    assert req.headers["apns-topic"] == "org.example.hestia" and req.headers["apns-push-type"] == "alert"
    assert req.headers["authorization"].startswith("bearer ey")
    assert req.headers["apns-collapse-id"] == push.COLLAPSE_ID


# --- dry run and inertness ---------------------------------------------------------------------

def test_unconfigured_on_a_phone_box_is_a_logged_dry_run(unconfigured, apple, db, monkeypatch, capsys):
    monkeypatch.setattr(backend, "BACKEND", "phone")
    _closed_phone()
    assert push.notify("waiting") == {"alex-17pro": "dry run"}
    assert apple["seen"] == [] and "dry run" in capsys.readouterr().out


def test_unconfigured_off_a_phone_box_does_nothing(unconfigured, apple, db, monkeypatch):
    monkeypatch.setattr(backend, "BACKEND", "ollama")
    _closed_phone()
    assert push.notify("waiting") == {} and apple["seen"] == []


def test_a_failed_send_never_breaks_the_caller(key, db, monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(push, "_transport", httpx.MockTransport(boom))
    _closed_phone()
    assert push.notify("waiting") == {"alex-17pro": "error: ConnectError"}
    assert reminder.execute("create", text="x", when="10 minutes").startswith("Reminder #")


# --- tokens: registration, Apple saying a token is gone -----------------------------------------

def test_410_unregistered_drops_the_token(key, apple, db):
    _closed_phone()
    apple["replies"].append((410, {"reason": "Unregistered"}))
    assert push.notify("waiting") == {"alex-17pro": "gone"}
    assert push.devices()[0]["token"] is None
    assert push.notify("waiting") == {"alex-17pro": "no token"} and len(apple["seen"]) == 1


def test_bad_device_token_drops_the_token(key, apple, db):
    _closed_phone()
    apple["replies"].append((400, {"reason": "BadDeviceToken"}))
    assert push.notify("waiting") == {"alex-17pro": "gone"} and push.devices()[0]["token"] is None


def test_an_expired_provider_token_is_re_signed_once(key, apple, db):
    _closed_phone()
    apple["replies"].append((403, {"reason": "ExpiredProviderToken"}))
    assert push.notify("waiting") == {"alex-17pro": "sent"} and len(apple["seen"]) == 2


def test_only_hex_apns_tokens_are_kept(db):
    assert not push.register("alex-17pro", "not a token")
    assert not push.register("alex-17pro", "ab" * 8)
    assert push.register("alex-17pro", TOKEN_A.upper()) and push.devices()[0]["token"] == TOKEN_A


@pytest.fixture
def box(tmp_path, monkeypatch, db):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"weights")
    monkeypatch.setattr(phone, "DEVICES", tmp_path / "devices.json")
    monkeypatch.setattr(phone, "MODEL_FILE", str(model))
    monkeypatch.setattr(phone, "hub", phone.Hub())
    monkeypatch.setattr(backend, "BACKEND", "phone")
    return {"token": phone.add_device("alex-17pro"), "sha": phone.model_info()["sha256"]}


def test_the_hello_registers_the_token_under_the_authenticated_device(box):
    with TestClient(hestia.app) as client:
        with client.websocket_connect("/v1/model/socket",
                                      headers={"Authorization": f"Bearer {box['token']}"}) as ws:
            # The device name comes from the bearer token, not from what the hello claims.
            ws.send_json({"type": "hello", "device": "someone-else", "model_sha256": box["sha"],
                          "push_token": TOKEN_A})
            ws.receive_json()
            [row] = push.devices()
            assert row["device"] == "alex-17pro" and row["token"] == TOKEN_A and row["connected"]
    for _ in range(100):  # the server's goodbye runs after the client has gone
        [row] = push.devices()
        if not row["connected"]:
            break
        time.sleep(0.02)
    assert not row["connected"] and row["last_seen_at"]


def test_an_unknown_device_cannot_register_a_token(box):
    from starlette.websockets import WebSocketDisconnect
    with TestClient(hestia.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/model/socket", headers={"Authorization": "Bearer guess"}) as ws:
                ws.send_json({"type": "hello", "model_sha256": box["sha"], "push_token": TOKEN_B})
                ws.receive_json()
    assert push.devices() == []


# --- triggers and debounce ---------------------------------------------------------------------

def test_one_ping_for_many_jobs_then_again_after_the_window(key, apple, db, monkeypatch):
    monkeypatch.setattr(backend, "BACKEND", "phone")
    _closed_phone()
    req = {"messages": [{"role": "user", "content": "Rain at 3."}]}
    model_jobs.enqueue("briefing", req, {}, key="2026-11-04")
    model_jobs.enqueue("note", req, {})
    model_jobs.enqueue("feed", req, {})
    assert len(apple["seen"]) == 1
    assert json.loads(apple["seen"][0].content)["aps"]["alert"]["body"] == push.MESSAGES["briefing"]
    later = dt.datetime.now().astimezone() + dt.timedelta(minutes=31)
    monkeypatch.setattr(push, "_now", lambda: later)
    model_jobs.enqueue("note", req, {})
    assert len(apple["seen"]) == 2


def test_the_app_opening_resets_the_debounce(key, apple, db):
    _closed_phone()
    assert push.notify("waiting") == {"alex-17pro": "sent"}
    assert push.notify("waiting") == {"alex-17pro": "debounced"}
    push.seen("alex-17pro", True)
    push.seen("alex-17pro", False)
    assert push.notify("waiting") == {"alex-17pro": "sent"}


def test_waiting_work_pings_nobody_while_any_phone_is_connected(key, apple, db):
    _closed_phone("alex-17pro", TOKEN_A)
    _closed_phone("dawn-15", TOKEN_B)
    push.seen("dawn-15", True)
    assert push.notify("waiting") == {"alex-17pro": "connected", "dawn-15": "connected"}
    assert apple["seen"] == []


def test_a_reminder_pings_each_closed_app_but_not_an_open_one(key, apple, db):
    _closed_phone("alex-17pro", TOKEN_A)
    _closed_phone("dawn-15", TOKEN_B)
    push.seen("dawn-15", True)
    assert push.notify("reminder", every_device=True) == {"alex-17pro": "sent", "dawn-15": "connected"}
    assert [r.url.path for r in apple["seen"]] == [f"/3/device/{TOKEN_A}"]


def test_a_connection_held_by_a_dead_process_does_not_count(key, apple, db):
    _closed_phone()
    with push.store._conn() as c:
        c.execute("UPDATE phone_push SET connected_pid=? WHERE device=?", (2**22 + 12345, "alex-17pro"))
    assert push.notify("waiting") == {"alex-17pro": "sent"}
