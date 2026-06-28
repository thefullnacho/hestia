#!/usr/bin/env python3
"""Assign the newly-paired Android TV device to the Living Room area via HA WS API."""
import asyncio, json, os, sys
import websockets

URL = os.environ["HA_URL"].replace("http", "ws", 1) + "/api/websocket"
TOKEN = os.environ["HA_TOKEN"]
ENTRY_ID = "01KTQ636WSE969JWC6XY7KKSE8"  # androidtv_remote config entry

async def main():
    async with websockets.connect(URL, max_size=8_000_000) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        _id = 0
        async def cmd(payload):
            nonlocal _id
            _id += 1
            payload["id"] = _id
            await ws.send(json.dumps(payload))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == _id and msg.get("type") == "result":
                    return msg

        areas = (await cmd({"type": "config/area_registry/list"}))["result"]
        devices = (await cmd({"type": "config/device_registry/list"}))["result"]

        lr = next((a for a in areas if a["name"].lower() in ("living room",)), None)
        if not lr:
            print("Areas found:", [a["name"] for a in areas]); sys.exit("No Living Room area")
        tv = next((d for d in devices if ENTRY_ID in d.get("config_entries", [])), None)
        if not tv:
            sys.exit("TV device not found for entry " + ENTRY_ID)
        print(f"TV device: {tv['id']}  name={tv.get('name_by_user') or tv.get('name')}  current_area={tv.get('area_id')}")
        print(f"Living Room area_id: {lr['area_id']}")
        if tv.get("area_id") == lr["area_id"]:
            print("Already in Living Room — nothing to do."); return
        res = await cmd({"type": "config/device_registry/update",
                         "device_id": tv["id"], "area_id": lr["area_id"]})
        print("Update success:", res.get("success"), "-> area now:", res["result"].get("area_id"))

asyncio.run(main())
