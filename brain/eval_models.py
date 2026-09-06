"""Compare local models through the production loop using isolated synthetic state.

uv run --project brain python brain/eval_models.py qwen3:14b:nothink
No household credentials, live snapshots, service commands or external tool writes.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import statistics
import sys
import time

from eval_support import fixtures, hestia, records_store

REPEATS = int(os.environ.get("EVAL_REPEATS", "3"))


def _logged(state):
    return any(e["subject"] == "Biscuit" and "vaccinat" in
               ((e["action"] or "") + (e["detail"] or "")).lower()
               for e in records_store.recent_events())


CASES = [
    ("light write", ["Turn off the kitchen lights."],
     lambda text, s: s["light"] == "off"),
    ("record write", ["Log that Biscuit was vaccinated today."],
     lambda text, s: _logged(s)),
    ("record read", ["What breed is Biscuit?"],
     lambda text, s: "corgi" in text.lower() and not records_store.recent_events()),
    ("memory recall", ["Which bag has the good coffee?"],
     lambda text, s: "orange" in text.lower()),
    ("mixed request", ["Turn off the kitchen lights and add milk to the shopping list."],
     lambda text, s: s["light"] == "off" and "milk" in s["shopping"]),
    ("mixed read and write", ["Turn off the kitchen lights and tell me what breed Biscuit is."],
     lambda text, s: s["light"] == "off" and "corgi" in text.lower()),
    ("follow-up", ["Turn off the kitchen lights.", "Turn them back on."],
     lambda text, s: s["light"] == "on" and len(s["calls"]) >= 2),
    ("unavailable backend", ["Search the web for today's technology news."],
     lambda text, s: any(w in text.lower() for w in ("unavailable", "disabled", "couldn't", "can't", "cannot", "unable", "error"))),
]


async def evaluate(specs):
    rows = []
    for spec in specs:
        think = spec.endswith(":think")
        model = spec.rsplit(":", 1)[0] if spec.endswith((":think", ":nothink")) else spec
        model_rows = []
        for name, turns, check in CASES:
            for repeat in range(REPEATS):
                with fixtures() as state:
                    messages, traces, response = [], [], ""
                    for prompt in turns:
                        messages.append({"role": "user", "content": prompt})
                        trace = hestia.TurnTrace(model=model, think=think)
                        response = await hestia.run_agent(messages, trace=trace)
                        messages.append({"role": "assistant", "content": response})
                        traces.append(asdict(trace))
                    row = {"model": model, "think": think, "case": name, "repeat": repeat,
                           "pass": bool(check(response, state)), "response": response,
                           "calls": state["calls"], "traces": traces,
                           "seconds": sum(t["total_seconds"] for t in traces)}
                    rows.append(row)
                    model_rows.append(row)
                    print(f"{name}: {'PASS' if row['pass'] else 'FAIL'} ({row['seconds']:.2f}s)")
        times = sorted(r["seconds"] for r in model_rows)
        print(f"{spec}: {sum(r['pass'] for r in model_rows)}/{len(model_rows)}; "
              f"p50={statistics.median(times):.2f}s p95={times[min(len(times)-1, int(len(times)*.95))]:.2f}s")
    return rows


def main():
    rows = asyncio.run(evaluate(sys.argv[1:] or [hestia.MODEL]))
    # Synthetic-only output, outside the public repository by default.
    path = Path(os.environ.get("EVAL_OUTPUT", f"/tmp/hestia-eval-{time.time_ns()}.json"))
    path.write_text(json.dumps(rows, indent=2))
    print(f"Results: {path}")
    if any(not r["pass"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
