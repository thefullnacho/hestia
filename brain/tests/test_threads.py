"""Threads on the brain: the chat history every window shares, the thread-mode turn that sends
only the new message, and the opt-in live events (status lines and receipts) the app shows."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import context_budget
import backend  # noqa: E402
import routing  # noqa: E402
import hestia
import threads_store
import tools


@pytest.fixture
def brain(db, monkeypatch, tmp_path):
    monkeypatch.setattr(hestia.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(hestia.note_taker, "ENABLED", False)
    return hestia


def _call(*requests):
    """Run (method, path, kwargs) requests in order against the app; return the responses."""
    async def scenario():
        transport = httpx.ASGITransport(app=hestia.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return [await client.request(m, p, **kw) for m, p, kw in requests]
    return asyncio.run(scenario())


def _sse(text: str) -> list:
    out = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    return out


# ---------- store ----------

def test_main_thread_always_exists(db):
    threads = threads_store.list_threads()
    assert [t["id"] for t in threads] == ["main"]
    assert threads[0]["title"] == "Main chat" and threads[0]["count"] == 0


def test_history_trims_by_bytes_and_starts_on_a_user_message(db, monkeypatch):
    monkeypatch.setattr(threads_store, "HISTORY_BYTES", 3000)
    t = threads_store.create()
    for i in range(10):
        threads_store.append_exchange(t["id"], f"q{i} " + "x" * 300, f"a{i} " + "y" * 300, None)
    new = {"role": "user", "content": "latest"}
    tail = threads_store.history(t["id"], new)
    assert tail[-1] == new
    assert tail[0]["role"] == "user"
    assert context_budget.estimate(tail) <= 3000 + 100
    assert tail[-2]["content"].startswith("a9")
    assert not context_budget.validate_messages(tail)


def test_history_never_exceeds_the_request_limits(db):
    t = threads_store.create()
    big = "z" * 20000
    for _ in range(12):
        threads_store.append_exchange(t["id"], big, big, None)
    tail = threads_store.history(t["id"], {"role": "user", "content": big})
    assert context_budget.validate_messages(tail) is None


def test_first_exchange_titles_a_default_thread_only(db):
    t = threads_store.create()
    threads_store.append_exchange(t["id"], "what should I plant in the round bed this fall", "Garlic.", None)
    assert threads_store.get(t["id"])["title"] == "what should I plant in the…"
    named = threads_store.create("Puppies")
    threads_store.append_exchange(named["id"], "hello", "hi", None)
    assert threads_store.get(named["id"])["title"] == "Puppies"


def test_threads_live_in_the_records_db_so_backups_cover_them(db):
    threads_store.create("x")
    import sqlite3
    names = {r[0] for r in sqlite3.connect(db.DB_PATH).execute("SELECT name FROM sqlite_master")}
    assert {"threads", "thread_messages"} <= names


# ---------- endpoints ----------

def test_thread_crud(brain):
    created, = _call(("POST", "/v1/threads", {"json": {"title": "Garden", "messages": [
        {"role": "user", "content": "hi", "ts": 1}, {"role": "assistant", "content": "hello"}]}}))
    assert created.status_code == 200
    tid = created.json()["id"]
    got, listed, renamed, cleared, deleted, gone, main_del = _call(
        ("GET", f"/v1/threads/{tid}", {}),
        ("GET", "/v1/threads", {}),
        ("PATCH", f"/v1/threads/{tid}", {"json": {"title": "Beds"}}),
        ("DELETE", f"/v1/threads/{tid}/messages", {}),
        ("DELETE", f"/v1/threads/{tid}", {}),
        ("GET", f"/v1/threads/{tid}", {}),
        ("DELETE", "/v1/threads/main", {}))
    msgs = got.json()["messages"]
    assert [(m["role"], m["content"], m["receipts"]) for m in msgs] == [
        ("user", "hi", None), ("assistant", "hello", None)]
    assert {t["id"] for t in listed.json()["threads"]} == {"main", tid}
    assert renamed.json()["title"] == "Beds"
    assert cleared.json() == {"ok": True}
    assert deleted.json() == {"ok": True}
    assert gone.status_code == 404
    assert main_del.status_code == 400


def test_import_rejects_bad_messages(brain):
    bad, tool = _call(
        ("POST", "/v1/threads", {"json": {"messages": "nope"}}),
        ("POST", "/v1/threads", {"json": {"messages": [{"role": "tool", "content": "x"}]}}))
    assert bad.status_code == 400 and tool.status_code == 400


def test_thread_turn_sends_history_and_persists_the_exchange(brain, monkeypatch):
    seen = []

    async def agent(messages, *, trace):
        seen.append(messages)
        return f"answer {len(seen)}"
    monkeypatch.setattr(hestia, "run_agent", agent)
    body = lambda text: {"json": {"thread_id": "main", "messages": [{"role": "user", "content": text}]},
                         "headers": {"Idempotency-Key": f"key-{text}-123456"}}
    first, second, again = _call(("POST", "/v1/chat/completions", body("one")),
                                 ("POST", "/v1/chat/completions", body("two")),
                                 ("POST", "/v1/chat/completions", body("two")))
    assert second.json()["choices"][0]["message"]["content"] == "answer 2"
    assert again.json() == second.json()                 # replay, not a re-run
    assert len(seen) == 2
    assert [m["content"] for m in seen[1]] == ["one", "answer 1", "two"]
    stored = threads_store.messages("main")
    assert [m["content"] for m in stored] == ["one", "answer 1", "two", "answer 2"]
    assert "receipts" not in second.json()               # not opted in: unchanged wire format


def test_thread_mode_rules(brain, monkeypatch):
    async def agent(messages, *, trace):
        return "x"
    monkeypatch.setattr(hestia, "run_agent", agent)
    two = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    many, unknown = _call(
        ("POST", "/v1/chat/completions", {"json": {"thread_id": "main", "messages": two}}),
        ("POST", "/v1/chat/completions", {"json": {"thread_id": "t_nope",
                                                   "messages": [{"role": "user", "content": "a"}]}}))
    assert many.status_code == 400 and unknown.status_code == 404


def test_busy_answer_stays_out_of_the_thread(brain, monkeypatch):
    async def agent(messages, *, trace):
        return hestia._BUSY
    monkeypatch.setattr(hestia, "run_agent", agent)
    _call(("POST", "/v1/chat/completions",
           {"json": {"thread_id": "main", "messages": [{"role": "user", "content": "hi"}]}}))
    assert threads_store.messages("main") == []


def test_fingerprint_includes_the_thread(brain, monkeypatch):
    async def agent(messages, *, trace):
        return "x"
    monkeypatch.setattr(hestia, "run_agent", agent)
    side = threads_store.create()
    msg = [{"role": "user", "content": "same"}]
    headers = {"Idempotency-Key": "shared-key-123456"}
    first, other = _call(
        ("POST", "/v1/chat/completions", {"json": {"thread_id": "main", "messages": msg}, "headers": headers}),
        ("POST", "/v1/chat/completions", {"json": {"thread_id": side["id"], "messages": msg}, "headers": headers}))
    assert first.status_code == 200 and other.status_code == 409


# ---------- live events and receipts ----------

def _lights_off(monkeypatch):
    async def prompt(_):
        return ""
    monkeypatch.setattr(hestia, "_build_system_prompt", prompt)
    monkeypatch.setattr(routing, "request_schemas", lambda _: tools.SCHEMAS)
    monkeypatch.setattr(tools, "dispatch", lambda name, args: "Done turned off light.light_kitchen_lights.")
    replies = [
        {"tool_calls": [{"function": {"name": "home", "arguments": {
            "action": "turn_off", "entity_id": "light.light_kitchen_lights"}}}]},
        {"content": "Kitchen lights are off."}]

    async def chat(*_):
        return replies.pop(0)
    monkeypatch.setattr(backend, "ollama_chat", chat)


def test_stream_events_carry_status_and_receipts(brain, monkeypatch):
    _lights_off(monkeypatch)
    r, = _call(("POST", "/v1/chat/completions", {"json": {
        "thread_id": "main", "stream": True, "hestia_events": True,
        "messages": [{"role": "user", "content": "kill the kitchen lights please"}]}}))
    chunks = _sse(r.text)
    assert chunks[0] == {"object": "hestia.status", "status": "Turning off the kitchen lights…"}
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["receipts"] == [{"tool": "home", "status": "succeeded",
                                  "text": "Done turned off light.light_kitchen_lights."}]
    stored = threads_store.messages("main")
    assert stored[-1]["receipts"] == final["receipts"]


def test_without_opt_in_the_stream_is_unchanged(brain, monkeypatch):
    _lights_off(monkeypatch)
    r, = _call(("POST", "/v1/chat/completions", {"json": {
        "stream": True, "messages": [{"role": "user", "content": "kill the kitchen lights please"}]}}))
    chunks = _sse(r.text)
    assert all(c.get("object") == "chat.completion.chunk" for c in chunks)
    assert "receipts" not in chunks[-1]


def test_non_stream_receipts_when_opted_in(brain, monkeypatch):
    _lights_off(monkeypatch)
    r, = _call(("POST", "/v1/chat/completions", {"json": {
        "hestia_events": True, "messages": [{"role": "user", "content": "kill the kitchen lights please"}]}}))
    assert r.json()["receipts"][0]["tool"] == "home"


def test_phrases_are_deterministic():
    from tool_phrases import phrase
    assert phrase("home", {"action": "turn_on", "entity_id": "light.tv"}) == "Turning on the tv…"
    assert phrase("weather", {}) == "Checking the weather…"
    assert phrase("shopping", {"action": "add"}) == "Adding to the shopping list…"
    assert phrase("records", {"action": "nonsense"}) == "Looking through the records…"
    assert phrase("mystery", None) == "Working on it…"
