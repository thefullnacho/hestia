"""A reply may only say something was done when a tool did it; a bare link gets a fixed answer.

Both from a real morning: a pasted Facebook link got "imported into your recipe collection as
a draft" and "Remind to start the seeds January 2" got "Reminder #23 set", with no tool call
behind either. Nothing was saved, and nobody would have known until January.
"""
from __future__ import annotations

import asyncio

import pytest

import hestia


def _run(monkeypatch, user_text, reply):
    calls = []

    async def chat(*_):
        calls.append(1)
        return {"content": reply}
    monkeypatch.setattr(hestia, "_ollama_chat", chat)
    answer = asyncio.run(hestia.run_agent([{"role": "user", "content": user_text}]))
    return answer, calls


@pytest.mark.parametrize("user_text, reply", [
    ("Remind to start the seeds January 2, 2027",
     "Reminder #23 set — I'll ping your phone Fri Jan 2 at 9:00 AM: start the seeds"),
    ("here's the winter sowing write-up from the extension office",
     "I have imported the information from that link into your recipe collection as a draft for you to review."),
    ("milk", "I've added milk to your shopping list."),
    ("dentist friday at 2", "Done, it's on the calendar for Friday."),
])
def test_a_claimed_action_with_no_tool_call_is_never_shown(monkeypatch, user_text, reply):
    answer, _ = _run(monkeypatch, user_text, reply)
    assert answer == hestia._UNDONE_CLAIM


@pytest.mark.parametrize("reply", [
    "You could set a reminder for early January to amend the soil.",
    "Would you like me to add that to your shopping list?",
    "Blueberries like acidic soil, around pH 4.5 to 5.5.",
    "I've put together a summary of the winter sowing method below.",
])
def test_advice_offers_and_answers_pass_through(monkeypatch, reply):
    answer, _ = _run(monkeypatch, "when should I amend the blueberry soil", reply)
    assert answer == reply


@pytest.mark.parametrize("text", [
    "https://www.facebook.com/share/r/19Z9cwYhxX/?mibextid=wwXIfr",
    "Winter sowing link: https://www.facebook.com/share/r/1HnrUqGzPz/?mibextid=wwXIfr",
])
def test_a_bare_link_gets_the_import_instructions_without_the_model(monkeypatch, text):
    answer, calls = _run(monkeypatch, text, "I have imported it into your recipe collection.")
    assert answer == hestia._LINK_ONLY_REPLY
    assert calls == []


def test_a_link_with_a_request_still_reaches_the_model(monkeypatch):
    answer, calls = _run(monkeypatch, "save this recipe https://example.com/chili",
                         "Which part should I keep?")
    assert calls and answer == "Which part should I keep?"


def test_an_undone_claim_stays_out_of_the_thread():
    assert hestia._UNDONE_CLAIM in hestia._NO_LEARN


# The shapes smaller models claim in (2026-10-29: Nanbeige 4.2 3B, Gemma 4 E4B and E2B, each
# with no tool call behind the sentence). The guard was tuned on the resident model's phrasing
# and missed seven of these nine.
@pytest.mark.parametrize("reply", [
    "I have saved that the good coffee is the bag with the orange label.",
    "Saved that to memory — the good coffee comes in the orange bag.",
    "I have recorded Biscuit as a corgi pet in our records.",
    "I have logged that you vaccinated the dogs today.",
    "Turning off the kitchen lights.",
    "Grabbing the latest season of Severance for you now.",
    "It's queued and downloading in the background.",
    "Okay, I've turned off the porch light.",
])
def test_small_model_claims_are_caught(reply):
    assert hestia._claims_action(reply)


@pytest.mark.parametrize("reply", [
    "Turning off the water before a freeze protects the pipes.",
    "Turning the compost every week helps it heat up.",
    "Saved seeds keep for years in a cool, dry jar.",
    "Downloading is slow over the hotspot, so it may take a while.",
    "I can save that to memory if you like.",
    "You told me the good coffee is the bag with the orange label.",
])
def test_small_model_advice_shapes_still_pass(reply):
    assert not hestia._claims_action(reply)


def test_a_claim_with_no_call_gets_one_try_at_the_real_call(monkeypatch):
    """The model claims the save, is told nothing ran, and then makes the call."""
    replies = [{"content": "I have saved that the good coffee is the orange bag."},
               {"tool_calls": [{"function": {"name": "memory", "arguments": {
                   "op": "write", "content": "The good coffee is the bag with the orange label."}}}]},
               {"content": "Noted."}]
    async def chat(*_):
        return replies.pop(0)
    monkeypatch.setattr(hestia, "_ollama_chat", chat)
    ran = []
    monkeypatch.setattr(hestia.tools, "dispatch", lambda name, args: ran.append(name) or "remembered")
    asyncio.run(hestia.run_agent([{"role": "user", "content":
        "Remember that the good coffee is the bag with the orange label."}]))
    assert ran == ["memory"]


def test_a_claim_twice_is_refused(monkeypatch):
    calls = []
    async def chat(*_):
        calls.append(1)
        return {"content": "I have saved that the good coffee is the orange bag."}
    monkeypatch.setattr(hestia, "_ollama_chat", chat)
    monkeypatch.setattr(hestia.tools, "dispatch", lambda name, args: pytest.fail("nothing may run"))
    answer = asyncio.run(hestia.run_agent([{"role": "user", "content":
        "Remember that the good coffee is the bag with the orange label."}]))
    assert answer == hestia._UNDONE_CLAIM
    assert len(calls) == 2
