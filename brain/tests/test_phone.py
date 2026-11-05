"""phone: the box's end of the phone's socket, devices, the model file, and catching up."""
import asyncio
import os
import stat
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend
import hestia
import model_jobs
import phone
import tools

TEMPLATE = ("{{ bos_token }}{% for m in messages %}<{{ m.role }}>{{ m.content }}</>{% endfor %}"
            "{% if tools %}[{{ tools | length }} tools]{% endif %}<assistant>")
CALL = ("<tool_call>\n<function=home>\n<parameter=action>\nturn_off\n</parameter>\n"
        "<parameter=entity_id>\nlight.kitchen\n</parameter>\n</function>\n</tool_call>")


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A brain whose model is a phone: a device, a model file, a template, an empty hub."""
    template = tmp_path / "template.jinja"
    template.write_text(TEMPLATE)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"weights")
    monkeypatch.setattr(phone, "DEVICES", tmp_path / "devices.json")
    monkeypatch.setattr(phone, "MODEL_FILE", str(model))
    monkeypatch.setattr(phone, "hub", phone.Hub())
    monkeypatch.setattr(backend, "BACKEND", "phone")
    monkeypatch.setattr(backend, "CHAT_TEMPLATE", str(template))
    backend._template_text.clear()
    return {"token": phone.add_device("alex-17pro"), "sha": phone.model_info()["sha256"]}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_device_tokens_are_kept_only_as_hashes(box):
    raw = phone.DEVICES.read_text()
    assert box["token"] not in raw and phone.device_for(box["token"]) == "alex-17pro"
    assert phone.device_for("guess") is None and phone.device_for(None) is None
    assert stat.S_IMODE(os.stat(phone.DEVICES).st_mode) == 0o600


def test_the_hub_routes_one_answer_and_streams_it():
    hub = phone.Hub()
    sent, streamed = [], []

    async def scenario():
        async def send(msg):
            sent.append(msg)
            p.deliver({"type": "delta", "id": msg["id"], "text": "Lights "})
            p.deliver({"type": "delta", "id": msg["id"], "text": "off."})
            p.deliver({"type": "done", "id": msg["id"], "stop_type": "eos", "tokens_predicted": 3})
        p = phone.Phone("test", send)
        hub.attach(p)

        async def sink(text):
            streamed.append(text)
        return await hub.complete({"prompt": "x", "n_predict": 8}, sink)

    out = asyncio.run(scenario())
    assert out["content"] == "Lights off." and out["stop_type"] == "eos" and streamed == ["Lights ", "off."]
    assert sent[0]["type"] == "complete" and sent[0]["prompt"] == "x"


def test_no_phone_and_a_dropped_phone_both_raise():
    hub = phone.Hub()
    with pytest.raises(phone.NoPhone):
        asyncio.run(hub.complete({"prompt": "x"}))

    async def scenario():
        async def send(msg):
            hub.detach(p)  # the app went to the background mid-answer
        p = phone.Phone("test", send)
        hub.attach(p)
        await hub.complete({"prompt": "x"})
    with pytest.raises(phone.NoPhone):
        asyncio.run(scenario())


def test_the_socket_refuses_an_unknown_device(box):
    with TestClient(hestia.app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect("/v1/model/socket", headers=auth("wrong")) as ws:
                ws.receive_json()
        assert e.value.code == 4401


def test_a_phone_with_the_wrong_model_is_told_and_not_used(box):
    with TestClient(hestia.app) as client:
        with client.websocket_connect("/v1/model/socket", headers=auth(box["token"])) as ws:
            ws.send_json({"type": "hello", "device": "17pro", "model_sha256": "old"})
            ready = ws.receive_json()
            assert ready == {"type": "ready", "model_sha256": box["sha"], "model_ok": False}
            assert not phone.hub.connected()


def test_a_chat_turn_runs_on_the_phone_over_the_socket(box, monkeypatch):
    ran = []
    monkeypatch.setattr(tools, "dispatch", lambda name, args: ran.append((name, args)) or "Done - Kitchen lights are off.")
    monkeypatch.setattr(hestia, "_build_system_prompt", lambda text: asyncio.sleep(0, result="You are Hestia."))
    monkeypatch.setattr(tools.home, "light_areas", lambda: [])
    with TestClient(hestia.app) as client:
        with client.websocket_connect("/v1/model/socket", headers=auth(box["token"])) as ws:
            ws.send_json({"type": "hello", "device": "17pro", "model_sha256": box["sha"]})
            assert ws.receive_json()["model_ok"] is True
            result = {}
            turn = threading.Thread(target=lambda: result.update(r=client.post(
                "/v1/chat/completions", json={"messages": [{"role": "user", "content": "lights off"}]})))
            turn.start()
            first = ws.receive_json()                        # the box sends prompt text, never messages
            assert first["type"] == "complete" and "<user>lights off</>" in first["prompt"]
            ws.send_json({"type": "delta", "id": first["id"], "text": CALL})
            ws.send_json({"type": "done", "id": first["id"], "stop_type": "eos"})
            second = ws.receive_json()                       # the answer after the tool result
            ws.send_json({"type": "delta", "id": second["id"], "text": "Kitchen lights are off."})
            ws.send_json({"type": "done", "id": second["id"], "stop_type": "eos"})
            turn.join(30)
    assert ran == [("home", {"action": "turn_off", "entity_id": "light.kitchen"})]
    # A single successful action is answered with its receipt, not the model's wording.
    assert result["r"].json()["choices"][0]["message"]["content"] == "Done - Kitchen lights are off."


def test_with_no_phone_a_turn_says_to_open_the_app(box, monkeypatch):
    monkeypatch.setattr(hestia, "_build_system_prompt", lambda text: asyncio.sleep(0, result="You are Hestia."))
    with TestClient(hestia.app) as client:
        r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "lights off"}]})
    assert r.json()["choices"][0]["message"]["content"] == hestia._NO_PHONE


def test_work_that_waited_is_finished_when_a_phone_connects(box, db, monkeypatch):
    done = []
    monkeypatch.setattr(model_jobs, "_finisher", lambda kind: lambda text, ctx: done.append((text, ctx)))
    model_jobs.enqueue("briefing", {"messages": [{"role": "user", "content": "Rain at 3."}],
                                    "options": {"temperature": 0.3, "max_tokens": 64}}, {"identifier": "b1"})
    with TestClient(hestia.app) as client:
        with client.websocket_connect("/v1/model/socket", headers=auth(box["token"])) as ws:
            ws.send_json({"type": "hello", "device": "17pro", "model_sha256": box["sha"]})
            ws.receive_json()
            job = ws.receive_json()
            assert job["type"] == "complete" and "Rain at 3." in job["prompt"] and job["n_predict"] == 64
            ws.send_json({"type": "delta", "id": job["id"], "text": "Rain this afternoon."})
            ws.send_json({"type": "done", "id": job["id"], "stop_type": "eos"})
            for _ in range(100):
                if done:
                    break
                threading.Event().wait(0.05)
    assert done == [("Rain this afternoon.", {"identifier": "b1"})] and model_jobs.pending() == []


def test_the_model_file_and_its_details_take_a_device_token(box):
    with TestClient(hestia.app) as client:
        assert client.get("/v1/model/info").status_code == 401
        info = client.get("/v1/model/info", headers=auth(box["token"])).json()
        assert info["sha256"] == box["sha"] and info["size"] == len(b"weights")
        assert client.get("/v1/model/file").status_code == 401
        r = client.get("/v1/model/file", headers=auth(box["token"]))
        assert r.content == b"weights" and r.headers["x-model-sha256"] == box["sha"]


def test_health_names_the_phone_model_and_whether_a_phone_is_there(box):
    client = TestClient(hestia.app)
    out = client.get("/health").json()
    assert out["status"] == "ok" and out["backend"] == "phone"
    assert out["model"] == "model" and out["phone"] == "not connected"
    assert "ollama" not in out
    phone.hub.attach(phone.Phone("alex-17pro", None))
    assert client.get("/health").json()["phone"] == "connected"


def test_the_turn_log_names_the_phone_model_not_ollamas(box):
    assert hestia.TurnTrace().model == "model"
