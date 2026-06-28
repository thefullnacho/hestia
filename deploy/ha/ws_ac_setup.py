#!/usr/bin/env python3
"""Assign areas + rename entity_ids for the two newly-added Midea ACs."""
import asyncio, json, os, sys
import websockets

URL = os.environ["HA_URL"].replace("http", "ws", 1) + "/api/websocket"
TOKEN = os.environ["HA_TOKEN"]

# device-name -> (preferred area-name-substring, clean entity slug)
PLAN = {
    "Living Room AC":   ("living room",  "climate.living_room_ac"),
    "Master Bedroom AC": ("master",      "climate.master_bedroom_ac"),
}

async def main():
    async with websockets.connect(URL, max_size=8_000_000) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        _id = 0
        async def cmd(p):
            nonlocal _id; _id += 1; p["id"] = _id
            await ws.send(json.dumps(p))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == _id and m.get("type") == "result":
                    return m

        areas = (await cmd({"type": "config/area_registry/list"}))["result"]
        devices = (await cmd({"type": "config/device_registry/list"}))["result"]
        entities = (await cmd({"type": "config/entity_registry/list"}))["result"]

        for devname, (area_sub, slug) in PLAN.items():
            dev = next((d for d in devices if (d.get("name_by_user") or d.get("name")) == devname), None)
            if not dev:
                print(f"!! device {devname!r} not found"); continue
            area = next((a for a in areas if area_sub in a["name"].lower()), None)
            if area:
                r = await cmd({"type": "config/device_registry/update",
                               "device_id": dev["id"], "area_id": area["area_id"]})
                print(f"{devname}: area -> {area['name']} ({'ok' if r.get('success') else r.get('error')})")
            else:
                print(f"{devname}: no area matching {area_sub!r}; areas = {[a['name'] for a in areas]}")
            # rename its climate entity
            ent = next((e for e in entities if e["device_id"] == dev["id"] and e["entity_id"].startswith("climate.")), None)
            if ent and ent["entity_id"] != slug:
                r = await cmd({"type": "config/entity_registry/update",
                               "entity_id": ent["entity_id"], "new_entity_id": slug})
                print(f"{devname}: {ent['entity_id']} -> {slug} ({'ok' if r.get('success') else r.get('error')})")
            elif ent:
                print(f"{devname}: entity already {slug}")

asyncio.run(main())
