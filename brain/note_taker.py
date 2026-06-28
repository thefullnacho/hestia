"""Background note-taker — "it gets smarter over time" (MEMORY-DESIGN.md §"How it gets
smarter over time").

After an exchange, a model reads the transcript and PROPOSES durable facts worth
remembering ("User prefers TV downloads in 1080p, not 4K"). Per Hestia's north star —
determinism over intelligence; the Eyes pattern of *propose, don't dispose* — proposals
land in a review inbox (memory/inbox/*.md), NOT straight into the live memory store. You
review and promote them with `review_notes.py`. Set HESTIA_NOTETAKER_AUTOWRITE=1 to skip
the queue and write durable memories directly once you trust it.

It runs out of band: the brain answers the user first, then this fires as a background
task, bounded by a timeout, never raising into the request. By default it reuses the
resident model (already hot on the 5080); point HESTIA_NOTETAKER_MODEL at a cheaper model
(e.g. a second Ollama on the free 4060 Ti) to take the load off the brain.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import httpx

import config
import memory_store

OLLAMA = os.environ.get("HESTIA_OLLAMA", "http://127.0.0.1:11434")
# Reuse the resident brain by default (hot, zero cold-start); override to offload.
MODEL = os.environ.get("HESTIA_NOTETAKER_MODEL") or os.environ.get("HESTIA_MODEL", "qwen3:14b")
ENABLED = os.environ.get("HESTIA_NOTETAKER", "1") not in ("0", "", "false", "False")
AUTOWRITE = os.environ.get("HESTIA_NOTETAKER_AUTOWRITE", "0") not in ("0", "", "false", "False")
TIMEOUT = float(os.environ.get("HESTIA_NOTETAKER_TIMEOUT", "30"))
INBOX_DIR = config.INBOX_DIR

_VALID_TYPES = memory_store.TYPES
_MAX_PROPOSALS = 5          # a single exchange rarely yields more than a couple real facts
_MIN_USER_CHARS = 12        # skip trivial "turn on the light"-class turns

EXTRACT_PROMPT = """You are the memory note-taker for a private home assistant. Read the conversation below and extract ONLY durable, user-specific facts worth remembering for months — stable preferences, household facts, routines, people, or work facts the user stated or clearly implied.

STRICT RULES:
- Extract facts ABOUT THE USER / their home / their world — never about the assistant, the conversation, or how a tool works.
- Do NOT record transient or live state (whether a light is on, today's weather, what is downloading right now, the current time). That is queried live, never remembered.
- Do NOT record questions, one-off requests, or things the assistant merely did this turn.
- Only include something if a reasonable person would still want it remembered next month.
- If nothing qualifies, return an empty array. Prefer recording nothing over noise.

Return ONLY a JSON array, no prose. Each item:
  {"content": "<one self-contained fact, written so it stands alone later>",
   "type": "<one of: person, household, preference, routine, work, reference, episodic>",
   "confidence": <0.0-1.0>}

Conversation:
"""


def _transcript(messages: list[dict], answer: str, max_turns: int = 6) -> str:
    """Render the recent user/assistant turns + the final answer into a plain transcript."""
    turns = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    lines = [f"{m['role'].upper()}: {m['content']}" for m in turns[-max_turns:]]
    if not lines or turns[-1].get("role") != "assistant" or turns[-1].get("content") != answer:
        lines.append(f"ASSISTANT: {answer}")
    return "\n".join(lines)


def parse_proposals(raw: str) -> list[dict]:
    """Pure: turn a model reply into a clean, de-duplicated proposal list.

    Tolerates code fences / stray prose around the JSON, validates each item, coerces an
    out-of-whitelist type to 'preference', clamps confidence, drops empties, and removes
    in-batch duplicates. Returns [] on anything unparseable — never raises."""
    if not raw:
        return []
    text = raw.strip()
    # peel a ```json ... ``` fence if present
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    # otherwise grab the outermost JSON value (array or object) from any surrounding prose
    if text[:1] not in "[{":
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            text = m.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    # format=json makes these models often return a single object, or a wrapper like
    # {"facts": [...]}, instead of the asked-for top-level array. Normalize all three.
    if isinstance(data, dict):
        if "content" in data:
            data = [data]
        else:
            data = next((v for v in data.values() if isinstance(v, list)), [])
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if len(content) < 3:
            continue
        key = content.lower()
        if key in seen:
            continue
        seen.add(key)
        mtype = str(item.get("type") or "preference").strip().lower()
        if mtype not in _VALID_TYPES:
            mtype = "preference"
        try:
            conf = float(item.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        conf = max(0.0, min(1.0, conf))
        out.append({"content": content, "type": mtype, "confidence": conf})
        if len(out) >= _MAX_PROPOSALS:
            break
    return out


def _slug(text: str, n: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return ("-".join(words[:n]) or "note")[:60]


def is_novel(content: str) -> bool:
    """True if this fact isn't already known (live memory) or already queued (inbox).

    Keyword-overlap dedup, matching memory_store.recall's v1 approach — strong overlap
    with an existing fact means we don't re-propose it. (Vector similarity is the planned
    upgrade; the markdown stays the source of truth, so swapping this in changes nothing
    else.)"""
    words = set(re.findall(r"[a-z0-9]+", content.lower()))
    if not words:
        return False

    def overlaps(other: str) -> bool:
        ow = set(re.findall(r"[a-z0-9]+", other.lower()))
        if not ow:
            return False
        # near-duplicate if most of the smaller fact's words are shared
        return len(words & ow) / min(len(words), len(ow)) >= 0.6

    for hit in memory_store.recall(content, k=5):
        if overlaps(hit["body"]):
            return False
    if INBOX_DIR.exists():
        for p in INBOX_DIR.glob("*.md"):
            body = memory_store._parse(p)["body"]
            if overlaps(body):
                return False
    return True


def _write_proposal(prop: dict) -> str:
    """Write one proposal to the inbox as reviewable markdown. Returns its id."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    rid = _slug(prop["content"])
    if (INBOX_DIR / f"{rid}.md").exists():
        i = 2
        while (INBOX_DIR / f"{rid}-{i}.md").exists():
            i += 1
        rid = f"{rid}-{i}"
    fm = "\n".join([
        "---",
        f"id: {rid}",
        "status: proposed",
        f"type: {prop['type']}",
        f"confidence: {prop['confidence']}",
        f"source: note-taker@{dt.date.today().isoformat()}",
        f"created: {dt.datetime.now().isoformat(timespec='seconds')}",
        "---",
    ])
    (INBOX_DIR / f"{rid}.md").write_text(f"{fm}\n{prop['content'].strip()}\n")
    return rid


def _extract(transcript: str) -> str:
    """Call the model for raw extraction output. We deliberately do NOT set format=json:
    on qwen3:14b that biases the model to emit a single object and drop the other facts in
    the turn, whereas free-form returns the full array. parse_proposals tolerates the prose
    or fences that occasionally come with free-form output."""
    body = {"model": MODEL,
            "messages": [{"role": "user", "content": EXTRACT_PROMPT + transcript}],
            "stream": False, "think": False,
            "options": {"temperature": 0.1}}
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "") or ""


def run(messages: list[dict], answer: str, extract_fn=None) -> list[str]:
    """Extract durable facts from one exchange and record them.

    Returns the ids written (to the inbox, or to live memory when AUTOWRITE). `extract_fn`
    lets tests inject the model output; production uses the real Ollama call. Never raises —
    a note-taking failure must not affect the conversation that already completed."""
    extract_fn = extract_fn or _extract
    try:
        user_text = next((m["content"] for m in reversed(messages)
                          if m.get("role") == "user" and m.get("content")), "")
        if len(user_text.strip()) < _MIN_USER_CHARS:
            return []
        proposals = parse_proposals(extract_fn(_transcript(messages, answer)))
        written: list[str] = []
        for p in proposals:
            if not is_novel(p["content"]):
                continue
            if AUTOWRITE:
                written.append(memory_store.write(
                    p["content"], type=p["type"], source="note-taker",
                    confidence=p["confidence"]))
            else:
                written.append(_write_proposal(p))
        return written
    except Exception as e:  # noqa: BLE001 — background best-effort; log and move on
        print(f"[note-taker] failed: {e}", flush=True)
        return []
