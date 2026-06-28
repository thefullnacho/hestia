"""`memory` tool — the brain's durable, inspectable memory (see MEMORY-DESIGN.md)."""
from __future__ import annotations

import config  # noqa: F401  — ensures brain/ is on sys.path for the sibling import below
import memory_store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory",
        "description": ("Your long-term memory for the user's durable facts and preferences. "
                        "op='write' to save a fact about the user, household, or preferences (NOT "
                        "transient state like whether a light is on — that's queried live). "
                        "op='recall' to look something up — ALWAYS call recall when the user asks "
                        "what they previously told you or to retrieve a saved fact (e.g. 'which "
                        "coffee did I say was the good one?'); never answer such a question from a "
                        "guess or say you don't know without checking. For an entity or a dated "
                        "event, prefer the `records` tool over this."),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["write", "recall"]},
                "content": {"type": "string", "description": "For write: the fact. For recall: the search query."},
                "type": {"type": "string",
                         "enum": ["person", "household", "preference", "routine", "work", "reference", "episodic"],
                         "description": "optional category for write"},
            },
            "required": ["op", "content"],
        },
    },
}


def execute(op: str, content: str, type: str | None = None) -> str:
    if op == "write":
        rid = memory_store.write(content, type=type or "preference", source="agent")
        return f"Remembered (id={rid})."
    if op == "recall":
        hits = memory_store.recall(content)
        if not hits:
            return "Nothing relevant in memory."
        return "\n".join(f"- ({h['meta'].get('type', '?')}) {h['body']}" for h in hits)
    return f"Error: unknown memory op '{op}'."
