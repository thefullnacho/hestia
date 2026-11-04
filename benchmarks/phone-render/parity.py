"""Does the box render prompts and read tool calls exactly the way llama-server does?

For the phone, the box renders the model's prompt itself and reads tool calls out of plain text
(brain/chat_format.py). This holds both halves against llama-server on real Hestia turns: the
real system prompt and the real scoped tools for each sentence, then a second turn carrying the
tool call and its result.

  render:  chat_format.render() vs llama-server's /apply-template, byte for byte
  parse:   chat_format.parse() of /completion's raw text (greedy) vs the tool calls
           llama-server's own parser returns from /v1/chat/completions (greedy)

Start llama-server with the same template the box will be given, then run from the repo root:
    llama-server -m LFM3-1.7B-QAD-Q4_0.gguf --chat-template-file chat_template.jinja --jinja \\
        --host 127.0.0.1 --port 8099 -ngl 99 -c 16384
    uv run --project brain python benchmarks/phone-render/parity.py --template chat_template.jinja
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "brain"))
from eval_support import hestia  # noqa: E402  (imports the brain without its secrets)
import backend  # noqa: E402
import chat_format  # noqa: E402
import routing  # noqa: E402

SENTENCES = [
    "Turn off the kitchen lights.", "Shut off the outside lights.", "Dim the living room to 30 percent.",
    "Remind me to call the vet tomorrow at nine.", "Set a timer for ten minutes.",
    "What reminders do I have?", "Add milk and eggs to the shopping list.", "What's on the shopping list?",
    "We're out of coffee.", "Log that I harvested six peppers from the tomato bed.",
    "Remember that the good coffee is the bag with the orange label.", "Which coffee did I say was the good one?",
    "What's on the calendar this week?", "Will it frost tonight?", "How do I overwinter dahlias?",
    "Is purslane safe to eat?", "What's the moisture in the garden beds?", "Should I water the carrots?",
    "Play some music in the kitchen.", "Find the new Dune movie.", "Make a plan to winterize the greenhouse.",
    "I vaccinated the dogs today.", "Tell me a joke.", "Thanks, that's all.",
    "Turn on the porch light and remind me to turn it off at ten.",
]
OPTS = {"temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}


def turn(text: str) -> tuple[list[dict], list[dict]]:
    """The brain's own messages and offered tools for this sentence, as the loop would send them."""
    plan = routing.context_plan(text)
    token = routing.prepared.set(plan)
    try:
        tools = routing.request_schemas(text)
    finally:
        routing.prepared.reset(token)
    return [{"role": "system", "content": hestia._system_prompt(text, plan)},
            {"role": "user", "content": text}], tools


def server_calls(msg: dict) -> list[dict]:
    out = []
    for c in msg.get("tool_calls") or []:
        try:
            args = json.loads(c["function"]["arguments"] or "{}")
        except ValueError:  # cut off at the token cap: keep the text so the row shows it
            args = c["function"]["arguments"]
        out.append({"function": {"name": c["function"]["name"], "arguments": args}})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8099")
    ap.add_argument("--template", required=True)
    ap.add_argument("--bos", default="<|startoftext|>")
    args = ap.parse_args()
    template = Path(args.template).read_text()
    http = httpx.Client(base_url=args.server, timeout=120)
    rows, render_ok, parse_ok, cases = [], 0, 0, 0

    def check(label, messages, tools):
        nonlocal render_ok, parse_ok, cases
        cases += 1
        ours = chat_format.render(messages, tools, template, bos_token=args.bos)
        wire = backend.openai_messages(messages)
        theirs = http.post("/apply-template", json={"messages": wire, "tools": tools, **OPTS}).json()["prompt"]
        same_render = ours == theirs
        raw = http.post("/completion", json={"prompt": ours, "n_predict": 256, "temperature": 0,
                                             "cache_prompt": False}).json()["content"]
        chat = http.post("/v1/chat/completions", json={"messages": wire, "tools": tools, "max_tokens": 256,
                                                       **OPTS}).json()["choices"][0]["message"]
        mine = chat_format.parse(raw, tools)
        same_calls = mine.get("tool_calls", []) == server_calls(chat)
        render_ok += same_render
        parse_ok += same_calls
        rows.append({"case": label, "render": same_render, "calls": same_calls, "raw": raw,
                     "ours": mine.get("tool_calls", []), "server": server_calls(chat),
                     **({} if same_render else {"diff_at": next((i for i, (a, b) in enumerate(zip(ours, theirs)) if a != b),
                                                                 min(len(ours), len(theirs))),
                                                "ours_len": len(ours), "theirs_len": len(theirs)})})
        print(f"{'ok ' if same_render and same_calls else 'DIFF'} render={same_render!s:5} calls={same_calls!s:5} {label}")
        return chat

    for text in SENTENCES:
        messages, tools = turn(text)
        chat = check(text, messages, tools)
        calls = server_calls(chat)
        if calls and all(isinstance(c["function"]["arguments"], dict) for c in calls):
            # the second model call of the turn: the call and a plain result
            follow = messages + [{"role": "assistant", "content": "", "tool_calls": calls},
                                 {"role": "tool", "tool_name": calls[0]["function"]["name"],
                                  "content": "Done."}]
            check(text + "  [after the tool result]", follow, tools)

    print(f"\nrender identical {render_ok}/{cases}, tool calls identical {parse_ok}/{cases}")
    out = Path(__file__).with_name("parity_results.json")
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return 0 if render_ok == parse_ok == cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
