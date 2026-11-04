"""The held-out light commands on the whole turn, scored the way benchmarks/RESULTS.md describes.

63 commands against the 'outside' room, run through hestia.run_agent against the eval fixtures
(harness recovery counts). A try is exact when the first turn_on/turn_off that actually ran has
the gold action and an id that resolves, the way the home tool resolves ids, to the gold light.

Which path the model takes is the backend's environment, set before this starts:
    # llama-server's chat endpoint (how the 91.1% was measured)
    HESTIA_BACKEND=openai HESTIA_OPENAI_BASE=http://127.0.0.1:8099/v1 \\
      uv run --project brain python benchmarks/phone-render/heldout_turn.py --repeats 5
    # the phone's path: the box renders, plain /completion, the box parses
    HESTIA_BACKEND=completion HESTIA_COMPLETION_BASE=http://127.0.0.1:8099 \\
      HESTIA_CHAT_TEMPLATE=chat_template.jinja HESTIA_BOS_TOKEN='<|startoftext|>' \\
      uv run --project brain python benchmarks/phone-render/heldout_turn.py --repeats 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "brain"))
from eval_support import fixtures, hestia, run  # noqa: E402
import eval_heldout  # noqa: E402
import tools  # noqa: E402

FIXTURE_LIGHTS = [{"entity_id": "light.light_kitchen_lights", "attributes": {"friendly_name": "Kitchen lights"}},
                  {"entity_id": "light.light_outside_lights", "attributes": {"friendly_name": "Outside lights"}}]


def first_switch(prompt: str) -> dict | None:
    with fixtures() as state:
        run(hestia.run_agent([{"role": "user", "content": prompt}]))
    return next((c["args"] for c in state["calls"]
                 if c["name"] == "home" and c["args"].get("action") in ("turn_on", "turn_off")), None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--label", default=os.environ.get("HESTIA_BACKEND", "ollama"))
    ap.add_argument("--cases", default=eval_heldout.HELDOUT, help="the held-out jsonl (gitignored sft_data)")
    args = ap.parse_args()
    eval_heldout.HELDOUT = args.cases
    cases = eval_heldout.load_cases()
    rows, exact, t0 = [], 0, time.time()
    for rep in range(args.repeats):
        for case in cases:
            try:
                call = first_switch(case["user"])
            except Exception as e:  # noqa: BLE001
                call = {"error": type(e).__name__ + ": " + str(e)[:80]}
            resolved = tools.home._resolve(call.get("entity_id") or "", FIXTURE_LIGHTS) if call else None
            ok = bool(call) and call.get("action") == case["action"] and resolved == case["entity"]
            exact += ok
            rows.append({"rep": rep, "user": case["user"], "gold": [case["action"], case["entity"]],
                         "call": call, "resolved": resolved, "exact": ok})
        print(f"rep {rep + 1}: {sum(r['exact'] for r in rows if r['rep'] == rep)}/{len(cases)}", flush=True)
    total = len(rows)
    print(f"\n{args.label}: exact {exact}/{total} = {exact / total * 100:.1f}%  ({time.time() - t0:.0f}s)")
    out = Path(__file__).with_name(f"heldout_turn_{args.label}.jsonl")
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
