"""Tool dispatch — the registry contract the agent loop depends on: unknown tools and
bad arguments must come back as readable strings (never raise), and a real tool must
round-trip through dispatch. Uses only the offline, DB-backed tools (records/memory)."""
from __future__ import annotations

import tools


def test_dispatch_unknown_tool_returns_error_string():
    assert tools.dispatch("nope", {}) == "Error: no such tool 'nope'."


def test_dispatch_bad_arguments_returns_error_string():
    out = tools.dispatch("records", {"action": "remember", "bogus": 1})
    assert out.startswith("Error: bad arguments for records")


def test_dispatch_records_unknown_action():
    out = tools.dispatch("records", {"action": "frobnicate"})
    assert out == "Error: unknown action 'frobnicate'."


def test_dispatch_records_round_trip(db):
    assert "Remembered Momo" in tools.dispatch(
        "records", {"action": "remember", "name": "Momo", "kind": "pet"})
    profile = tools.dispatch("records", {"action": "entity", "name": "Momo"})
    assert "Momo" in profile and "pet" in profile


def test_dispatch_memory_round_trip(mem):
    assert "Remembered" in tools.dispatch(
        "memory", {"op": "write", "content": "the porch light should be dim"})
    assert "porch light" in tools.dispatch(
        "memory", {"op": "recall", "content": "porch light"})
