"""Operator MCP server: the brain's own tools, offered to the operator's Claude Code over stdio.

One brain, many windows. The chat the operator works in is one more window onto the same tool
layer, so it reads the records, files a reminder or logs a tie through the same
`tools.dispatch` the brain uses: the same argument validation and the same side effects (a tie
logged here files its follow-up reminders too). Nothing is duplicated, because the tool list is
built from `tools.SCHEMAS`.

Posture (SECURITY.md has the long version):
  - stdio only. No listener, so the network boundary does not move.
  - Read-only unless HESTIA_MCP_WRITES=1. In read-only mode each tool's action enum is narrowed
    to the actions that only read, so the client is shown what it can actually do.
  - Default deny: only the tools named in READ_ONLY are offered. `search` reaches the open web
    and is left out, and there is no shell tool in the registry to offer.
  - A write-mode call that changes anything appends one line (tool and arguments, never the
    result) to an audit log, and is refused if that line cannot be written.
  - The client's own tool approvals still stand in front of every call.

Wire: newline-delimited JSON-RPC 2.0, the MCP stdio transport. Handled: initialize, ping,
tools/list and tools/call. Notifications are accepted and never answered.

Register it with Claude Code, from the repo root (local scope keeps it out of git):
    claude mcp add --scope local hestia -- uv run --project brain python brain/mcp_server.py
and add `--env HESTIA_MCP_WRITES=1` before the name to turn writes on. To see it speak by hand:
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | uv run --project brain python brain/mcp_server.py
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import sys
from pathlib import Path

import config  # puts brain/ on sys.path + owns paths
import tools
from tool_contract import validate

SERVER = {"name": "hestia", "version": "0.1.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first; an unknown ask gets the first
AUDIT = Path(os.environ.get("HESTIA_MCP_AUDIT") or config.DATA_DIR / "mcp_audit.jsonl")

# tool -> (the argument that picks the action, the actions that only read). None means the tool
# only ever reads. A registry tool absent from this table and from EXCLUDED fails the test that
# guards it, so adding a tool to the brain forces a decision about whether the operator gets it.
READ_ONLY: dict[str, tuple[str, tuple[str, ...]] | None] = {
    "calendar": ("action", ("show",)),
    "home": ("action", ("get_state",)),
    "media": ("action", ("status",)),
    "memory": ("op", ("recall",)),
    "records": ("action", ("recent", "entity", "due")),
    "recipe": ("action", ("lookup", "list")),
    "reminder": ("action", ("list",)),
    "shopping": ("action", ("show",)),
    "status": None,
    "weather": None,
}
EXCLUDED = ("search",)  # reaches the open web; the operator's Claude Code has its own


def writes_enabled() -> bool:
    return os.environ.get("HESTIA_MCP_WRITES", "") not in ("", "0", "false", "False")


def _exposed() -> list[dict]:
    """The OpenAI-format schemas on offer right now. Read-only narrows each gated tool's action
    enum to its reading actions, and says so in the description."""
    out = []
    for schema in tools.SCHEMAS:
        name = schema["function"]["name"]
        if name not in READ_ONLY:
            continue
        schema = copy.deepcopy(schema)
        gate = READ_ONLY[name]
        if gate and not writes_enabled():
            arg, allowed = gate
            schema["function"]["parameters"]["properties"][arg]["enum"] = list(allowed)
            schema["function"]["description"] = "(read-only here) " + schema["function"]["description"]
        out.append(schema)
    return out


def _tool_list() -> list[dict]:
    return [{"name": s["function"]["name"], "description": s["function"]["description"],
             "inputSchema": s["function"]["parameters"]} for s in _exposed()]


def _writes(name: str, args: dict) -> bool:
    """Does this call change anything? A tool with no gate only reads."""
    gate = READ_ONLY.get(name)
    return bool(gate) and args.get(gate[0]) not in gate[1]


def _audit(name: str, args: dict) -> str | None:
    """Append the call to the audit log. Returns an error string if it could not be written,
    because a write nobody can account for is not allowed to happen."""
    line = json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "tool": name, "args": args})
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(AUDIT, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line + "\n")
    except OSError as e:
        return f"Error: the write was not made because the audit log could not be written ({type(e).__name__})."
    return None


def _call(name, args) -> tuple[str, bool]:
    """Run one tool call. Returns (text, is_error); never raises."""
    schemas = _exposed()
    if name not in {s["function"]["name"] for s in schemas}:
        return f"Error: no such tool '{name}' on this server.", True
    if not isinstance(args, dict):
        return "Error: tool arguments must be a JSON object.", True
    if not writes_enabled() and _writes(name, args):
        return (f"Error: {name} is read-only here. Writes are off; the operator turns them on "
                "with HESTIA_MCP_WRITES=1."), True
    if error := validate(name, args, schemas):
        return error, True
    if _writes(name, args) and (error := _audit(name, args)):
        return error, True
    result = tools.dispatch(name, args)
    return result, result.startswith("Error")


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, its response out. None for a notification or a stray response."""
    mid = msg.get("id")
    if mid is None or "method" not in msg:
        return None
    method, params = msg["method"], msg.get("params") or {}

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        ask = params.get("protocolVersion")
        mode = "read-write" if writes_enabled() else "read-only"
        return ok({"protocolVersion": ask if ask in PROTOCOLS else PROTOCOLS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER,
                   "instructions": (f"Hestia's own tools, the same ones the home brain uses ({mode}). "
                                    "A tie logged here files its pregnancy-check and whelp-watch reminders.")})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list()})
    if method == "tools/call":
        text, is_error = _call(params.get("name"), params.get("arguments") or {})
        return ok({"content": [{"type": "text", "text": text}], "isError": is_error})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(inp, out) -> None:
    """Read one JSON message per line until the client closes the pipe, answer each."""
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if not isinstance(msg, dict):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
            else:
                try:
                    reply = handle(msg)
                except Exception as e:  # noqa: BLE001 (one bad message must not end the session)
                    reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                             "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}}
        if reply is not None:
            out.write(json.dumps(reply, separators=(",", ":")) + "\n")
            out.flush()


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")
    out = sys.stdout
    sys.stdout = sys.stderr  # a stray print from a tool must never land in the protocol stream
    print(f"hestia-mcp: {'read-write' if writes_enabled() else 'read-only'}, "
          f"{len(_exposed())} tools, audit {AUDIT}", file=sys.stderr, flush=True)
    serve(sys.stdin, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
