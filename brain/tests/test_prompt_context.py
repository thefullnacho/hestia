"""Prompt-context selection: no unrelated HA work on the chat critical path."""
from __future__ import annotations

import asyncio

import hestia


def test_context_plan_skips_home_assistant_for_plain_chat(monkeypatch):
    monkeypatch.setattr(hestia.tools.skill, "match", lambda _: None)
    monkeypatch.setattr(hestia.records_store, "garden_lookup", lambda _: "")
    plan = hestia._context_plan("tell me a joke")
    assert plan["lights"] is False
    assert plan["soil"] is False


def test_context_plan_does_not_match_light_inside_an_unrelated_word(monkeypatch):
    monkeypatch.setattr(hestia.tools.skill, "match", lambda _: None)
    monkeypatch.setattr(hestia.records_store, "garden_lookup", lambda _: "")
    assert hestia._context_plan("that was a delightful answer")["lights"] is False


def test_context_plan_requests_light_catalog_for_home_control(monkeypatch):
    monkeypatch.setattr(hestia.tools.skill, "match", lambda _: {"name": "home_control"})
    monkeypatch.setattr(hestia.records_store, "garden_lookup", lambda _: "")
    plan = hestia._context_plan("turn off the kitchen lights")
    assert plan["lights"] is True
    assert plan["soil"] is False


def test_context_plan_requests_soil_for_garden_topic(monkeypatch):
    monkeypatch.setattr(hestia.tools.skill, "match", lambda _: {"name": "garden_bed"})
    monkeypatch.setattr(hestia.records_store, "garden_lookup", lambda _: "")
    plan = hestia._context_plan("should I water the carrots?")
    assert plan["lights"] is False
    assert plan["soil"] is True


def test_build_prompt_asks_home_for_only_selected_blocks(monkeypatch):
    seen = {}

    async def catalogs(*, lights: bool, soil: bool):
        seen.update(lights=lights, soil=soil)
        return "", ""

    monkeypatch.setattr(hestia, "_context_plan", lambda _: {
        "matched": None, "garden_focus": "", "garden_topic": False,
        "lights": False, "soil": False,
    })
    monkeypatch.setattr(hestia.tools.home, "context_catalogs", catalogs)
    prompt = asyncio.run(hestia._build_system_prompt("hello"))
    assert seen == {"lights": False, "soil": False}
    assert "--- LIGHT CATALOG ---" not in prompt
