"""Skill router — deterministic, pre-loop domain-knowledge injection.

Skill procedures are selected deterministically before the agent loop. The caller
unions up to three matching procedures and their tools for mixed requests; the model
receives relevant domain knowledge without an extra routing inference. The single
match() helper remains available for callers that need the highest-scoring skill.

A skill lives in `brain/skills/<name>/` with a `SKILL.md` whose frontmatter carries
`name`, `description`, and `triggers` (comma-separated keywords), plus `references/*.md`.
We inject the SKILL.md body + `knowledge.md` + `decide.md`; `learn.md` is offline-only.
"""
from __future__ import annotations

import re

import config

SKILLS_DIR = config.SKILLS_DIR
_INJECT_REFS = ("knowledge.md", "decide.md")


def _frontmatter(text: str) -> dict:
    """Pull name/description/triggers out of a `--- ... ---` header (no yaml dep)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    meta: dict = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta


def _skills() -> list[dict]:
    """Discover skills on disk: [{name, description, triggers, dir}], sorted by name."""
    out = []
    if not SKILLS_DIR.is_dir():
        return out
    for d in sorted(SKILLS_DIR.iterdir()):
        sm = d / "SKILL.md"
        if not sm.is_file():
            continue
        meta = _frontmatter(sm.read_text(encoding="utf-8"))
        triggers = [t.strip().lower() for t in meta.get("triggers", "").split(",") if t.strip()]
        # Optional `tools:` allow-list — when present, the brain offers the model ONLY
        # these tools for a request this skill owns (so a 14B can't misfire into the wrong tool).
        allow = [t.strip().lower() for t in meta.get("tools", "").split(",") if t.strip()]
        out.append({"name": meta.get("name", d.name),
                    "description": meta.get("description", ""),
                    "triggers": triggers, "tools": allow, "dir": d})
    return out


def get(name: str) -> dict | None:
    """The skill dict (name / triggers / tools / dir) for a given skill name, or None."""
    return next((s for s in _skills() if s["name"] == name), None)


def _score(text: str, triggers: list[str]) -> int:
    """How many distinct triggers appear in text as whole words/phrases."""
    t = text.lower()
    return sum(1 for kw in triggers if re.search(rf"\b{re.escape(kw)}\b", t))


def matches(user_text: str, limit: int = 3) -> list[dict]:
    """Rank matching procedures; keep a bounded union for mixed requests."""
    scored = [(s, _score(user_text, s['triggers'])) for s in _skills()]
    return [s for s, score in sorted(scored, key=lambda pair: -pair[1]) if score > 0][:limit]


def match(user_text: str) -> dict | None:
    return next(iter(matches(user_text, 1)), None)


def _body(text: str) -> str:
    """SKILL.md with its frontmatter stripped."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def active_block(user_text: str, selected: dict | None = None) -> str:
    """The matched skill's knowledge to inline into the system prompt, or '' if none."""
    s = selected or match(user_text)
    if s is None:
        return ""
    d = s["dir"]
    parts = [f"This skill was selected for this request — follow its knowledge and "
             f"procedure, then answer.\n\n{_body((d / 'SKILL.md').read_text(encoding='utf-8'))}"]
    for ref in _INJECT_REFS:
        p = d / "references" / ref
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8").rstrip())
    return f"--- ACTIVE SKILL: {s['name']} ---\n" + "\n\n".join(parts)
