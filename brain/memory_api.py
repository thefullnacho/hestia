"""The app's half of memory review: keep or drop what the note-taker proposed. No model.

The note-taker proposes durable facts into an inbox and nothing is remembered until a person
says so (CLAUDE.md, invariant 4). This adds the app as a second place to say so, beside
review_notes.py at the terminal; both call review_notes.promote() and discard(). The model has
no tool that reaches either.

Writes follow the goals page guard: a custom header, a same-origin check and a JSON body, so a
link, a form or a dashboard tile on another page cannot nod a fact through. The brain's network
boundary is still the access control; this only stops cross-site requests riding on it.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import review_notes

HEADER = "X-Hestia-Memory"
MAX_BODY = 16 * 1024


async def body_json(request: Request, header: str = HEADER, what: str = "review what Hestia remembers") -> dict:
    """The guard on the app's review writes (memory here, receipts in receipts_api): the page's own
    header, the same origin, a JSON object of bounded size."""
    if request.headers.get(header) != "1":
        raise ValueError(f"Use the app to {what}.")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise ValueError("That must come from this Hestia page.")
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ValueError("Expected JSON from the app.")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ValueError("Request too large.")
    data = json.loads(raw or b"{}")
    if not isinstance(data, dict):
        raise ValueError("Expected an object.")
    return data


def router() -> APIRouter:
    api = APIRouter()

    async def run(fn, *args):
        try:
            out = await asyncio.to_thread(fn, *args)
        except LookupError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except (ValueError, TypeError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        left = len(await asyncio.to_thread(review_notes.proposals))
        return {"ok": True, "memory": out, "left": left}

    @api.post("/api/memory/inbox/{rid}/keep")
    async def keep(rid: str, request: Request):
        """Remember it: as proposed, or in the person's own words when `text` is sent."""
        try:
            data = await body_json(request)
        except (ValueError, json.JSONDecodeError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        text = data.get("text")
        if text is not None and not isinstance(text, str):
            return JSONResponse({"ok": False, "error": "text must be a string"}, status_code=409)
        return await run(review_notes.promote, rid, text)

    @api.post("/api/memory/inbox/{rid}/discard")
    async def discard(rid: str, request: Request):
        try:
            await body_json(request)
        except (ValueError, json.JSONDecodeError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        return await run(review_notes.discard, rid)

    return api
