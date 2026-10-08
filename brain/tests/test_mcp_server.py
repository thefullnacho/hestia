import datetime as dt
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_server
import reminders_store
import tools


@pytest.fixture(autouse=True)
def _scratch_audit(tmp_path, monkeypatch):
    """The audit log is never the real one, and writes start off."""
    monkeypatch.setattr(mcp_server, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.delenv("HESTIA_MCP_WRITES", raising=False)


def rpc(method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return mcp_server.handle(msg)


def call(tool, /, **args):
    out = rpc("tools/call", {"name": tool, "arguments": args})["result"]
    return out["content"][0]["text"], out["isError"]


def listed():
    return {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}


def test_the_handshake_names_the_server_and_echoes_a_supported_protocol():
    out = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})["result"]
    assert out["protocolVersion"] == "2025-03-26"
    assert out["capabilities"]["tools"] == {"listChanged": False}
    assert out["serverInfo"]["name"] == "hestia" and "read-only" in out["instructions"]
    assert rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == mcp_server.PROTOCOLS[0]
    assert rpc("ping", mid=0)["result"] == {}  # id 0 is a request, not a notification
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc("nope")["error"]["code"] == -32601


def test_every_registry_tool_is_either_offered_or_deliberately_left_out():
    registry = set(tools._TOOLS)
    assert registry == set(mcp_server.READ_ONLY) | set(mcp_server.EXCLUDED), \
        "a tool was added or renamed: decide whether the operator gets it (READ_ONLY) or not (EXCLUDED)"
    assert not set(mcp_server.READ_ONLY) & set(mcp_server.EXCLUDED)


def test_the_read_only_table_names_real_arguments_and_real_actions():
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in tools.SCHEMAS}
    for name, gate in mcp_server.READ_ONLY.items():
        if gate is None:
            continue
        arg, allowed = gate
        assert set(allowed) <= set(schemas[name]["properties"][arg]["enum"]), name


def test_read_only_offers_only_the_tables_tools_with_each_enum_narrowed():
    offered = listed()
    assert set(offered) == set(mcp_server.READ_ONLY) and "search" not in offered
    assert offered["records"]["inputSchema"]["properties"]["action"]["enum"] == ["recent", "entity", "due"]
    assert offered["memory"]["inputSchema"]["properties"]["op"]["enum"] == ["recall"]
    assert offered["records"]["description"].startswith("(read-only here)")
    assert not offered["status"]["description"].startswith("(read-only here)")  # it only ever reads
    # the brain's own schema object is untouched by the narrowing
    full = next(s for s in tools.SCHEMAS if s["function"]["name"] == "records")
    assert "log" in full["function"]["parameters"]["properties"]["action"]["enum"]


def test_writes_mode_offers_every_action_but_still_not_search(monkeypatch):
    monkeypatch.setenv("HESTIA_MCP_WRITES", "1")
    offered = listed()
    assert "log" in offered["records"]["inputSchema"]["properties"]["action"]["enum"]
    assert not offered["records"]["description"].startswith("(read-only here)")
    assert "search" not in offered
    monkeypatch.setenv("HESTIA_MCP_WRITES", "0")
    assert "log" not in listed()["records"]["inputSchema"]["properties"]["action"]["enum"]


def test_a_read_call_goes_through_the_brains_own_tool(db):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    text, is_error = call("records", action="entity", name="Juniper")
    assert "Juniper" in text and not is_error
    assert call("reminder", action="list") == (tools.dispatch("reminder", {"action": "list"}), False)


def test_a_write_is_refused_while_read_only_and_nothing_is_written(db, tmp_path):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    text, is_error = call("records", action="log", kind="note", subject="Juniper", did="weighed")
    assert is_error and "read-only" in text and "HESTIA_MCP_WRITES=1" in text
    assert db.recent_events(subject="Juniper") == []
    assert not (tmp_path / "audit.jsonl").exists()
    # the doors a client might try instead
    assert call("records")[1]                                  # no action at all
    assert call("records", action=["log"])[1]                  # an action that is not a string
    assert call("memory", op="write", text="x")[1]             # the other tools' gates hold too
    assert call("reminder", action="create", text="x", when="in 5 minutes")[1]
    assert reminders_store.pending() == []
    assert call("search", action="search", query="x")[1]       # left out entirely
    assert call("bash", command="id")[1]                       # there is no such tool
    out = rpc("tools/call", {"name": "records", "arguments": "not an object"})["result"]
    assert out["isError"]


def test_a_write_when_enabled_takes_the_brains_path_and_is_audited(db, monkeypatch, tmp_path):
    monkeypatch.setenv("HESTIA_MCP_WRITES", "1")
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    when = (dt.datetime.now() - dt.timedelta(days=3)).replace(microsecond=0).isoformat()
    text, is_error = call("records", action="log", kind="breeding", subject="Juniper", did="tied", ts=when)
    assert not is_error and "Reminders set: Juniper pregnancy check" in text  # the hook ran, as in the brain
    assert len(reminders_store.pending()) == 2
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "records" and entry["args"]["action"] == "log" and "Reminders set" not in lines[0]
    assert oct((tmp_path / "audit.jsonl").stat().st_mode & 0o777) == "0o600"
    call("records", action="recent")  # a read in write mode is not a write
    call("status")
    assert len((tmp_path / "audit.jsonl").read_text().splitlines()) == 1


def test_a_write_nobody_can_account_for_is_not_made(db, monkeypatch, tmp_path):
    monkeypatch.setenv("HESTIA_MCP_WRITES", "1")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory should be")
    monkeypatch.setattr(mcp_server, "AUDIT", blocker / "audit.jsonl")
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    text, is_error = call("records", action="log", kind="note", subject="Juniper", did="weighed")
    assert is_error and "audit log could not be written" in text
    assert db.recent_events(subject="Juniper") == []


def test_serve_survives_garbage_and_answers_every_request_in_order():
    inp = io.StringIO("\n".join([
        "this is not json",
        "[1, 2]",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "",
        json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/list"}),
    ]) + "\n")
    out = io.StringIO()
    mcp_server.serve(inp, out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [None, None, 7, 8]
    assert replies[0]["error"]["code"] == -32700 and replies[1]["error"]["code"] == -32600
    assert replies[2]["result"] == {} and replies[3]["result"]["tools"]


def test_over_a_real_stdio_pipe_only_json_reaches_stdout(tmp_path):
    brain = Path(__file__).resolve().parent.parent
    env = {**os.environ, "HESTIA_DB": str(tmp_path / "hestia.db"), "HESTIA_MCP_AUDIT": str(tmp_path / "audit.jsonl")}
    env.pop("HESTIA_MCP_WRITES", None)
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "records", "arguments": {"action": "recent"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "records", "arguments": {"action": "log", "kind": "note", "subject": "x"}}},
    ]
    run = subprocess.run([sys.executable, "mcp_server.py"], cwd=brain, env=env, timeout=90, capture_output=True,
                         text=True, input="\n".join(json.dumps(m) for m in lines) + "\n")
    replies = [json.loads(line) for line in run.stdout.splitlines()]  # every stdout line must parse
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["serverInfo"]["name"] == "hestia"
    assert "records" in {t["name"] for t in replies[1]["result"]["tools"]}
    assert replies[2]["result"]["isError"] is False
    assert replies[3]["result"]["isError"] is True and "read-only" in replies[3]["result"]["content"][0]["text"]
    assert "hestia-mcp: read-only" in run.stderr
    assert not (tmp_path / "audit.jsonl").exists()
