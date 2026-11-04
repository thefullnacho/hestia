"""guards.said_words: a light switch is held to the person's own words."""
import asyncio

import pytest

import backend
import guards
import hestia
import tools

AREAS = ["kitchen", "outside"]
check = guards.said_words


def home(action, entity="light.light_outside_lights"):
    return {"action": action, "entity_id": entity}


@pytest.mark.parametrize("text,args", [
    ("put on the outside", home("turn_off", "light.light_kitchen_lights")),   # the 4 misses
    ("flip on the outside", home("turn_off")),
    ("kill the outside", home("turn_on")),
    ("turn the lights out", home("turn_on")),
    ("shut off the outside", home("turn_off", "light.light_kitchen_lights")),
])
def test_a_switch_against_the_words_is_sent_back(text, args):
    assert check("home", args, text, AREAS).startswith("Not run:")


@pytest.mark.parametrize("text,args", [
    ("put on the outside", home("turn_on")),
    ("turn off the outside lights", home("turn_off", "light.outside_lights")),     # resolver tidies it
    ("turn off the kitchen and turn on the outside", home("turn_on")),             # mixed: no action check
    ("turn it back on", home("turn_on", "light.light_kitchen_lights")),            # no area named
    ("lights off please", home("turn_off", "light.light_kitchen_lights")),
    ("dim the kitchen to 30", {"action": "set_brightness", "entity_id": "light.light_kitchen_lights"}),
    ("put on the outside", home("turn_on", None)),                                 # validation's job
    ("what's on outside tonight", {"action": "get_state"}),
])
def test_calls_that_agree_or_are_ambiguous_pass(text, args):
    assert check("home", args, text, AREAS) is None


def test_other_tools_are_never_checked():
    assert check("reminder", {"action": "create", "text": "turn off the oven"}, "remind me to turn on", AREAS) is None


def test_a_refused_switch_never_runs_and_the_retry_does(monkeypatch):
    """Through the loop: the wrong light is sent back, the model calls again, only that one runs."""
    replies = iter([
        {"tool_calls": [{"function": {"name": "home", "arguments": home("turn_off", "light.light_kitchen_lights")}}]},
        {"tool_calls": [{"function": {"name": "home", "arguments": home("turn_on")}}]},
        {"content": "Outside lights on."},
    ])
    seen = []

    async def chat(messages, schemas):
        seen.append(messages[-1])
        return next(replies)

    ran = []
    monkeypatch.setattr(backend, "chat", chat)
    monkeypatch.setattr(tools.home, "light_areas", lambda: AREAS)
    monkeypatch.setattr(tools, "dispatch", lambda name, args: ran.append(args) or "Done - Outside lights are on.")
    monkeypatch.setattr(hestia, "_build_system_prompt", lambda text: asyncio.sleep(0, result="system"))
    answer = asyncio.run(hestia.run_agent([{"role": "user", "content": "put on the outside"}]))
    assert ran == [home("turn_on")]
    assert seen[1]["role"] == "tool" and "Not run" in seen[1]["content"]
    assert "Outside" in answer
