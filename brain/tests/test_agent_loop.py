"""The agent loop's failure paths — the brain's core behaviour, previously untested.

A backend that is down, or a model that emits garbage arguments, must still produce an
answer. Neither may reach the client as an HTTP 500."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import hestia


@pytest.fixture(autouse=True)
def no_system_prompt(monkeypatch):
    """Keep loop failure tests independent of prompt-context construction."""
    async def empty_prompt(_: str) -> str:
        return ""
    monkeypatch.setattr(hestia, "_build_system_prompt", empty_prompt)


def _run(text: str = "hi") -> str:
    return asyncio.run(hestia.run_agent([{"role": "user", "content": text}]))


def test_ollama_outage_returns_graceful_answer(monkeypatch):
    async def boom(convo, schemas):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hestia, "_ollama_chat", boom)
    assert _run() == hestia._BACKEND_DOWN


def test_ollama_http_error_returns_graceful_answer(monkeypatch):
    async def boom(convo, schemas):
        raise httpx.HTTPStatusError(
            "500", request=httpx.Request("POST", "http://x"),
            response=httpx.Response(500))

    monkeypatch.setattr(hestia, "_ollama_chat", boom)
    assert _run() == hestia._BACKEND_DOWN


def test_backend_down_answer_is_never_note_taken():
    """A give-up string is not a durable fact about the household."""
    assert hestia._BACKEND_DOWN in hestia._NO_LEARN


def test_malformed_tool_args_are_refused_not_run(monkeypatch):
    called: list[tuple] = []
    replies = [
        {"content": "", "tool_calls": [
            {"function": {"name": "records", "arguments": "{not json"}}]},
        {"content": "All set."},
    ]

    async def fake_chat(convo, schemas):
        return replies.pop(0)

    monkeypatch.setattr(hestia, "_ollama_chat", fake_chat)
    monkeypatch.setattr(hestia.tools, "dispatch",
                        lambda name, args: called.append((name, args)) or "ran")
    out = _run("log something")
    assert out == "All set."
    assert called == []           # the refusal path must not execute the tool


def test_timed_out_tool_holds_its_worker_slot_until_it_really_finishes(monkeypatch):
    """A timeout returns to the user but cannot create an unbounded queue of live workers."""
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def slow_then_fast(name, args):
        calls.append((name, args))
        if len(calls) == 1:
            started.set()
            release.wait(timeout=1)
            finished.set()
        return "done"

    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(hestia, "_tool_executor", pool)
    monkeypatch.setattr(hestia, "_tool_slots", asyncio.BoundedSemaphore(1))
    monkeypatch.setattr(hestia.tools, "dispatch", slow_then_fast)

    async def scenario():
        first = asyncio.create_task(hestia._run_tool("search", {}, 0.02))
        await asyncio.to_thread(started.wait, 0.2)
        assert await first == ("Error: search timed out after 0s (backend slow/unreachable).", "timeout")
        # The first worker is still live, so another tool cannot queue behind it.
        _, outcome = await hestia._run_tool("weather", {}, 0.02)
        assert outcome == "capacity"
        assert len(calls) == 1
        release.set()
        await asyncio.to_thread(finished.wait, 0.2)
        await asyncio.sleep(0)  # let the executor completion callback release the slot
        assert await hestia._run_tool("weather", {}, 0.2) == ("done", "ok")

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        pool.shutdown(wait=True)
