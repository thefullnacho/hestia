"""The agent loop's failure paths — the brain's core behaviour, previously untested.

A backend that is down, or a model that emits garbage arguments, must still produce an
answer. Neither may reach the client as an HTTP 500."""
from __future__ import annotations

import asyncio

import httpx
import pytest

import hestia


@pytest.fixture(autouse=True)
def no_system_prompt(monkeypatch):
    """The real prompt builder calls Home Assistant for the light/soil catalog."""
    monkeypatch.setattr(hestia, "_system_prompt", lambda t: "")


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
