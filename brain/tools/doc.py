"""`doc` tool: propose a house doc (docs_store.py, LIBRARY-DESIGN.md "House docs").

The model drafts; nothing is kept until a person taps Keep on Today. The turn's library citations
arrive in CITATIONS (set per call by the brain, the way recipe's SOURCE_CONTEXT is), so a source or
page anchor the library didn't return this turn is refused by code.
"""
from __future__ import annotations

from contextvars import ContextVar

import config  # noqa: F401  — ensures brain/ is on sys.path for the sibling import below
import docs_store

CITATIONS: ContextVar[list] = ContextVar("doc_citations", default=[])

SCHEMA = {
    "type": "function",
    "function": {
        "name": "doc",
        "description": (
            "Propose a house doc: the household's own page, kept in the library once a person "
            "approves it on the Today page. kind='guide' for a subject made for this house (a GMRS "
            "card for the family, a generator checklist), 'account' for something that happened "
            "(what went wrong in the storm), 'note' for marginalia on something the house owns (the "
            "choke sticks below 20F). Only when the person asks for a doc, a write-up, a card, a "
            "checklist or a note. Write the body in plain text with short headings. Draw on library "
            "passages first and list the ones you used in `sources`, exactly as cited. A note needs "
            "`about` (a name in the records); `page` pins it to one cited library page. The doc "
            "is only proposed: say it's waiting on Today, never that it's saved."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["propose"]},
                "kind": {"type": "string", "enum": list(docs_store.KINDS)},
                "title": {"type": "string", "description": "short title"},
                "body": {"type": "string", "description": "the doc itself, plain text with short headings"},
                "about": {"type": "string", "description": "the records name it is about (required for a note)"},
                "page": {"type": "string", "description": "one library passage, exactly as cited, to pin a note to"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "library passages used, exactly as cited (title, year, p.N)"},
            },
            "required": ["action", "kind", "title", "body"],
        },
    },
}


def execute(action: str, kind: str, title: str, body: str, about: str | None = None,
            page: str | None = None, sources: list | None = None) -> str:
    if action != "propose":
        return f"Error: unknown action '{action}' (use 'propose')."
    try:
        out = docs_store.propose(kind, title, body, about=about, page=page, sources=sources,
                                 allowed=CITATIONS.get())
    except ValueError as e:
        return f"Error: {e}."
    revises = f" It would replace the kept doc '{out['revises']}'." if out["revises"] else ""
    return (f"Proposed house doc #{out['id']}, '{' '.join(title.split())}'. It waits on the Today page "
            f"for Keep, Edit or Not this; nothing is in the library until then.{revises}")
