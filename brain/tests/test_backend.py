"""backend.complete(): the one door for no-tools completions, on both wire formats."""
import asyncio
import re

import pytest

import backend


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return self

    def json(self):
        return self.payload


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        if url.endswith("/v1/chat/completions"):
            return _Response({"choices": [{"message": {"content": "hi"}, "finish_reason": "length"}]})
        return _Response({"message": {"content": "hi"}, "done_reason": "stop"})

    monkeypatch.setattr(backend.httpx, "post", post)
    return calls


def test_ollama_gets_its_own_options_and_never_thinks(sent, monkeypatch):
    monkeypatch.setattr(backend, "BACKEND", "ollama")
    out = backend.complete([{"role": "user", "content": "x"}], model="m", temperature=0.2,
                           num_ctx=4096, max_tokens=64, json_format="json")
    url, body = sent[0]
    assert url.endswith("/api/chat") and body["model"] == "m" and body["think"] is False
    assert body["options"] == {"temperature": 0.2, "num_ctx": 4096, "num_predict": 64}
    assert body["format"] == "json"
    assert out == {"content": "hi", "truncated": False}


def test_one_caller_can_point_at_another_ollama(sent, monkeypatch):
    monkeypatch.setattr(backend, "BACKEND", "ollama")
    backend.complete([{"role": "user", "content": "x"}], ollama="http://elsewhere:11434/")
    assert sent[0][0] == "http://elsewhere:11434/api/chat"


def test_openai_mode_speaks_openai_and_reports_truncation(sent, monkeypatch):
    monkeypatch.setattr(backend, "BACKEND", "openai")
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    out = backend.complete([{"role": "user", "content": "x"}], model="ignored", max_tokens=64,
                           num_ctx=4096, json_format=schema)
    url, body = sent[0]
    assert url.endswith("/v1/chat/completions") and body["model"] == backend.OPENAI_MODEL
    assert body["max_tokens"] == 64 and "options" not in body and "think" not in body
    assert body["response_format"]["json_schema"]["schema"] == schema
    assert out == {"content": "hi", "truncated": True}


def test_acomplete_uses_the_same_request(monkeypatch):
    monkeypatch.setattr(backend, "BACKEND", "ollama")
    seen = []

    class Client:
        async def post(self, path, json):
            seen.append((path, json))
            return _Response({"message": {"content": "{}"}, "done_reason": "length"})

    monkeypatch.setattr(backend, "client", Client())
    out = asyncio.run(backend.acomplete([{"role": "user", "content": "x"}], temperature=0, max_tokens=8))
    assert seen[0][0] == "/api/chat" and seen[0][1]["options"] == {"temperature": 0, "num_predict": 8}
    assert out == {"content": "{}", "truncated": True}


def test_nothing_else_in_the_brain_posts_to_a_model_server():
    from pathlib import Path
    brain = Path(backend.__file__).parent
    for path in brain.glob("*.py"):
        if path.name == "backend.py" or path.name.startswith(("eval_", "sft_")):
            continue
        text = path.read_text()
        # The endpoint as a string literal ends in a quote; prose naming it in a docstring doesn't.
        assert not re.search(r"/api/(?:chat|generate)['\"]", text), path.name
