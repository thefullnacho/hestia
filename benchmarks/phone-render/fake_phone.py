"""A stand-in for the iPhone app's model side, for testing the box before the app exists.

It does what the app will: dials the brain's /v1/model/socket with a device token, says hello
with the sha256 of the model it runs, then answers each "complete" by running the prompt (here
on a llama-server's /completion, streaming) and sending the text back as deltas and a done.

    uv run --project brain python benchmarks/phone-render/fake_phone.py \\
        --brain ws://127.0.0.1:8733 --token <device token> \\
        --llama http://127.0.0.1:8099 --model LFM3-1.7B-QAD-Q4_0.gguf
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json

import httpx
import websockets


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def answer(ws, llama: httpx.AsyncClient, req: dict) -> None:
    rid = req["id"]
    body = {k: v for k, v in req.items() if k not in ("type", "id")}
    try:
        async with llama.stream("POST", "/completion", json={**body, "stream": True}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                if event.get("content"):
                    await ws.send(json.dumps({"type": "delta", "id": rid, "text": event["content"]}))
                if event.get("stop"):
                    await ws.send(json.dumps({"type": "done", "id": rid, "stop_type": event.get("stop_type"),
                                              "tokens_evaluated": event.get("tokens_evaluated", 0),
                                              "tokens_predicted": event.get("tokens_predicted", 0)}))
                    return
    except Exception as e:  # noqa: BLE001
        await ws.send(json.dumps({"type": "error", "id": rid, "message": f"{type(e).__name__}: {e}"}))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--llama", default="http://127.0.0.1:8099")
    ap.add_argument("--model", required=True, help="the GGUF the brain serves, to prove we run the same one")
    args = ap.parse_args()
    digest = await asyncio.to_thread(sha256, args.model)
    async with websockets.connect(args.brain.rstrip("/") + "/v1/model/socket",
                                  additional_headers={"Authorization": f"Bearer {args.token}"}) as ws, \
            httpx.AsyncClient(base_url=args.llama, timeout=300) as llama:
        await ws.send(json.dumps({"type": "hello", "device": "fake-phone", "model_sha256": digest}))
        print("ready:", await ws.recv(), flush=True)
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "complete":
                print(f"complete #{msg['id']}: {len(msg['prompt'])} chars", flush=True)
                await answer(ws, llama, msg)


if __name__ == "__main__":
    asyncio.run(main())
