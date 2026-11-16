"""Web answers name their sources, and the app's thinking button makes one turn think (Ollama only)."""
import asyncio

import backend
import guards
import hestia
from tools import search

RESULTS = ("Top web results for 'garlic':\n"
           "1. Growing Garlic | UMN Extension\n   https://extension.umn.edu/garlic\n   Plant in fall.\n"
           "2. Garlic - Wikipedia\n   https://en.wikipedia.org/wiki/Garlic\n   A species.\n"
           "3. Garlic tips\n   https://example.com/g\n   x\n"
           "4. More garlic\n   https://example.org/m\n   y")


def test_sources_from_results_and_from_a_read_page():
    assert guards.web_sources(RESULTS)[0] == ("Growing Garlic | UMN Extension", "https://extension.umn.edu/garlic")
    assert guards.web_sources("Readable text from https://a.org/x:\nbody") == [("", "https://a.org/x")]
    assert guards.web_sources("No web results for 'q'.") == []
    assert guards.web_sources("Error: fetch is limited to public web pages") == []


def test_addendum_names_up_to_three_and_a_read_page_first():
    srcs = guards.web_sources(RESULTS)
    line = guards.web_addendum("Plant cloves in October.", srcs)
    assert line.startswith("Sources: Growing Garlic") and line.count("https://") == 3
    read = srcs + [("", "https://extension.umn.edu/garlic-page")]
    assert guards.web_addendum("Plant cloves in October.", read) == "Sources: https://extension.umn.edu/garlic-page."


def test_no_addendum_when_the_answer_names_a_source_or_found_nothing():
    srcs = guards.web_sources(RESULTS)
    assert guards.web_addendum("Per extension.umn.edu, plant in fall.", srcs) == ""
    assert guards.web_addendum("I couldn't find anything useful on that.", srcs) == ""
    assert guards.web_addendum("Plant in fall.", []) == ""


def test_loop_appends_web_sources(monkeypatch):
    async def prompt(_):
        return ""
    monkeypatch.setattr(hestia, "_build_system_prompt", prompt)
    monkeypatch.setattr(search, "_search", lambda q, n=6: RESULTS)
    replies = [{"content": "", "tool_calls": [{"function": {"name": "search",
                                                            "arguments": {"action": "search", "query": "garlic"}}}]},
               {"content": "Plant cloves in October."}]

    async def fake_chat(convo, schemas):
        return replies.pop(0)
    monkeypatch.setattr(backend, "ollama_chat", fake_chat)
    out = asyncio.run(hestia.run_agent([{"role": "user", "content": "search the web: when to plant garlic"}]))
    assert out.startswith("Plant cloves in October.\nSources: Growing Garlic")


def test_a_thinking_turn_gets_room_for_its_thinking():
    t = hestia.TurnTrace()
    t.think = True
    assert backend.body_think(t) is True
    assert backend.THINK_OUTPUT_TOKENS > backend.OUTPUT_TOKENS
    assert hestia.THINK_TURN_BUDGET > hestia.TURN_BUDGET


def _think_seen(monkeypatch, backend_name, flag):
    seen = []

    async def fake_agent(messages, *, trace=None):
        seen.append(trace.think)
        return "ok"
    monkeypatch.setattr(hestia, "run_agent", fake_agent)
    monkeypatch.setattr(backend, "BACKEND", backend_name)
    from fastapi.testclient import TestClient
    body = {"messages": [{"role": "user", "content": "hard question"}], "stream": False}
    if flag is not None:
        body["hestia_think"] = flag
    TestClient(hestia.app).post("/v1/chat/completions", json=body)
    return seen


def test_the_button_turns_thinking_on_for_ollama_only(db, monkeypatch):
    assert _think_seen(monkeypatch, "ollama", True) == [True]
    assert _think_seen(monkeypatch, "ollama", None) == [backend.THINK]
    assert _think_seen(monkeypatch, "ollama", "yes") == [backend.THINK]     # only a real true
    assert _think_seen(monkeypatch, "phone", True) == [backend.THINK]
