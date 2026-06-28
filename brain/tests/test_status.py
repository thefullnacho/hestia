"""The status tool's contract: it must NEVER raise (a probe failing IS the signal, and the
agent loop expects a string back), it must advertise a well-formed schema, and `snapshot()`
must always return the dict shape the future /status web endpoint relies on — even fully
offline, where every service simply reports down. Network is not stubbed: the assertions
hold whether the stack is up or unreachable."""
from __future__ import annotations

import tools
from tools import status


def test_status_registered_and_advertised():
    assert "status" in [s["function"]["name"] for s in tools.SCHEMAS]


def test_status_dispatch_returns_string():
    out = tools.dispatch("status", {})
    assert isinstance(out, str) and out.startswith("Hestia status:")


def test_status_unknown_section_is_a_string_not_a_raise():
    assert tools.dispatch("status", {"section": "bogus"}) == "Error: unknown section 'bogus'."


def test_snapshot_shape():
    snap = status.snapshot()
    assert set(snap) >= {"services", "brain", "gpus", "system", "downloads"}
    assert isinstance(snap["services"], list)
    assert {"ollama_up", "model", "resident"} <= set(snap["brain"])
    # swap_pct is always present — it's the meltdown early-warning signal we never omit.
    assert "swap_pct" in snap["system"]
