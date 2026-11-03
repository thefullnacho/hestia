"""Does a grammar make the caller faster? The same action sentences through the cached-slot
caller three ways: as deployed (stop at the end of the call), with a GBNF grammar built from
each domain's own tool declarations, and with the grammar plus a tight n_predict.

Run on the Pi with the caller server up (servers.sh). Stdlib only:
    python3 grammar_probe.py --runs run_2026-10-26_lean.jsonl --reps 3
Reports caller time, tokens written, and whether each output matches the deployed one.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SERVER = "http://127.0.0.1:8090"
STOPS = ["<end_function_call>", "<start_function_response>", "<end_of_turn>"]
ESC = "<escape>"


def post(body: dict) -> dict:
    req = urllib.request.Request(f"{SERVER}/completion", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def lit(s: str) -> str:
    return json.dumps(s)  # a GBNF string literal is a JSON string


def _block(text: str, i: int) -> tuple[str, int]:
    """The balanced {...} starting at text[i] == "{": its inside, and the index after it."""
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j], j + 1
    raise ValueError("unbalanced braces")


def _top(body: str) -> list[tuple[str, str]]:
    """Top-level key:value pairs of a declaration body, values left as raw text."""
    out, i = [], 0
    while i < len(body):
        m = re.compile(r"(\w+):").match(body, i)
        if not m:
            i += 1
            continue
        i = m.end()
        if body.startswith("{", i):
            value, i = _block(body, i)
        elif body.startswith("[", i):
            j = body.index("]", i)
            value, i = body[i:j + 1], j + 1
        else:
            j = body.find(",", i)
            j = len(body) if j < 0 else j
            value, i = body[i:j], j
        out.append((m.group(1), value))
    return out


def _value(spec: dict) -> str:
    kind = re.search(r"<escape>(\w+)<escape>", spec.get("type", "")).group(1)
    if "enum" in spec:
        return "(" + " | ".join(lit(ESC + v + ESC) for v in re.findall(r"<escape>(.*?)<escape>", spec["enum"])) + ")"
    if kind in ("INTEGER", "NUMBER"):
        return '"-"? [0-9]+ ("." [0-9]+)?'
    if kind == "BOOLEAN":
        return '("true" | "false")'
    if kind == "ARRAY":
        item = _value(dict(_top(spec["items"])))
        return f'"[" ({item} ("," {item})*)? "]"'
    return lit(ESC) + " [^<]* " + lit(ESC)


def grammar_for(before: str) -> str:
    """root: one call to one of this domain's tools. Arguments typed from the declaration, in
    declared order, each at most once, any of them optional."""
    rules, tools = [], []
    for decl in re.findall(r"<start_function_declaration>declaration:(.*?)<end_function_declaration>", before):
        name = decl[:decl.index("{")]
        fields = dict(_top(_block(decl, decl.index("{"))[0]))
        params = dict(_top(fields.get("parameters", "")))
        props = _top(params.get("properties", ""))
        rule = "t-" + name.replace("_", "-")
        tools.append(rule)
        if not props:
            rules.append(f"{rule} ::= {lit(name + '{}')}")
            continue
        args = [f"{lit(key + ':')} {_value(dict(_top(spec)))}" for key, spec in props]
        for k, arg in enumerate(args):
            nxt = f"{rule}-{k + 1}" if k + 1 < len(args) else None
            rules.append(f"{rule}-{k} ::= " + (f"({arg}) (\",\" {nxt})? | {nxt}" if nxt else f"({arg})"))
        rules.append(f"{rule} ::= {lit(name + '{')} {rule}-0? \"}}\"")
    if not tools:
        raise ValueError("no tool declarations found")
    head = f"root ::= {lit('<start_function_call>call:')} ({' | '.join(tools)}) {lit('<end_function_call>')}"
    return "\n".join([head, *rules])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="run_2026-10-26_lean.jsonl")
    ap.add_argument("--prompts", default=str(HERE / "prompts.json"))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--tight", type=int, default=48)
    args = ap.parse_args()
    prompts = json.loads(Path(args.prompts).read_text())
    grammars = {d: grammar_for(b) for d, (b, _) in prompts.items()}
    rows = [json.loads(l) for l in (HERE / args.runs).read_text().splitlines()[1:]]
    cases = [(r["heard"], r["top"][0][0]) for r in rows if r.get("route") == "act" and r["top"][0][0] in prompts]
    slots = list(prompts)
    for slot, (before, _) in enumerate(prompts.values()):
        post({"prompt": before, "id_slot": slot, "cache_prompt": True, "n_predict": 0})

    modes = {"deployed": {}, "grammar": {"grammar": None}, "grammar+tight": {"grammar": None, "n_predict": args.tight}}
    out = {m: [] for m in modes}
    for rep in range(args.reps):
        for heard, domain in cases:
            before, after = prompts[domain]
            for mode, extra in modes.items():
                body = {"prompt": before + heard + after, "id_slot": slots.index(domain), "cache_prompt": True,
                        "n_predict": 96, "temperature": 0, "stop": STOPS}
                if "grammar" in extra:
                    body["grammar"] = grammars[domain]
                if "n_predict" in extra:
                    body["n_predict"] = extra["n_predict"]
                # Reset the slot to the bare tool prompt first, so every mode reads the person's
                # words itself instead of reusing the previous mode's cache (outside the timing).
                post({"prompt": before, "id_slot": slots.index(domain), "cache_prompt": True, "n_predict": 0})
                t0 = time.perf_counter()
                res = post(body)
                wall = time.perf_counter() - t0
                text = res["content"].replace("<end_function_call>", "")
                t = res.get("timings", {})
                out[mode].append({"heard": heard, "domain": domain, "rep": rep, "wall": wall, "text": text,
                                  "predicted_n": t.get("predicted_n"), "predicted_ms": t.get("predicted_ms"),
                                  "prompt_n": t.get("prompt_n"), "prompt_ms": t.get("prompt_ms")})

    base = {(r["heard"], r["rep"]): r["text"] for r in out["deployed"]}
    print(f"{len(cases)} action sentences x {args.reps} reps, caller only\n")
    print(f"{'mode':<15}{'median s':>9}{'worst s':>9}{'read ms':>9}{'tokens':>8}{'ms/token':>10}{'same call':>11}")
    for mode, rs in out.items():
        walls = [r["wall"] for r in rs]
        per = [r["predicted_ms"] / r["predicted_n"] for r in rs if r["predicted_n"]]
        same = sum(base[(r["heard"], r["rep"])].strip() == r["text"].strip() for r in rs)
        print(f"{mode:<15}{statistics.median(walls):>9.2f}{max(walls):>9.2f}"
              f"{statistics.median(r['prompt_ms'] for r in rs):>9.0f}"
              f"{statistics.median(r['predicted_n'] or 0 for r in rs):>8.0f}{statistics.median(per):>10.0f}"
              f"{same:>6}/{len(rs)}")
    diffs = [(r["heard"], base[(r["heard"], r["rep"])], r["text"]) for m in ("grammar",) for r in out[m]
             if r["rep"] == 0 and base[(r["heard"], r["rep"])].strip() != r["text"].strip()]
    for heard, a, b in diffs:
        print(f"\n{heard}\n  deployed: {a}\n  grammar:  {b}")
    Path(HERE / f"grammar_probe_{time.strftime('%Y-%m-%d')}.jsonl").write_text(
        "\n".join(json.dumps({"mode": m, **r}) for m, rs in out.items() for r in rs) + "\n")


if __name__ == "__main__":
    main()
