"""Background note-taker — "it gets smarter over time" (MEMORY-DESIGN.md §"How it gets
smarter over time").

After an exchange, a model reads the transcript and PROPOSES durable facts worth
remembering ("User prefers TV downloads in 1080p, not 4K"). Per Hestia's north star —
determinism over intelligence; the Eyes pattern of *propose, don't dispose* — proposals
land in a review inbox (memory/inbox/*.md), NOT straight into the live memory store. You
review and promote them with `review_notes.py`. Background extraction always requires
review; deployment environment variables cannot bypass this gate.

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


import backend
import config
import memory_store

OLLAMA = os.environ.get("HESTIA_NOTETAKER_OLLAMA") or os.environ.get("HESTIA_OLLAMA", "http://127.0.0.1:11434")
# Reuse the resident brain by default (hot, zero cold-start); override to offload.
MODEL = os.environ.get("HESTIA_NOTETAKER_MODEL") or os.environ.get("HESTIA_MODEL", "gemma4:12b")
ENABLED = os.environ.get("HESTIA_NOTETAKER", "1") not in ("0", "", "false", "False")
TIMEOUT = float(os.environ.get("HESTIA_NOTETAKER_TIMEOUT", "30"))
INBOX_DIR = config.INBOX_DIR

_VALID_TYPES = memory_store.TYPES
_MAX_PROPOSALS = 2          # most exchanges hold no durable fact; a good one holds one or two
_MIN_USER_CHARS = 12        # skip trivial "turn on the light"-class turns

EXTRACT_PROMPT = """You are the memory note-taker for a private home assistant. Read the conversation below and decide whether it revealed a durable fact about this household worth remembering for months: who lives here, their animals, home, garden, equipment, routines, and lasting tastes.

Most conversations reveal nothing new. Returning [] is the normal, correct answer.

Record ONLY:
- facts about the people, animals, home, garden, belongings and routines of this household;
- lasting tastes and preferences, including how they like to be spoken to.

Never record (these are the usual mistakes):
- anything about the assistant itself, its tools, prompts, logs, models or the software it runs on ("The user uses Home Assistant", "The user is tuning the assistant");
- wishes, requests or plans ("The user wants alerts when...", "The user intends to post..."): those are tasks for the assistant, not facts;
- what someone is in the middle of doing ("The user is currently working on...", "is transitioning to..."): it will be stale soon;
- live state (a light, today's weather, a download), questions, and anything the assistant did this turn;
- restatements of a fact in different words.

At most 2 facts. Return ONLY a JSON array, no prose. Each item:
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


# The prompt alone did not hold: on 2026-10-20 Gemma proposed 29 facts in a day and 24 were
# junk, mostly the assistant describing its own system and requests phrased as facts. These
# patterns are the deterministic backstop. Tuned on that day's labeled proposals (19 of 24
# junk dropped, all 5 keepers kept) and checked against the approved memories for false drops.
_ABOUT_SYSTEM = re.compile(
    r"\b(?:the assistant|assistant's|AI|LLM|language model|prompts?|backend|inference|"
    r"tool[- ]calling|Hestia|the system|'?goals'? system|(?:uses|utilizes|runs) (?:Home Assistant|HA)\b|logs?\b)", re.I)
# How the household wants to be spoken to is a real fact even though it mentions the assistant
# ("prefers that the assistant admits uncertainty"). Only that: "values technical depth in how
# the AI works" opens the same way and is still the system describing itself.
_PREFERENCE = re.compile(
    r"^(?:the )?user\s+(?:strongly\s+)?(?:prefers|values|prioritizes|likes|dislikes)\b"
    r"(?=.*\b(?:honest\w*|integrity|accura\w*|uncertain\w*|admits?|tone|brief|brevity|concise|plain|"
    r"direct|polish\w*|facade|pleasant|flatter\w*)\b)", re.I)
# Wishes, intentions and work in progress: tasks or soon-stale state, not durable facts.
# "wants a system / wants the assistant to" is a request; "wants movies in 1080p" is a standing
# preference and stays. "is interested in <topic>" stays; "is interested in using X" goes.
_WISH = re.compile(
    r"\b(?:(?:wants|would like) (?:a|an|the assistant|to|it to|you to|alerts?|notifications?|reminders?|reports?|updates)\b|wishes to|intends|plans to|hopes to|is interested in (?:using|setting up|building|adding)|"
    r"is planning|is trying|is working on|is developing|is tuning|is currently|is creating|is transitioning|"
    r"needs? to|needs curation|that need)\b", re.I)


def rejected(content: str) -> str | None:
    """Why a proposed fact is not a household fact ('about the system', 'a wish or task'),
    or None if it may go to review. Pure; the note-taker's deterministic backstop."""
    if _ABOUT_SYSTEM.search(content) and not _PREFERENCE.search(content):
        return "about the system"
    if _WISH.search(content):
        return "a wish or task"
    return None


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
        if key in seen or rejected(content):
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
    request = extract_request(transcript)
    return backend.complete(request["messages"], **request["options"])["content"]


def extract_request(transcript: str) -> dict:
    """The model step as data, so it can run now or wait in model_jobs for a phone."""
    return {"messages": [{"role": "user", "content": EXTRACT_PROMPT + transcript}],
            "options": {"model": MODEL, "temperature": 0.1, "num_ctx": 8192, "max_tokens": 512,
                        "timeout": TIMEOUT, "ollama": OLLAMA}}


def run(messages: list[dict], answer: str, extract_fn=None) -> list[str]:
    """Extract durable facts from one exchange and record them.

    Returns the proposal IDs written to the review inbox. `extract_fn`
    lets tests inject the model output; production uses the real Ollama call. Never raises —
    a note-taking failure must not affect the conversation that already completed."""
    extract_fn = extract_fn or _extract
    try:
        user_text = next((m["content"] for m in reversed(messages)
                          if m.get("role") == "user" and m.get("content")), "")
        if len(user_text.strip()) < _MIN_USER_CHARS:
            return []
        transcript = _transcript(messages, answer)
        if extract_fn is _extract and backend.deferred():
            import model_jobs
            model_jobs.enqueue("note", extract_request(transcript), {}, ttl_hours=24 * 7)
            return []
        return _record(extract_fn(transcript))
    except Exception as e:  # noqa: BLE001 — background best-effort; log and move on
        print(f"[note-taker] failed: {e}", flush=True)
        return []


def _record(raw: str) -> list[str]:
    """Novel proposals from the model's output, written to the review inbox. Never to memory."""
    return [_write_proposal(p) for p in parse_proposals(raw) if is_novel(p["content"])]


def finish(raw: str, context: dict) -> None:
    """model_jobs finisher: a phone has read a queued exchange; propose what it found."""
    _record(raw)
