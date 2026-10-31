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
  - HESTIA_PERSON names who this window acts for: the audit line records it, and a reminder made
    here goes to that person's phone (reminders_tick.py).
  - recipe_review is offered here and never to the brain: a person can approve a recipe draft in
    their chat; the brain's model cannot approve anything.

Wire: newline-delimited JSON-RPC 2.0, the MCP stdio transport. Handled: initialize, ping,
tools/list and tools/call. Notifications are accepted and never answered.

Register it with Claude Code, from the repo root (local scope keeps it out of git). $PWD makes
the paths absolute, which matters: a relative path resolves against wherever the session starts,
so it connects from the repo root and fails from brain/:
    claude mcp add --scope local hestia -- uv run --project "$PWD/brain" python "$PWD/brain/mcp_server.py"
and add `--env HESTIA_MCP_WRITES=1` before the name to turn writes on. It is registered per
project, so a session started in another repo does not see it. To see it speak by hand:
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
    "goals": ("action", ("list", "show")),
    "home": ("action", ("get_state",)),
    "library": None,
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

# Tools only this server offers, never the brain: a person drives this window, so it can do what
# the brain's model must not. recipe_review approves a recipe draft once the person has read it
# in their chat and said yes; in the brain only the browser review page can (recipe_drafts.py).
RECIPE_REVIEW = {"type": "function", "function": {
    "name": "recipe_review",
    "description": ("Recipe drafts waiting for a person's approval. 'pending' lists them; 'show' returns one "
                    "draft in full (draft_id is the 32-character id in the /recipes/review/<id> link that "
                    "recipe save or import_url returns). 'approve' saves it to the collection exactly as "
                    "shown. Approve ONLY after showing the person the whole draft from 'show' and hearing "
                    "them say to approve it; to change anything, save a new draft instead. Set replace "
                    "only when the person agreed to overwrite a saved recipe with the same name."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["pending", "show", "approve"]},
        "draft_id": {"type": "string", "description": "The draft's 32-character id."},
        "replace": {"type": "boolean", "description": "Overwrite a saved recipe with the same name."}},
        "required": ["action"]}}}
MCP_ONLY = {"recipe_review": RECIPE_REVIEW}
GATES = {**READ_ONLY, "recipe_review": ("action", ("pending", "show"))}


def writes_enabled() -> bool:
    return os.environ.get("HESTIA_MCP_WRITES", "") not in ("", "0", "false", "False")


def _exposed() -> list[dict]:
    """The OpenAI-format schemas on offer right now. Read-only narrows each gated tool's action
    enum to its reading actions, and says so in the description."""
    out = []
    for schema in [*tools.SCHEMAS, *MCP_ONLY.values()]:
        name = schema["function"]["name"]
        if name not in GATES:
            continue
        schema = copy.deepcopy(schema)
        gate = GATES[name]
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
    gate = GATES.get(name)
    return bool(gate) and args.get(gate[0]) not in gate[1]


def _audit(name: str, args: dict) -> str | None:
    """Append the call to the audit log. Returns an error string if it could not be written,
    because a write nobody can account for is not allowed to happen."""
    line = json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                       "who": config.person() or "operator", "tool": name, "args": args})
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
    result = _recipe_review(args) if name == "recipe_review" else tools.dispatch(name, args)
    return result, result.startswith("Error")


def _recipe_review(args: dict) -> str:
    """List, show or approve a recipe draft. Approval takes the draft's first candidate exactly
    as 'show' printed it, through the same compare-and-swap the review page uses."""
    import re

    import recipe_drafts
    action, draft_id = args.get("action"), (args.get("draft_id") or "").strip()
    if action == "pending":
        rows = recipe_drafts.pending()
        return "\n".join(f"- {r['id']}: {', '.join(n for n in r['names'] if n) or 'unnamed'} ({r['created_at'][:10]})"
                         for r in rows) or "No recipe drafts are waiting."
    if not re.fullmatch(r"[a-f0-9]{32}", draft_id):
        return "Error: draft_id must be the 32-character id from the review link."
    try:
        entry = recipe_drafts.read(draft_id)
    except (FileNotFoundError, ValueError):
        return f"Error: no recipe draft {draft_id}."
    cand = (entry.get("candidates") or [{}])[0]
    name, content = (cand.get("name") or "").strip(), (cand.get("content") or "").strip()
    if not name or not content:
        return "Error: this draft has no recipe text to approve yet. Open its review link instead."
    saved = recipe_drafts.target(name)
    if action == "show":
        lines = [f"Draft {draft_id} ({entry.get('status', 'pending')}): {name}"]
        if cand.get("servings"):
            lines.append(f"Servings: {cand['servings']}")
        lines += ["", content, ""]
        lines += [f"Warning: {w}" for w in entry.get("warnings", [])]
        if saved["exists"]:
            lines.append(f"A saved recipe is already named '{name}'; approving needs replace=true.")
        return "\n".join(lines)
    try:
        recipe_drafts.approve(draft_id, revision=entry["revision"], name=name, content=content,
                              servings=cand.get("servings") or "", aliases=cand.get("aliases") or "",
                              expected_target_hash=saved["sha256"], replace=args.get("replace") is True,
                              acknowledged=True)
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"
    return f"Approved: '{name}' is in the recipe collection now."


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
                   "instructions": (f"Hestia's own tools, the same ones the home brain uses ({mode})."
                                    + (" A tie logged here files its pregnancy-check and whelp-watch reminders."
                                       if writes_enabled() else ""))})
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
