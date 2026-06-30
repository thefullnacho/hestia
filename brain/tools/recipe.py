"""`recipe` tool — the household's recipe collection (local markdown files).

Determinism over a web blob: the recipes the family actually cooks live as clean markdown
on disk (one file each), so "her" banana bread is byte-identical every time and works
offline. `lookup` returns the full recipe text into context, where the model answers
follow-ups from it (reading comprehension, not recall — a 14B will confidently hallucinate
a flour quantity otherwise). `save` persists a recipe the model has already cleaned into
structured form: the MODEL is the parser (it can see the recipe), this tool just writes the
file. A recipe that isn't in the collection falls through to the `search` tool (web), which
the recipe skill then offers to `save` for next time.

Recipes are private household data, so they live under DATA_DIR (gitignored), never the
public tree — same posture as photos.
"""
from __future__ import annotations

import datetime as dt
import re

import config

RECIPES_DIR = config.RECIPES_DIR

SCHEMA = {
    "type": "function",
    "function": {
        "name": "recipe",
        "description": (
            "The household's saved recipe collection (local files). "
            "action='lookup' finds a saved recipe by name and returns its full text — use it "
            "FIRST for any 'how do I make / cook / bake X' request, then answer the user's "
            "questions from the returned recipe (never state a quantity or temperature that "
            "isn't in it). action='list' names the saved recipes. action='save' stores a recipe "
            "for next time: pass the recipe already cleaned into structured markdown — a short "
            "Ingredients list and numbered Steps, with blog story/ads removed — as `content`. "
            "If lookup finds nothing, use the `search` tool to find the recipe on the web, then "
            "offer to save it."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["lookup", "save", "list"]},
                "name": {"type": "string", "description": "the dish name, e.g. 'banana bread' (for lookup/save)"},
                "content": {"type": "string", "description": "for save: the cleaned recipe body — an Ingredients list and numbered Steps in markdown"},
                "servings": {"type": "string", "description": "for save (optional): yield, e.g. '1 loaf', 'serves 4'"},
                "aliases": {"type": "string", "description": "for save (optional): other names for this dish, comma-separated"},
            },
            "required": ["action"],
        },
    },
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "recipe"


def _frontmatter(text: str) -> tuple[dict, str]:
    """Split a recipe file into (frontmatter dict, body). No yaml dep — same style as skill.py."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text[end + 4:].lstrip("\n")


def _files() -> list:
    if not RECIPES_DIR.is_dir():
        return []
    return sorted(RECIPES_DIR.glob("*.md"))


def _terms(path, meta: dict) -> str:
    """Searchable text for a recipe: filename + name + aliases, lowercased."""
    return " ".join([path.stem.replace("-", " "), meta.get("name", ""),
                     meta.get("aliases", "")]).lower()


def _title(path, meta: dict) -> str:
    return meta.get("name") or path.stem.replace("-", " ").title()


def _lookup(name: str) -> str:
    q = (name or "").strip().lower()
    if not q:
        return "Which recipe? Give me a dish name."
    tokens = [t for t in re.split(r"\s+", q) if t]
    best, best_score, best_len = None, 0, 0
    for p in _files():
        meta, _ = _frontmatter(p.read_text(encoding="utf-8"))
        score = sum(1 for t in tokens if t in _terms(p, meta))
        title_len = len(_title(p, meta))
        # Best score wins; tie-break toward the shorter title, so a 2-word match on
        # "banana bread" beats the same words landing inside a longer compound name.
        if score > best_score or (score == best_score and score and title_len < best_len):
            best, best_score, best_len = p, score, title_len
    if not best or best_score == 0:
        return (f"No saved recipe matches '{name}'. It's not in the collection — use the "
                f"`search` tool to find it on the web, then offer to save it.")
    meta, body = _frontmatter(best.read_text(encoding="utf-8"))
    serv = f" ({meta['servings']})" if meta.get("servings") else ""
    return f"Saved recipe: {_title(best, meta)}{serv}\n\n{body.rstrip()}"


def _save(name: str | None, content: str | None, servings: str | None = None,
          aliases: str | None = None) -> str:
    if not (name or "").strip():
        return "What's the recipe called? I need a name to save it."
    if not (content or "").strip():
        return "Nothing to save — pass the cleaned recipe (ingredients + numbered steps) as content."
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    path = RECIPES_DIR / f"{_slug(name)}.md"
    existed = path.exists()
    fm = [f"name: {name.strip()}"]
    if aliases and aliases.strip():
        fm.append(f"aliases: {aliases.strip()}")
    if servings and servings.strip():
        fm.append(f"servings: {servings.strip()}")
    fm.append(f"saved: {dt.date.today().isoformat()}")
    path.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + content.strip() + "\n", encoding="utf-8")
    return f"{'Updated' if existed else 'Saved'} '{name.strip()}' in the recipe collection."


def _list() -> str:
    titles = sorted(_title(p, _frontmatter(p.read_text(encoding="utf-8"))[0]) for p in _files())
    if not titles:
        return "No recipes saved yet."
    return "Saved recipes:\n" + "\n".join(f"- {t}" for t in titles)


def execute(action: str, name: str | None = None, content: str | None = None,
            servings: str | None = None, aliases: str | None = None) -> str:
    if action == "lookup":
        return _lookup(name or "")
    if action == "save":
        return _save(name, content, servings, aliases)
    if action == "list":
        return _list()
    return f"Error: unknown recipe action '{action}' (use 'lookup', 'save', or 'list')."
