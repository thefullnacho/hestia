"""The Today page's half of receipts: keep, edit or drop what a receipt proposed. No model.

The phone files a receipt's lines (POST /v1/phone/receipts, device token); receipts.py reads
them into proposals. Nothing reaches the inventory until a person keeps it here. Writes follow
memory_api's guard: a custom header, a same-origin check and a JSON body, so a link or a form on
another page cannot add to the inventory.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import memory_api
import receipts

HEADER = "X-Hestia-Inventory"


async def body_json(request: Request) -> dict:
    return await memory_api.body_json(request, HEADER, "review receipts")


def router() -> APIRouter:
    api = APIRouter()

    async def run(fn, *args):
        try:
            out = await asyncio.to_thread(fn, *args)
        except LookupError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except (ValueError, TypeError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        left = len(await asyncio.to_thread(receipts.pending))
        return {"ok": True, "result": out, "left": left}

    async def guarded(request: Request):
        try:
            return await body_json(request), None
        except (ValueError, json.JSONDecodeError) as e:
            return None, JSONResponse({"ok": False, "error": str(e)}, status_code=409)

    @api.get("/api/inventory/receipt-items")
    async def items():
        return JSONResponse({"items": await asyncio.to_thread(receipts.pending)},
                            headers={"Cache-Control": "no-store"})

    @api.post("/api/inventory/receipt-items/{pid}/keep")
    async def keep(pid: int, request: Request):
        """Into the inventory: as read, or with the name and amount the person edited."""
        data, err = await guarded(request)
        if err:
            return err
        name, amount = data.get("name"), data.get("amount")
        if any(v is not None and not isinstance(v, str) for v in (name, amount)):
            return JSONResponse({"ok": False, "error": "name and amount must be text"}, status_code=409)
        return await run(receipts.keep, pid, name, amount)

    @api.post("/api/inventory/receipt-items/{pid}/discard")
    async def discard(pid: int, request: Request):
        _, err = await guarded(request)
        return err or await run(receipts.discard, pid)

    @api.post("/api/inventory/receipts/{rid}/keep-all")
    async def keep_all(rid: str, request: Request):
        _, err = await guarded(request)
        return err or await run(receipts.keep_receipt, rid)

    return api
