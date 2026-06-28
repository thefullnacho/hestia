#!/usr/bin/env python3
"""Fetch (and optionally save) the HomesteaderLabs lovelace dashboard via HA WS API.

Usage:
  ws_lovelace.py fetch            -> prints live config JSON to stdout
  ws_lovelace.py save <file.json> -> saves file.json as the dashboard config
"""
import asyncio, json, os, sys
import websockets

URL = os.environ["HA_URL"].replace("http", "ws", 1) + "/api/websocket"
TOKEN = os.environ["HA_TOKEN"]
DASH = "homelab-status"

async def run(action, arg):
    async with websockets.connect(URL, max_size=16_000_000) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        _id = 1
        if action == "fetch":
            await ws.send(json.dumps({"id": _id, "type": "lovelace/config", "url_path": DASH}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == _id and m.get("type") == "result":
                    if not m.get("success"):
                        sys.exit("fetch failed: " + json.dumps(m.get("error")))
                    print(json.dumps(m["result"], indent=2))
                    return
        elif action == "save":
            cfg = json.load(open(arg))
            await ws.send(json.dumps({"id": _id, "type": "lovelace/config/save",
                                      "url_path": DASH, "config": cfg}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == _id and m.get("type") == "result":
                    print("save success:", m.get("success"), m.get("error") or "")
                    return

asyncio.run(run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
