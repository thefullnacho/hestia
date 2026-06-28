"""Hestia's memory store — inspectable markdown records, per MEMORY-DESIGN.md.

Each memory is one markdown file: YAML frontmatter (the structured part) + free
text (the human part). Markdown is the source of truth; it lives in a git repo so
every fact the brain learns is a diff you can read, audit, or revert.

v1 recall is keyword/overlap scoring over the records — simple and dependency-free.
The design's vector recall is a later upgrade: the markdown stays the source of
truth, the index is derived and rebuildable, so adding embeddings later changes
nothing here. Household *state* (is a light on?) is deliberately NOT stored — that's
queried live from Home Assistant via the `home` tool.
"""
from __future__ import annotations

import re
import datetime as dt
from pathlib import Path

import yaml

import config

MEMORY_DIR = config.MEMORY_DIR
TYPES = {"person", "household", "preference", "routine", "work", "reference", "episodic"}


def _today() -> str:
    return dt.date.today().isoformat()


def _slug(text: str, n: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    base = "-".join(words[:n]) or "note"
    return base[:60]


def _parse(path: Path) -> dict:
    raw = path.read_text()
    meta, body = {}, raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    return {"id": meta.get("id", path.stem), "meta": meta, "body": body.strip(), "path": path}


def _all() -> list[dict]:
    if not MEMORY_DIR.exists():
        return []
    return [_parse(p) for p in sorted(MEMORY_DIR.glob("*.md")) if p.name != "INDEX.md"]


def _reindex() -> None:
    lines = ["# Hestia memory index", "", "*Auto-generated. One line per record.*", ""]
    for r in sorted(_all(), key=lambda x: x["id"]):
        m = r["meta"]
        first = r["body"].splitlines()[0] if r["body"] else ""
        lines.append(f"- `{r['id']}` ({m.get('type', '?')}, conf {m.get('confidence', '?')}) — {first}")
    (MEMORY_DIR / "INDEX.md").write_text("\n".join(lines) + "\n")


def write(content: str, type: str = "preference", source: str = "agent",
          confidence: float = 0.8, links: list[str] | None = None,
          pinned: bool = False) -> str:
    """Write a durable memory record. Returns the record id."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    mtype = type if type in TYPES else "preference"
    rid = _slug(content)
    # de-dupe ids
    if (MEMORY_DIR / f"{rid}.md").exists():
        i = 2
        while (MEMORY_DIR / f"{rid}-{i}.md").exists():
            i += 1
        rid = f"{rid}-{i}"
    meta = {
        "id": rid, "type": mtype, "confidence": confidence,
        "source": f"{source}@{_today()}", "last_seen": _today(),
        "links": links or [], "pinned": pinned,
    }
    fm = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False).strip()
    (MEMORY_DIR / f"{rid}.md").write_text(f"---\n{fm}\n---\n{content.strip()}\n")
    _reindex()
    return rid


def recall(query: str, k: int = 5) -> list[dict]:
    """Return up to k records most relevant to the query (keyword overlap v1)."""
    q = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not q:
        return []
    scored = []
    for r in _all():
        hay = " ".join([r["id"], r["body"], " ".join(map(str, r["meta"].get("links", []))),
                        str(r["meta"].get("type", ""))]).lower()
        toks = set(re.findall(r"[a-z0-9]+", hay))
        score = len(q & toks)
        # pinned + confidence as gentle tiebreakers
        score += 0.5 if r["meta"].get("pinned") else 0
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:k]]


def context_block(query: str, k: int = 5) -> str:
    """Relevant memories formatted for injection into the system prompt."""
    hits = recall(query, k)
    if not hits:
        return ""
    out = ["Relevant things you remember (from your memory store):"]
    for r in hits:
        out.append(f"- ({r['meta'].get('type', '?')}) {r['body']}")
    return "\n".join(out)
