"""Held-out generalization probe — does trained actuation transfer to an UNSEEN room?

sft_gen_v2 withholds the 'outside' room wholesale (HELDOUT_ROOM): no training example
ever actuates light.light_outside_lights, but the entity IS present in the live scoped
prompt at inference. So this measures pure generalization of the tool *shape*, not recall.

It reuses the exact production path (hestia._system_prompt + _request_schemas, i.e. the
home_control skill scoping) and grades the FIRST tool call at three levels:
  fired   — emitted a `home` tool call at all (vs answering in prose = the agency cliff)
  action  — turn_on/turn_off matches gold
  entity  — entity_id matches gold
  exact   — action AND entity both correct

Usage:
  set -a; . secrets/ha.env; set +a
  uv run --project brain python brain/eval_heldout.py qwen3-4b-toolfire-v2 qwen3:4b
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

import config  # noqa: E402

config.load_secrets()

import hestia  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
REPEATS = int(os.environ.get("EVAL_REPEATS", "1"))
HELDOUT = os.path.join(os.path.dirname(__file__), "sft_data", "toolfire_v2.heldout.jsonl")


def load_cases() -> list[dict]:
    cases = []
    for line in open(HELDOUT):
        msgs = json.loads(line)["messages"]
        user = next(m["content"] for m in msgs if m["role"] == "user")
        gold = next(m for m in msgs if m["role"] == "assistant")["tool_calls"][0]["function"]
        args = json.loads(gold["arguments"]) if isinstance(gold["arguments"], str) else gold["arguments"]
        cases.append({"user": user, "action": args.get("action"), "entity": args.get("entity_id")})
    return cases


def fire(model: str, prompt: str) -> tuple[str, dict]:
    """One model turn via the real production scoping. Returns (tool_name|'∅', args)."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": hestia._system_prompt(prompt)},
                     {"role": "user", "content": prompt}],
        "tools": hestia._request_schemas(prompt), "stream": False, "think": False,
        "options": {"temperature": 0.3},
    }
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=300)
    r.raise_for_status()
    calls = (r.json()["message"].get("tool_calls") or [])
    if not calls:
        return "∅", {}
    fn = calls[0].get("function", {})
    args = fn.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:  # noqa: BLE001
            args = {}
    return fn.get("name", "?"), args


def eval_model(model: str, cases: list[dict]) -> None:
    print(f"\n{'='*72}\nMODEL: {model}   (cases={len(cases)} × repeats={REPEATS})\n{'='*72}")
    fired = action = entity = exact = misfire = total = 0
    fails = []
    for c in cases:
        for _ in range(REPEATS):
            total += 1
            try:
                name, args = fire(model, c["user"])
            except Exception as e:  # noqa: BLE001
                name, args = f"ERR:{str(e)[:30]}", {}
            if name == "home":
                fired += 1
                a_ok = args.get("action") == c["action"]
                e_ok = args.get("entity_id") == c["entity"]
                action += a_ok
                entity += e_ok
                if a_ok and e_ok:
                    exact += 1
                else:
                    fails.append(f"    ~ {c['user'][:42]:<42} got action={args.get('action')} entity={args.get('entity_id')}")
            else:
                if name != "∅":
                    misfire += 1  # fired the WRONG tool
                fails.append(f"    ✗ {c['user'][:42]:<42} -> {name}")
    pct = lambda n: f"{n/total*100:5.1f}%"
    print(f"  fired home : {fired:>3}/{total}  {pct(fired)}   (∅/wrong-tool = the agency cliff)")
    print(f"  action ok  : {action:>3}/{total}  {pct(action)}")
    print(f"  entity ok  : {entity:>3}/{total}  {pct(entity)}")
    print(f"  EXACT      : {exact:>3}/{total}  {pct(exact)}   <- held-out generalization score")
    if misfire:
        print(f"  wrong-tool : {misfire}")
    if fails:
        print("  misses:")
        for f in fails[:20]:
            print(f)
        if len(fails) > 20:
            print(f"    ... +{len(fails)-20} more")
    import subprocess
    subprocess.run(["ollama", "stop", model], capture_output=True)


def main() -> None:
    cases = load_cases()
    for model in sys.argv[1:] or ["qwen3-4b-toolfire-v2"]:
        t0 = time.time()
        eval_model(model, cases)
        print(f"  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
