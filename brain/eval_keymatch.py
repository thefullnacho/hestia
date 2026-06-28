"""Key-matching probe — does the brain reach for the RIGHT tool for a given input?

This isolates the production failure the benchmarks miss: the model emits perfectly
valid output but picks the wrong tool ("key matching"). It runs ONE model turn with the
brain's real system prompt + real per-request tool scoping + native Ollama tool-calling,
then reads the FIRST tool it tried — and never dispatches anything, so there are zero
side effects (no phone pings, no records writes, no media grabs).

The battery targets the confusable boundaries in the *unscoped* tool surface, where
memory / records / reminder / search / weather / media all compete:

  memory   = loose, durable facts & preferences
  records  = entities + dated event log (pets, sightings, chores, service)
  reminder = a future time-triggered phone ping
  weather  = the homestead forecast
  search   = live web lookup
  media    = acquire/manage a named title

Usage:
  uv run --project brain python brain/eval_keymatch.py qwen3:14b qwen3:8b
  EVAL_REPEATS=5 uv run --project brain python brain/eval_keymatch.py qwen2.5:14b
"""
from __future__ import annotations

import os
import sys
import time

import httpx

import config  # noqa: E402 — puts brain/ on sys.path

config.load_secrets()

import hestia  # noqa: E402 — real _system_prompt + _request_schemas
import tools   # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
REPEATS = int(os.environ.get("EVAL_REPEATS", "5"))

# Each case: a user line, the acceptable tool(s), and tools that would be a clear miss.
# 'forbid' is the diagnostic half — it catches the specific confusion we expect.
CASES = [
    {"name": "reminder vs memory/records (umbrella)",
     "prompt": "Remind me to bring an umbrella tomorrow at 7am.",
     "want": {"reminder"}, "forbid": {"memory", "records"}},
    {"name": "reminder vs memory (oven, relative)",
     "prompt": "Set a reminder to check the oven in 20 minutes.",
     "want": {"reminder"}, "forbid": {"memory", "records"}},
    {"name": "memory vs records/reminder (coffee pref)",
     "prompt": "Remember that the good coffee is the bag with the orange label.",
     "want": {"memory"}, "forbid": {"records", "reminder"}},
    {"name": "memory recall (coffee)",
     "prompt": "Which coffee did I say was the good one?",
     "want": {"memory"}, "forbid": {"records", "search"}},
    {"name": "records vs memory (new pet entity)",
     "prompt": "We got a new puppy, her name is Biscuit — she's a corgi.",
     "want": {"records"}, "forbid": {"memory"}},
    {"name": "records vs memory (health log)",
     "prompt": "I vaccinated the dogs today.",
     "want": {"records"}, "forbid": {"memory"}},
    {"name": "records vs search/memory (sighting recall)",
     "prompt": "When did I last see a deer in the yard?",
     "want": {"records"}, "forbid": {"search", "memory"}},
    {"name": "weather vs search (rain)",
     "prompt": "Is any rain coming in the next few days?",
     "want": {"weather"}, "forbid": {"search"}},
    {"name": "search vs weather (news)",
     "prompt": "What happened with the Fed meeting today?",
     "want": {"search"}, "forbid": {"weather"}},
    {"name": "media vs search (download title)",
     "prompt": "Grab the latest season of Severance for me.",
     "want": {"media"}, "forbid": {"search"}},
    {"name": "home (sanity)",
     "prompt": "Turn off the kitchen lights.",
     "want": {"home"}, "forbid": set()},
]


def first_tool(model: str, prompt: str) -> tuple[str, int]:
    """One model turn. Return (first tool name or '∅:final', n_tools_offered)."""
    schemas = hestia._request_schemas(prompt)
    body = {
        "model": model,
        "messages": [{"role": "system", "content": hestia._system_prompt(prompt)},
                     {"role": "user", "content": prompt}],
        "tools": schemas, "stream": False, "think": False,
        "options": {"temperature": 0.3},
    }
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=300)
    r.raise_for_status()
    msg = r.json()["message"]
    calls = msg.get("tool_calls") or []
    if not calls:
        return "∅:final", len(schemas)
    return calls[0].get("function", {}).get("name", "?"), len(schemas)


def eval_model(model: str) -> None:
    print(f"\n{'='*72}\nMODEL: {model}   (repeats={REPEATS})\n{'='*72}")
    total_ok = 0
    for case in CASES:
        hits, picks, offered = 0, {}, 0
        for _ in range(REPEATS):
            try:
                tool, offered = first_tool(model, case["prompt"])
            except Exception as e:  # noqa: BLE001
                tool = f"ERR:{str(e)[:30]}"
            picks[tool] = picks.get(tool, 0) + 1
            if tool in case["want"]:
                hits += 1
        total_ok += hits / REPEATS
        rate = hits / REPEATS
        flag = "✅" if rate == 1 else ("⚠️ " if rate > 0 else "❌")
        # Surface the actual distribution of picks so a miss names the wrong key.
        dist = "  ".join(f"{k}×{v}" for k, v in sorted(picks.items(), key=lambda x: -x[1]))
        print(f"  {flag} {case['name']:<40} {hits}/{REPEATS}  [offered {offered}]  {dist}")
    print(f"  ── key-match {total_ok/len(CASES)*100:.0f}%   ({total_ok:.1f}/{len(CASES)} cases)")
    import subprocess
    subprocess.run(["ollama", "stop", model], capture_output=True)


def main() -> None:
    for model in sys.argv[1:] or ["qwen3:14b"]:
        t0 = time.time()
        eval_model(model)
        print(f"  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
