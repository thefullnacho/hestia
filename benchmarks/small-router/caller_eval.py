"""Score a FunctionGemma checkpoint as the tool caller, given the right domain's tools.

    uv run --with torch --with transformers --with jinja2 python caller_eval.py MODEL_DIR [--rows cases.jsonl]

Runs on a GPU through transformers so the official chat template is used exactly and the
special tokens stay visible for parsing. Accuracy here should match the Pi apart from
quantization; the Pi run is for latency and a final check of the quantized model.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

import caller


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--rows", default="cases.jsonl")
    ap.add_argument("--split", help="only rows with this split (train.jsonl has train/val)")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--show", type=int, default=40, help="failures to print")
    ap.add_argument("--json", help="write per-row results here")
    a = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(a.model)
    tok.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=torch.bfloat16).to("cuda").eval()
    stops = [tok.convert_tokens_to_ids(t) for t in ("<end_of_turn>", "<start_function_response>", "<eos>")]

    rows = [r for r in caller.load_rows(a.rows) if not a.split or r.get("split") == a.split]
    rows = [r for r in rows if caller.domain_of(r)]
    prompts = [tok.apply_chat_template(caller.messages(r), tools=caller.DOMAINS[caller.domain_of(r)],
                                       add_generation_prompt=True, tokenize=False) for r in rows]
    outs = []
    for i in range(0, len(prompts), a.batch):
        enc = tok(prompts[i:i + a.batch], return_tensors="pt", padding=True, add_special_tokens=False).to("cuda")
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=96, do_sample=False, eos_token_id=stops,
                                 pad_token_id=tok.pad_token_id)
        outs += tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=False)

    by_expect, by_tool, tool_right = defaultdict(Counter), defaultdict(Counter), Counter()
    fails, results = [], []
    for r, out in zip(rows, outs):
        out = out.replace("<pad>", "")
        calls, said = caller.parse(out)
        ok = caller.passes(r, calls, said)
        by_expect[r["expect"]]["n"] += 1
        by_expect[r["expect"]]["ok"] += ok
        key = r["calls"][0]["tool"] if r["calls"] else r["expect"]
        by_tool[key]["n"] += 1
        by_tool[key]["ok"] += ok
        if r["expect"] == "call":
            tool_right["n"] += 1
            tool_right["ok"] += bool(calls) and calls[0]["tool"] == r["calls"][0]["tool"]
        results.append({"id": r["id"], "ok": ok, "calls": calls, "said": said})
        if not ok:
            fails.append(f"  {r['id']:8} {r['expect']:4} {r['text']!r}\n"
                         f"           want {json.dumps(r['calls']) if r['calls'] else r['expect']}\n"
                         f"           got  {json.dumps(calls) if calls else repr(said)}")

    gate = Counter()
    for r, res in zip(rows, results):
        if res["calls"]:
            kept = caller.spans_said(r["text"], res["calls"])
            gate[("right" if res["ok"] else "wrong") + (" kept" if kept else " refused")] += 1
    n = sum(v["n"] for v in by_expect.values())
    ok = sum(v["ok"] for v in by_expect.values())
    print(f"{a.model} on {a.rows}{' ' + a.split if a.split else ''}: {ok}/{n} = {ok / n:.0%}")
    print(f"right tool on call cases: {tool_right['ok']}/{tool_right['n']} = {tool_right['ok'] / tool_right['n']:.0%}")
    print(f"after the said-words gate: wrong actions {gate['wrong kept']} (caught {gate['wrong refused']}),"
          f" right calls refused {gate['right refused']}")
    for k, v in sorted(by_expect.items()):
        print(f"  expect {k:5} {v['ok']:4}/{v['n']}")
    for k, v in sorted(by_tool.items()):
        print(f"  {k:17} {v['ok']:4}/{v['n']}")
    print(f"failures (first {a.show}):")
    print("\n".join(fails[:a.show]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(results, f)


if __name__ == "__main__":
    main()
