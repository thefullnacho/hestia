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


def test_dispatch_tool_exception_returns_error_string(db, monkeypatch):
    """A broken store must not escape dispatch — the loop expects a string, never a raise."""
    import reminders_store

    def boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(reminders_store, "pending", boom)
    out = tools.dispatch("reminder", {"action": "list"})
    assert out.startswith("Error: reminder failed:")
    assert "database is locked" in out


def test_dispatch_internal_typeerror_is_not_blamed_on_arguments(mem, monkeypatch):
    """A TypeError raised *inside* a tool is a bug, not bad args — don't invite a retry."""
    import memory_store

    def boom(*a, **k):
        raise TypeError("unsupported operand type(s)")

    monkeypatch.setattr(memory_store, "recall", boom)
    out = tools.dispatch("memory", {"op": "recall", "content": "porch light"})
    assert out.startswith("Error: memory failed:")
    assert "bad arguments" not in out
