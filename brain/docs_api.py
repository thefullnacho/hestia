"""The Today page's half of house docs: keep, edit or drop what the model proposed. No model.

The `doc` tool proposes (docs_store.py); nothing reaches the library until a person keeps it here.
Writes follow memory_api's guard: a custom header, a same-origin check and a JSON body, so a link
or a form on another page cannot file a doc.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import docs_store
import memory_api

HEADER = "X-Hestia-Docs"


def router() -> APIRouter:
    api = APIRouter()

    async def run(fn, *args):
        try:
            out = await asyncio.to_thread(fn, *args)
        except LookupError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except (ValueError, TypeError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        left = len(await asyncio.to_thread(docs_store.pending))
        return {"ok": True, "result": out, "left": left}

    async def guarded(request: Request):
        try:
            return await memory_api.body_json(request, HEADER, "review house docs", 64 * 1024), None
        except (ValueError, json.JSONDecodeError) as e:
            return None, JSONResponse({"ok": False, "error": str(e)}, status_code=409)

    @api.get("/api/docs/proposed")
    async def proposed():
        return JSONResponse({"docs": await asyncio.to_thread(docs_store.pending)},
                            headers={"Cache-Control": "no-store"})

    @api.post("/api/docs/{doc_id}/keep")
    async def keep(doc_id: int, request: Request):
        """Into the library: as proposed, or with the title and body the person edited."""
        data, err = await guarded(request)
        if err:
            return err
        title, body = data.get("title"), data.get("body")
        if any(v is not None and not isinstance(v, str) for v in (title, body)):
            return JSONResponse({"ok": False, "error": "title and body must be text"}, status_code=409)
        return await run(docs_store.keep, doc_id, title, body)

    @api.post("/api/docs/{doc_id}/discard")
    async def discard(doc_id: int, request: Request):
        _, err = await guarded(request)
        return err or await run(docs_store.discard, doc_id)

    return api
