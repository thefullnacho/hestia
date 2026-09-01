"""Hestia — the brain (Phase 4: agent loop + tools + memory).

Still exposes one OpenAI-compatible endpoint (POST /v1/chat/completions) so every
window — terminal, phone, kitchen mic — speaks one dialect. But the brain is no
longer a passthrough proxy: it owns the loop. Per request it recalls relevant
memory, then runs a tool-calling loop against Ollama (qwen3:14b) — calling `home`
to control the house, `memory` to remember/recall — until the model produces a final
answer, which it returns to the client as a normal chat completion.

Internally it speaks to Ollama's native /api/chat (structured tool_calls). Tool
execution is sync, run off the event loop. v1 returns complete (non-streamed)
responses; clients that asked for stream=true get the final answer in one SSE chunk.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize

# config puts brain/ on sys.path and owns every path; load secrets before importing the
# tools that read tokens/URLs at import time.
import config  # noqa: E402

config.load_secrets()

import datetime as _dt  # noqa: E402

import memory_store  # noqa: E402
import nfc  # noqa: E402
import note_taker  # noqa: E402
import records_store  # noqa: E402
import review_notes  # noqa: E402
import tools  # noqa: E402
from prompt import SYSTEM_PROMPT  # noqa: E402

OLLAMA = os.environ.get("HESTIA_OLLAMA", "http://127.0.0.1:11434")
MODEL = os.environ.get("HESTIA_MODEL", "qwen3:14b")
# Explicit, not left to each model's undocumented Ollama default — some GGUFs default far
# below what the real system prompt + tool surface needs (~5.7k tokens worst case) and
# silently 400 instead of truncating. See brain/eval_keymatch.py's num_ctx fix.
NUM_CTX = int(os.environ.get("HESTIA_NUM_CTX", "32768"))
MAX_STEPS = int(os.environ.get("HESTIA_MAX_STEPS", "6"))
# Wall-clock guards so a slow/hung backend can't hang a whole request (2026-06-11: a hung
# SearXNG made one turn run ~5 min). TURN_BUDGET bounds the entire request; TOOL_BUDGET caps
# any single tool call. Blocking tool work cannot be safely killed in Python, so the bounded
# pool below also prevents timed-out workers from multiplying until they starve the service.
TURN_BUDGET = float(os.environ.get("HESTIA_TURN_BUDGET", "45"))
TOOL_BUDGET = float(os.environ.get("HESTIA_TOOL_BUDGET", "20"))
TOOL_WORKERS = max(1, int(os.environ.get("HESTIA_TOOL_WORKERS", "8")))
# qwen3 thinking mode: off by default (fast, no eval gain). HESTIA_THINK=1 to enable.
THINK = os.environ.get("HESTIA_THINK", "0") not in ("0", "", "false", "False")

# Voice services (the same Wyoming STT/TTS the HA Assist pipeline uses). The chat client's mic
# posts audio to the brain, which proxies to these — one hop for the phone, same as the text
# path, and the GPU services stay tailnet-only. host:port pairs, overridable via env.
def _hostport(env: str, default: str) -> tuple[str, int]:
    h, _, p = os.environ.get(env, default).rpartition(":")
    return h, int(p)


WHISPER_ADDR = _hostport("HESTIA_WHISPER", "127.0.0.1:10300")  # wyoming-faster-whisper (STT)
PIPER_ADDR = _hostport("HESTIA_PIPER", "127.0.0.1:10200")      # wyoming-piper (TTS)
STT_RATE = 16000  # faster-whisper wants 16 kHz mono s16le; ffmpeg resamples whatever the browser sends

app = FastAPI(title="Hestia", version="0.4-phase4")
client = httpx.AsyncClient(base_url=OLLAMA, timeout=httpx.Timeout(300.0, connect=10.0))
# Keep potentially blocking sync tools out of asyncio's shared default executor. A slot is
# released only when the underlying function actually ends, not when its caller times out.
_tool_executor = ThreadPoolExecutor(max_workers=TOOL_WORKERS, thread_name_prefix="hestia-tool")
_tool_slots = asyncio.BoundedSemaphore(TOOL_WORKERS)

# Photo intake (iOS Shortcuts / Telegram): images land on disk under HESTIA_PHOTO_DIR and a
# `photo` event is logged against the named entity (see records_store.attach_photo). Token auth.
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
PHOTO_DIR = config.PHOTO_DIR

# NFC capture (see nfc.py): a tag-triggered harvest/service log that never touches the model —
# a scanned tag can't afford the agent's silent "logged it" failure (2026-09-01 incident).
NFC_TOKEN = os.environ.get("NFC_TOKEN", "")
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif"}
_MAX_PHOTO_BYTES = 25 * 1024 * 1024  # 25 MB — a phone photo is a few MB; reject the absurd


_LIGHT_CONTEXT = ("light", "lights", "lamp", "lamps", "bulb", "bulbs", "lighting",
                  "dim", "brighten", "brightness", "light strip", "lamppost")


def _context_plan(user_text: str) -> dict:
    """Classify the dynamic context a turn needs using only local data.

    This runs in a worker thread because skill discovery and garden lookup read disk/SQLite.
    It lets ordinary conversation skip Home Assistant instead of fetching every light and soil
    sensor before every model call.
    """
    matched = tools.skill.match(user_text)
    garden_focus = records_store.garden_lookup(user_text)
    garden_topic = bool(garden_focus) or bool(matched and matched.get("name") == "garden_bed")
    text = user_text.lower()
    return {"matched": matched, "garden_focus": garden_focus, "garden_topic": garden_topic,
            "lights": bool(matched and matched.get("name") == "home_control")
            or any(re.search(rf"\b{re.escape(term)}\b", text) for term in _LIGHT_CONTEXT),
            # The garden skill's procedure relies on live readings, including broad bed questions.
            # Keep that behavior, but do not load soil for unrelated turns.
            "soil": garden_topic}


def _system_prompt(user_text: str, plan: dict, light_catalog: str = "", soil: str = "") -> str:
    """Assemble the prompt from already-selected context.

    Callers run this off the event loop after the small async HA fetch. Keeping prompt wording
    here preserves grounding behavior while making I/O ownership explicit.
    """
    now = _dt.datetime.now().strftime("%A %B %d %Y, %H:%M")
    parts = [SYSTEM_PROMPT, "", f"Current date/time: {now}."]
    if light_catalog:
        parts += ["", "--- LIGHT CATALOG ---", light_catalog]
    if soil:
        parts += ["", "--- GARDEN SOIL MOISTURE (live readings — the COMPLETE sensor list) ---",
                  "These are ALL the soil-moisture sensors and their current % readings. To answer "
                  "ANY question about garden/bed/sensor moisture — whether broad ('what do the "
                  "moisture sensors say', 'how's the garden') or about one bed ('is the carrot bed "
                  "dry') — read the values straight from this list and report them. Do NOT call a "
                  "tool for this, and never name a bed or sensor that is not written here. For a "
                  "broad request, give every reading.",
                  soil]
    matched = plan["matched"]
    skill_block = tools.active_skill(user_text)
    if skill_block:
        parts += ["", skill_block]
    # The almanac pages are nightly-regenerated files, not static skill knowledge, so the
    # brain injects them live when the almanac skill owns the request (same move as the
    # GARDEN blocks: put the real data in front of the model instead of hoping it fetches).
    if matched and matched.get("name") == "almanac":
        pages = _almanac_pages()
        if pages:
            parts += ["", "--- ALMANAC (authoritative season record) ---",
                      "Answer season questions — frost dates, degree-days, the garden "
                      "timeline, wildlife, year-over-year comparisons — strictly from the "
                      "page(s) below. Everything historical is already written here; do NOT "
                      "call a tool for it. Never invent a date or event that is not on a page.",
                      pages]
    # Garden topic = the watering skill triggered OR the user named a real bed / zone /
    # plant that exists in records (data-driven, so we don't have to enumerate every plant
    # as a keyword). Places are kept out of roster() to avoid bloating every prompt.
    garden_focus = plan["garden_focus"]
    garden_topic = plan["garden_topic"]
    if garden_topic:
        garden = records_store.garden_overview()
        if garden:
            parts += ["", "--- GARDEN (authoritative — the COMPLETE planting list) ---",
                      "Answer every question about what is planted, or about any bed / zone / "
                      "area, strictly and only from the list below. Use these exact plant names "
                      "and counts. Never add, invent, generalize, or guess a plant, bed, or area "
                      "that is not written here. If something isn't in this list, say it's not in "
                      "the records rather than making it up. (This grounding is for QUESTIONS. If "
                      "the user instead reports something that happened in the garden — planted, "
                      "thinned, transplanted, harvested, lost, treated a bed — record it with the "
                      "records tool against the named bed, then confirm.)",
                      garden]
    roster = records_store.roster()
    if roster:
        parts += ["", "--- WHO & WHAT ---", roster]
    mem = memory_store.context_block(user_text)
    if mem:
        parts += ["", "--- MEMORY ---", mem]
    # Focused, exact garden records for the entities the user named — injected LAST so
    # it's the most recent context the model sees, which it grounds on far better than a
    # block buried earlier. Answer the specific question from this; no tool call needed.
    if garden_focus:
        parts += ["", "--- GARDEN RECORDS FOR THIS QUESTION "
                  "(answer using these exact entries; do NOT use search for this) ---",
                  garden_focus]
    return "\n".join(parts)


async def _build_system_prompt(user_text: str) -> str:
    """Collect only context relevant to this turn, without blocking the request event loop."""
    plan = await asyncio.to_thread(_context_plan, user_text)
    light_catalog, soil = await tools.home.context_catalogs(lights=plan["lights"], soil=plan["soil"])
    return await asyncio.to_thread(_system_prompt, user_text, plan, light_catalog, soil)


def _almanac_pages() -> str:
    """This year's almanac page, preceded by last year's when it exists (so 'compared to
    last year' has both in view). Current year goes last — recency grounds best."""
    year = _dt.date.today().year
    pages = []
    for y in (year - 1, year):
        p = config.ALMANAC_DIR / f"{y}.md"
        if p.is_file():
            pages.append(p.read_text(encoding="utf-8").rstrip())
    return "\n\n".join(pages)


async def _ollama_chat(messages: list[dict], schemas: list | None = None) -> dict:
    # think=False keeps qwen3 in fast mode (thinking on cost ~4s/turn for no eval gain;
    # see brain/eval_models.py — qwen3:14b no-think scored 100%/100% English at 1.5s).
    body = {"model": MODEL, "messages": messages,
            "tools": tools.SCHEMAS if schemas is None else schemas,
            "stream": False, "think": THINK,
            "options": {"temperature": 0.3, "num_ctx": NUM_CTX}}
    r = await client.post("/api/chat", json=body)
    r.raise_for_status()
    return r.json()["message"]


# A soil-STATE readout ("what's the moisture", "are the beds dry", "soil readings") is
# answered straight from the injected SOIL block — no tool call is ever correct. We offer
# ZERO tools for these so the 14B can't misfire into `weather` (rain forecast), which it did
# intermittently on "what's the moisture of the garden beds". A watering DECISION ("should I
# water the carrots") keeps the full garden toolset, because that genuinely needs the forecast.
_SOIL_STATE = ("moisture", "moist", "soil", "dry", "wet", "damp", "parched", "soaked", "dryness")
_WATER_DECISION = ("water", "irrigat")


def _is_soil_readout(user_text: str) -> bool:
    t = user_text.lower()
    return any(w in t for w in _SOIL_STATE) and not any(w in t for w in _WATER_DECISION)


def _request_schemas(user_text: str) -> list:
    """Tools offered for this request. If the matched skill declares a `tools:` allow-list,
    offer ONLY those (so a small model can't misfire into search on a garden question);
    otherwise offer everything. Garden detection is data-driven: naming a real bed/zone/plant
    scopes to the garden_bed tools even when no keyword trigger fired (e.g. 'fig trees')."""
    matched = tools.skill.match(user_text)
    garden = bool(matched and matched.get("name") == "garden_bed") or bool(records_store.garden_lookup(user_text))
    # Pure soil-state readout + the live block is present → offer nothing; force a read from it.
    if garden and _is_soil_readout(user_text) and tools.soil_catalog():
        return []
    allow = (matched or {}).get("tools")
    if not allow and records_store.garden_lookup(user_text):
        allow = (tools.skill.get("garden_bed") or {}).get("tools")
    if not allow:
        return tools.SCHEMAS
    return [s for s in tools.SCHEMAS if s["function"]["name"] in allow]


def _log(msg: str) -> None:
    """One-line agent trace to stdout -> journald (journalctl --user -u hestia-brain)."""
    print(f"[agent] {msg}", flush=True)


def _short(args: dict) -> str:
    """Compact args for a log line — truncate values so we don't dump payloads/secrets."""
    out = {k: (s if len(s := str(v)) <= 40 else s[:37] + "...") for k, v in (args or {}).items()}
    return str(out)


_TOO_SLOW = "Sorry — that took too long to pull together (a backend was slow). Try again in a moment?"
_BACKEND_DOWN = "Sorry, my model backend is having trouble right now. Try again in a moment?"


async def _run_tool(name: str, args: dict, budget: float) -> tuple[str, str]:
    """Run one synchronous tool within the shared bounded worker pool.

    ``wait_for(asyncio.to_thread(...))`` returns on timeout but leaves the thread running,
    allowing repeated slow calls to consume the global default executor. Here a slot stays held
    until the real worker exits; later calls wait only within their own budget and fail cleanly
    when capacity is exhausted. A hung backend can therefore degrade tool replies, but cannot
    turn into unbounded thread growth or block unrelated FastAPI work.
    """
    started = time.monotonic()
    try:
        await asyncio.wait_for(_tool_slots.acquire(), timeout=budget)
    except asyncio.TimeoutError:
        return (f"Error: {name} could not start before the tool-worker budget expired "
                "(other backends are still busy).", "capacity")

    try:
        work = asyncio.get_running_loop().run_in_executor(_tool_executor, tools.dispatch, name, args)
    except Exception:
        _tool_slots.release()
        raise
    # The callback runs on this event loop. Shielding prevents a caller timeout from cancelling
    # the wrapper Future; the real work remains observable and owns its slot until it completes.
    work.add_done_callback(lambda _: _tool_slots.release())
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        return f"Error: {name} timed out before it could run.", "timeout"
    try:
        return str(await asyncio.wait_for(asyncio.shield(work), timeout=remaining)), "ok"
    except asyncio.TimeoutError:
        return f"Error: {name} timed out after {budget:.0f}s (backend slow/unreachable).", "timeout"


async def run_agent(messages: list[dict]) -> str:
    """Recall memory, then loop tool-calls against Ollama until a final answer.

    Hard-bounded so a hung backend can't hang the request (2026-06-11: a stuck SearXNG made one
    turn run ~5 min). The whole request must finish within TURN_BUDGET; any single tool call is
    capped at TOOL_BUDGET. Timed-out workers remain contained in a fixed-size pool until they
    finish, so a slow backend cannot exhaust asyncio's shared executor. Every step is traced to
    the journal so a misfire is one `journalctl` away.
    """
    user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    convo = [m for m in messages if m.get("role") != "system"]
    convo = [{"role": "system", "content": await _build_system_prompt(user_text)}, *convo]
    schemas = _request_schemas(user_text)

    t0 = time.monotonic()
    deadline = t0 + TURN_BUDGET
    seen: dict[str, str] = {}     # (tool|args) -> result, to break repeat-call loops
    last_result = ""              # most recent real tool result, for a graceful fallback
    dup_nudges = 0
    _log(f"start {user_text[:80]!r} tools={[s['function']['name'] for s in schemas]}")

    for step in range(1, MAX_STEPS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log(f"TURN BUDGET {TURN_BUDGET}s exhausted before step {step}")
            return _TOO_SLOW
        try:
            msg = await asyncio.wait_for(_ollama_chat(convo, schemas), timeout=remaining)
        except asyncio.TimeoutError:
            _log(f"model call exceeded remaining {remaining:.1f}s at step {step}")
            return _TOO_SLOW
        except Exception as e:  # noqa: BLE001 — an Ollama restart must not 500 the client
            _log(f"model call failed at step {step}: {type(e).__name__}: {e}")
            return _BACKEND_DOWN
        calls = msg.get("tool_calls") or []
        if not calls:
            _log(f"answered in {step} step(s), {time.monotonic()-t0:.1f}s")
            return msg.get("content", "") or ""
        convo.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
        for c in calls:
            fn = c.get("function", {})
            name = fn.get("name", "")
            raw = fn.get("arguments", {})
            args = raw or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:  # noqa: BLE001 — non-JSON args; refuse rather than run silent defaults
                    _log(f"step {step} tool={name!r} MALFORMED args {raw!r} — refused")
                    convo.append({"role": "tool", "tool_name": name,
                                  "content": f"Error: arguments for {name} were not valid JSON; nothing was run."})
                    continue
            sig = f"{name}|{json.dumps(args, sort_keys=True, default=str)}"
            if sig in seen:
                # The model is repeating a call it already made this turn — a small-model loop
                # that otherwise burns every step and dead-ends (e.g. media status 6x). Hand back
                # the prior result with a nudge to answer, rather than re-running it.
                dup_nudges += 1
                _log(f"step {step} tool={name} args={_short(args)} DUPLICATE x{dup_nudges} — nudging")
                convo.append({"role": "tool", "tool_name": name, "content":
                              seen[sig] + "\n\n(You already called this and have the result above. "
                              "Do not call it again — answer the user now from this.)"})
                continue
            budget = min(TOOL_BUDGET, max(0.1, deadline - time.monotonic()))
            ts = time.monotonic()
            try:
                result, outcome = await _run_tool(name, args, budget)
                if outcome == "ok":
                    _log(f"step {step} tool={name} args={_short(args)} ok {time.monotonic()-ts:.1f}s")
                elif outcome == "capacity":
                    _log(f"step {step} tool={name} args={_short(args)} CAPACITY {budget:.0f}s")
                else:
                    _log(f"step {step} tool={name} args={_short(args)} TIMEOUT {budget:.0f}s")
            except Exception as e:  # noqa: BLE001 — executor failures still become tool results
                result = f"Error: {name} failed before it could run: {e}"
                _log(f"step {step} tool={name} args={_short(args)} EXECUTOR ERROR {type(e).__name__}")
            seen[sig] = str(result)
            last_result = str(result)
            convo.append({"role": "tool", "tool_name": name, "content": str(result)})
        # The model ignored the nudge and is still repeating itself — stop looping and answer from
        # the data we already have rather than dead-ending at MAX_STEPS with an apology.
        if dup_nudges >= 2 and last_result:
            _log(f"breaking repeat-call loop after {dup_nudges} dups in {time.monotonic()-t0:.1f}s")
            return last_result
    _log(f"hit MAX_STEPS={MAX_STEPS} in {time.monotonic()-t0:.1f}s, no final answer")
    return "I wasn't able to finish that in a reasonable number of steps — can you narrow it down?"


def _completion(content: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion",
        "created": int(time.time()), "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/")
async def root() -> dict:
    return {"service": "Hestia", "phase": 4, "role": "agent brain (tools + memory)",
            "model": MODEL, "tools": [s["function"]["name"] for s in tools.SCHEMAS]}


@app.get("/app")
async def chat_client():
    """Serve the thin chat PWA (clients/chat.html) same-origin, so the page's fetch hits
    /v1 with no CORS and the bookmark is installable to a phone home screen. Read per
    request, so editing the HTML doesn't need a brain restart. This is the text path that
    routes AROUND HA's Assist box (one hop to the brain; history kept client-side)."""
    try:
        return HTMLResponse((config.CLIENTS_DIR / "chat.html").read_text())
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "chat client not installed"})


@app.get("/icon.png")
async def chat_icon():
    """The home-screen / apple-touch icon for the chat PWA. Served from disk, so swapping
    the artwork is just replacing clients/icon.png (no restart needed for the file itself)."""
    p = config.CLIENTS_DIR / "icon.png"
    if p.is_file():
        return FileResponse(p, media_type="image/png")
    return JSONResponse(status_code=404, content={"error": "icon not installed"})


@app.get("/status")
async def status_snapshot():
    """Whole-stack health as JSON — the data behind the chat client's Status panel. Serves the
    SAME tools.status.snapshot() the `status` brain tool formats for speech, so the dashboard
    and the spoken answer can never disagree. Run off the event loop (the probes block)."""
    snap = await asyncio.to_thread(tools.status.snapshot)
    return JSONResponse(snap)


@app.get("/memory/inbox")
async def memory_inbox(limit: int = 0):
    """The note-taker's pending proposals as JSON — what the brain wants to learn but hasn't
    been allowed to yet. Read-only ON PURPOSE: promoting a fact into live memory stays a
    human act at the CLI (review_notes.py), so a dashboard tile can surface the queue without
    becoming a way to nod it through. Serves review_notes.proposals(), the same list the CLI
    prints. Newest first (a dashboard shows the head, the CLI reviews the tail); ?limit=N caps
    the rows returned while `count` stays the true depth of the queue."""
    today = _dt.date.today()

    def created_at(meta: dict) -> _dt.datetime | None:
        """The proposal's timestamp. YAML resolves an unquoted `created: 2026-06-13T20:19:15`
        to a real datetime, not a string, so accept both rather than assuming either."""
        v = meta.get("created")
        if isinstance(v, _dt.datetime):
            return v
        if isinstance(v, _dt.date):
            return _dt.datetime.combine(v, _dt.time())
        try:
            return _dt.datetime.fromisoformat(str(v))
        except (TypeError, ValueError):
            return None

    props = await asyncio.to_thread(review_notes.proposals)
    # Undated proposals sort last rather than blowing up the comparison on mixed types.
    props.sort(key=lambda r: created_at(r["meta"]) or _dt.datetime.min, reverse=True)
    total = len(props)
    if limit > 0:
        props = props[:limit]

    rows = []
    for r in props:
        m = r["meta"]
        ts = created_at(m)
        rows.append({
            "id": r["id"], "body": r["body"],
            "type": str(m.get("type", "unknown")),
            "confidence": m.get("confidence"),
            "source": str(m.get("source", "")),
            # Serialised by hand: `created` may arrive as a datetime, and age_days is
            # precomputed because Glance's template time helpers want an offset this naive
            # local stamp doesn't carry — a plain int is one less thing for a tile to get wrong.
            "created": ts.isoformat(timespec="seconds") if ts else "",
            "age_days": (today - ts.date()).days if ts else None,
        })
    return JSONResponse({"count": total, "dir": str(review_notes.INBOX_DIR), "proposals": rows})


@app.get("/health")
async def health():
    try:
        r = await client.get("/api/tags")
        names = [m["name"] for m in r.json().get("models", [])]
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "down", "ollama": "unreachable", "error": str(e)})
    base = MODEL.split(":")[0]
    return {"status": "ok" if any(n.split(":")[0] == base for n in names) else "degraded",
            "ollama": "up", "model": MODEL,
            "tools": [s["function"]["name"] for s in tools.SCHEMAS],
            "memory_records": len(list(memory_store.MEMORY_DIR.glob("*.md"))) if memory_store.MEMORY_DIR.exists() else 0}


# Answers that are non-substantive (errors / give-ups) — never worth note-taking on.
_NO_LEARN = {_TOO_SLOW, _BACKEND_DOWN,
             "I wasn't able to finish that in a reasonable number of steps — can you narrow it down?"}


def _note_task(messages: list[dict], content: str) -> BackgroundTask | None:
    """A fire-after-response note-taking task, or None when there's nothing to learn from.
    Starlette runs sync background callables in a threadpool, so note_taker's blocking model
    call won't touch the event loop, and it runs only once the answer is already on the wire."""
    if not note_taker.ENABLED or not content or content in _NO_LEARN:
        return None
    return BackgroundTask(note_taker.run, messages, content)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    content = await run_agent(messages)
    comp = _completion(content)
    note = _note_task(messages, content)

    if body.get("stream"):
        async def one_shot():
            chunk = {"id": comp["id"], "object": "chat.completion.chunk", "created": comp["created"],
                     "model": MODEL, "choices": [{"index": 0, "delta": {"role": "assistant", "content": content},
                                                   "finish_reason": "stop"}]}
            yield f"data: {json.dumps(chunk)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        return StreamingResponse(one_shot(), media_type="text/event-stream", background=note)
    return JSONResponse(comp, background=note)


@app.post("/ingest/photo")
async def ingest_photo(request: Request):
    """Receive one photo (from an iOS Shortcut / Telegram bridge) and file it against an entity.
    One generic endpoint; the Shortcut bakes in `domain` (pet/garden/wildlife/asset) and `token`,
    and supplies `subject` (the entity name). Saves the image under PHOTO_DIR/<domain>/<subject>/
    and logs a `photo` event via records (resolves to the existing pup/bed or mints it).
    Parses the form manually so a misconfigured Shortcut gets a clear 'what's missing' reply
    (listing the fields it actually received) instead of FastAPI's generic 422."""
    form = await request.form()
    got = sorted(k for k in form.keys())
    # Token may arrive as a header (cleanest in a Shortcut — a dedicated Headers entry, separate
    # from the form fields) OR as a form field. Accept X-Ingest-Token, Authorization: Bearer, or
    # a `token` form field — whichever the client finds easiest.
    auth = request.headers.get("authorization", "")
    token = (request.headers.get("x-ingest-token")
             or (auth[7:] if auth.lower().startswith("bearer ") else "")
             or str(form.get("token") or "")).strip()
    if not INGEST_TOKEN or token != INGEST_TOKEN:
        return JSONResponse(status_code=401, content={
            "error": "missing or bad token",
            "hint": "send the token as a header named 'X-Ingest-Token' (value = the token), "
                    "or as a form field named 'token'",
            "received_fields": got})
    # One post can name several subjects ("Carrots Round Bed, Beets Round Bed") so a single
    # photo files against each real bed instead of minting one junk compound entity.
    subjects = [s.strip() for s in re.split(r"[;,\n]+", str(form.get("subject") or "")) if s.strip()]
    file = form.get("file")
    missing = []
    # A File field parses to an UploadFile-like object; a Text field parses to str. Check by
    # "not a string" rather than isinstance(UploadFile) — request.form() yields Starlette's
    # base UploadFile, which isn't an instance of FastAPI's subclass.
    if file is None or isinstance(file, str):
        missing.append("file  (must be a File-type form field named 'file' = the photo)")
    if not subjects:
        missing.append("subject  (the entity name, e.g. the pup's name; comma-separate several)")
    if missing:
        return JSONResponse(status_code=400, content={
            "error": "missing required field(s)", "missing": missing, "received_fields": got})
    domain = (str(form.get("domain") or "pet")).strip().lower()
    caption = str(form.get("caption") or "")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _PHOTO_EXTS:
        ext = ".jpg"
    data = await file.read()
    if not data:
        return JSONResponse(status_code=400, content={"error": "empty file"})
    if len(data) > _MAX_PHOTO_BYTES:
        return JSONResponse(status_code=413, content={"error": "file too large"})

    safe_domain = re.sub(r"[^a-z0-9]+", "-", domain).strip("-") or "misc"
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    filed = []
    for subject in subjects:
        safe_subject = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-") or "unknown"
        dest_dir = PHOTO_DIR / safe_domain / safe_subject
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{stamp}{ext}"
        dest.write_bytes(data)
        try:
            rec = records_store.attach_photo(subject, str(dest), caption or None, domain)
        except Exception as e:  # noqa: BLE001 — keep the file even if the record write hiccups
            return JSONResponse(status_code=500,
                                content={"error": f"saved file but record failed: {e}", "saved": str(dest)})
        filed.append({"subject": rec.get("subject", subject), "created": bool(rec.get("created")),
                      "saved": str(dest)})

    # Surface a mis-file loudly: a newly *created* entity usually means a typo'd or compound
    # subject that matched no existing bed/pup — exactly the silent-junk failure we want caught.
    new = [f["subject"] for f in filed if f["created"]]
    warning = (f"⚠️ created NEW {'entity' if len(new) == 1 else 'entities'} "
               + ", ".join(repr(n) for n in new)
               + " — if that wasn't intended, the subject didn't match an existing record.") if new else None
    return {"ok": True, "domain": domain, "bytes": len(data), "filed": filed, "warning": warning,
            # back-compat: first subject's values, where older clients read them flat
            "subject": filed[0]["subject"], "saved": filed[0]["saved"]}


@app.get("/nfc")
async def nfc_capture(token: str = "", kind: str = "", subject: str = ""):
    """Render the capture form for a scanned tag. Never touches the model — see nfc.py."""
    if not NFC_TOKEN or token != NFC_TOKEN:
        return HTMLResponse(nfc.bad_token_page(), status_code=401)
    if not subject:
        return HTMLResponse(nfc.error_page("Tag URL is missing 'subject'.", "400"), status_code=400)
    return HTMLResponse(nfc.capture_form(kind, subject, token))


@app.post("/nfc/log")
async def nfc_log(request: Request):
    """Write the scan (harvest or service) straight to records_store and confirm. Runs off the
    event loop — sqlite writes are sync — but is a plain deterministic call, no agent involved."""
    form = await request.form()
    token = str(form.get("token") or "")
    if not NFC_TOKEN or token != NFC_TOKEN:
        return HTMLResponse(nfc.bad_token_page(), status_code=401)
    kind = str(form.get("kind") or "")
    subject = str(form.get("subject") or "").strip()
    if not subject:
        return HTMLResponse(nfc.error_page("Tag URL is missing 'subject'.", "400"), status_code=400)

    if kind == "harvest":
        body, status = await asyncio.to_thread(
            nfc.log_harvest_tag, subject, str(form.get("crop") or ""),
            str(form.get("qty") or ""), str(form.get("unit") or ""))
    elif kind == "service":
        body, status = await asyncio.to_thread(
            nfc.log_service_tag, subject, str(form.get("note") or ""))
    elif kind == "use":
        body, status = await asyncio.to_thread(
            nfc.log_use_tag, subject, str(form.get("minutes") or ""), str(form.get("note") or ""))
    else:
        body, status = nfc.error_page(f"Unknown kind '{kind}'.", "400"), 400
    return HTMLResponse(body, status_code=status)


# --- Voice loop (browser mic -> brain -> Wyoming services) -------------------------------------
# The chat client captures audio with MediaRecorder (webm/opus on Chrome, mp4 on Safari) and
# POSTs the blob; the brain decodes it to 16 kHz mono PCM with ffmpeg and streams it to the STT
# service, then sends replies back through the TTS service. Endpoints are named OpenAI-style so
# the brain keeps speaking one dialect.

async def _ffmpeg_to_pcm(data: bytes) -> bytes:
    """Decode arbitrary browser-recorded audio to raw 16 kHz mono s16le for faster-whisper.
    ffmpeg reads the container from the bytes themselves, so we don't care what the phone chose."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-f", "s16le", "-ac", "1", "-ar", str(STT_RATE), "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(data)
    if proc.returncode != 0:
        raise RuntimeError((err.decode(errors="replace")[:300] or "ffmpeg failed").strip())
    return out


async def _wyoming_stt(pcm: bytes) -> str:
    """Stream PCM to wyoming-faster-whisper and return the transcript (the path HA Assist uses)."""
    async with AsyncTcpClient(*WHISPER_ADDR) as c:
        await c.write_event(Transcribe(language="en").event())
        await c.write_event(AudioStart(rate=STT_RATE, width=2, channels=1).event())
        for i in range(0, len(pcm), 2048):
            await c.write_event(AudioChunk(audio=pcm[i:i + 2048], rate=STT_RATE, width=2, channels=1).event())
        await c.write_event(AudioStop().event())
        while True:
            ev = await c.read_event()
            if ev is None:
                return ""
            if Transcript.is_type(ev.type):
                return Transcript.from_event(ev).text.strip()


async def _wyoming_tts(text: str) -> bytes:
    """Synthesize text with wyoming-piper and return a complete WAV (the browser plays one blob)."""
    rate = width = channels = None
    frames: list[bytes] = []
    async with AsyncTcpClient(*PIPER_ADDR) as c:
        await c.write_event(Synthesize(text=text).event())
        while True:
            ev = await c.read_event()
            if ev is None:
                break
            if AudioStart.is_type(ev.type):
                a = AudioStart.from_event(ev)
                rate, width, channels = a.rate, a.width, a.channels
            elif AudioChunk.is_type(ev.type):
                ch = AudioChunk.from_event(ev)
                rate, width, channels = ch.rate, ch.width, ch.channels  # AudioStart is optional
                frames.append(ch.audio)
            elif AudioStop.is_type(ev.type):
                break
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels or 1)
        w.setsampwidth(width or 2)
        w.setframerate(rate or 22050)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


@app.post("/v1/audio/transcriptions")
async def transcribe_audio(request: Request):
    """Mic -> text. Accepts a multipart upload (field `file`) of whatever the browser recorded;
    returns {"text": ...} (OpenAI's audio-transcription shape). The phone never touches the GPU
    service directly — same one-hop-to-the-brain posture as the text and photo paths."""
    form = await request.form()
    file = form.get("file")
    if file is None or isinstance(file, str):
        return JSONResponse(status_code=400, content={"error": "missing 'file' (the recorded audio)"})
    data = await file.read()
    if not data:
        return JSONResponse(status_code=400, content={"error": "empty audio"})
    try:
        pcm = await _ffmpeg_to_pcm(data)
        text = await _wyoming_stt(pcm)
    except Exception as e:  # noqa: BLE001 — surface a clean error so the client can show it
        return JSONResponse(status_code=502, content={"error": f"transcription failed: {e}"})
    return {"text": text}


@app.post("/v1/audio/speech")
async def synthesize_speech(request: Request):
    """Text -> spoken WAV (OpenAI's `input` field). The client calls this for replies to voice
    turns, so Hestia talks back; piper runs on CPU and is sub-second, so we return one buffer."""
    body = await request.json()
    text = (body.get("input") or body.get("text") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "missing 'input' (text to speak)"})
    try:
        wav = await _wyoming_tts(text)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"error": f"synthesis failed: {e}"})
    return Response(content=wav, media_type="audio/wav")
