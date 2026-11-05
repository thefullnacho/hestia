"""`library` tool: passages from the household's extension publications, with citations.

The operator chose the documents (data/library/, gitignored; LIBRARY-DESIGN.md). Code finds the
passages and the model answers from them. Two rules live here in code, not only in the prompt:
every passage carries its title, year and page so the answer can cite it (hestia.py adds the
citation if the reply leaves it out), and medication passages come with the standing note that
dose, substitution and stopping questions go to a pharmacist or doctor. Read-only, local.
"""
from __future__ import annotations

import os
import re

import library_store

K = 5
# Best-match similarity below which the shelf has nothing on the question. Picked 2026-10-24 on
# 128 real and 11 not-in-the-library questions (LIBRARY-DESIGN.md): the lowest real question
# scored 0.412; the clearly off-shelf ones 0.35-0.37. Near misses (foraging rules finding the
# chicken-permit sheet) score like real questions, so no floor can catch them; the model's
# reading of the passages is the second gate. Set low on purpose, as a floor that turns a real
# question away costs more than one that lets a near miss through to that second gate.
FLOOR = float(os.environ.get("HESTIA_LIBRARY_FLOOR", "0.38"))
MED_NOTE = ("Medication rule: cite what these publications say about planning, supply and storage "
            "only. For a dose, a substitute, or stopping or skipping a medication, tell the user to "
            "ask their pharmacist or doctor; do not advise it yourself.")
EDIBLE_NOTE = ("Wild food rule: describe what these publications say, but never tell the user a wild "
               "plant, berry or mushroom is safe to eat on the strength of a written description; say "
               "the identification must be confirmed in person by someone who knows the species.")
_EDIBLE = re.compile(r"\b(?:edible|safe to eat|can (?:i|we|you) eat|poisonous|toxic|forag\w*|wild (?:plants?|"
                     r"foods?|greens|berr(?:y|ies)|mushrooms?)|mushrooms?)\b", re.I)
_MED = re.compile(r"\b(?:medic(?:ation|ine)s?|meds|insulin|prescriptions?|pharmac\w*|dos(?:e|es|age)|"
                  r"pills?|inhalers?|oxygen)\b", re.I)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "library",
        "description": ("Search the household's library of university extension and agricultural "
                        "experiment station publications: vegetables, fruit, pruning, soil and "
                        "fertility, compost, pests and plant diseases, invasive plants, bees and "
                        "wildlife, backyard chickens, ticks, mold, food dates and budgets, home "
                        "canning and preserving, maple sugaring, herbs and wild edible plants, water "
                        "treatment, wilderness survival (fire, shelter, navigation, first aid, "
                        "snakebite) and emergency preparedness. Use it for how-to, timing and "
                        "diagnosis questions "
                        "in those areas, then answer from the passages and name the publication and "
                        "page. It also lists what the house already has on hand among the things "
                        "the passages name, so the answer can start from the shelf. Pass the user's "
                        "question in plain words as the query."),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "the question, in plain words"}},
            "required": ["query"],
        },
    },
}


def cite(p: dict) -> str:
    year = f", {p['year']}" if p.get("year") else ""
    return f"{p['title']}{year}, p.{p['page']}"


ON_HAND_HEAD = ("On hand in this house, from the inventory, among the things these passages name. "
                "Build the answer around these where the passages support them; say where each was "
                "last noted (never that it IS there), and pass on any old-stock note:")
NONE_ON_HAND = ("Nothing in the house's inventory matches what these passages name. If the answer needs "
                "something, say what, and offer to put it on the shopping list.")


def _on_hand(query: str, hits: list[dict]) -> str:
    """The inventory join (INVENTORY.md, "The grounded answer"), done in code: what the passages
    name that the house already has. Nothing is added for a house with no inventory at all."""
    try:
        import inventory_store
        items = inventory_store.on_hand_for([query, *(p["text"] for p in hits)])
        if items:
            return "\n\n" + ON_HAND_HEAD + "\n" + "\n".join(items)
        return "\n\n" + NONE_ON_HAND if inventory_store.has_any() else ""
    except Exception:  # noqa: BLE001 - the passages still answer without the join
        return ""


def execute(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: give the question to look up."
    hits = library_store.search(query, K)
    if hits and hits[0].get("score") is not None and hits[0]["score"] < FLOOR:
        hits = []   # vector search ran and nothing is close: the shelf doesn't cover this
    if not hits:
        return ("Nothing in the household library matches that. Say so plainly; do not answer "
                "from a loosely related publication.")
    lines = [f"[{i}] {cite(p)}\n{p['text']}" for i, p in enumerate(hits, 1)]
    head = ("Library passages (cite as: title, page). Answer only from these; if they don't cover "
            "the question, say the library doesn't. If a passage is about a different plant, animal "
            "or situation than the one asked about, say so before drawing on it.")
    if hits[0].get("mode") == "keyword":
        head += " (Keyword search: the meaning search was unavailable, so check fit carefully.)"
    out = head + "\n\n" + "\n\n".join(lines)
    out += _on_hand(query, hits)
    if _MED.search(query) or _MED.search(hits[0]["text"]):
        out += "\n\n" + MED_NOTE
    if _EDIBLE.search(query):
        out += "\n\n" + EDIBLE_NOTE
    return out
