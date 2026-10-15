"""Override gate: constrained decoding where "the program had to override the model" is a reason
to ask, not something to hide.

    uv run --with torch --with transformers --with jinja2 python override.py run MODEL --rows train.jsonl --split val --out val.json
    uv run --with torch --with transformers --with jinja2 python override.py run MODEL --out test.json
    python override.py score val.json test.json

Two signals, recorded at every step of constrained decoding (constrain.py), ask if either fires:

- override mass: the probability the model put on tokens the mask threw away (its top 50). Small
  means it was already going somewhere legal; large means it was confidently headed somewhere
  forbidden. Disagreement with the program.
- gap: at decision points (call or talk, the tool name, an enum value, the first word of a span),
  the log-probability margin between the best allowed token and the best allowed token that says
  something different (not a longer or shorter spelling of the same piece). Uncertainty within
  the allowed set.

A row's signals are its largest override and its smallest gap. Thresholds are picked on the val
split only, minimising wrong actions plus needless questions (each counts one). The said-words
gate (caller.spans_said) still runs after: defense in depth.

The question is built from the veto, never "I'm confused": what the model wanted against what
the program allowed. `run` finishes both branches at the deciding step (the forbidden one left to
the model, the runner-up one still constrained) so `score` can say both.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter

import caller

TOP = 50


def decision(at, params) -> bool:
    """Does the next token choose something: call or talk, which tool, which value?"""
    kind = at[0]
    if kind in ("open", "tool"):
        return True
    if kind != "value":
        return False
    spec = params[at[1]][at[2]]
    if spec.get("type") == "integer":
        return False
    if len(at) == 3:
        return True  # the value's first token (may carry the opening escape and a word)
    return at[3] == "enum" or not at[4].strip()


def run(a):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, LogitsProcessor, LogitsProcessorList

    import constrain

    class Recorder(LogitsProcessor):
        """Constrain (or only watch, with enforce=False) and log both signals per step."""

        def __init__(self, tok, checkers, prompt_len, stops, enforce=True):
            self.tok, self.checkers, self.prompt_len = tok, checkers, prompt_len
            self.stops, self.enforce = set(stops), enforce
            self.steps = [[] for _ in checkers]

        def __call__(self, input_ids, scores):
            logp = scores.float().log_softmax(-1)
            for row, ck in enumerate(self.checkers):
                gen = [t for t in input_ids[row, self.prompt_len:].tolist() if t != self.tok.pad_token_id]
                if gen and gen[-1] in self.stops:
                    continue
                prefix = self.tok.decode(gen, skip_special_tokens=False)
                valid = ck.ok(prefix)
                at = ck.at
                lp, top = torch.topk(logp[row], TOP)
                lp, top = lp.tolist(), top.tolist()
                pieces = [self.tok.decode(gen + [t], skip_special_tokens=False)[len(prefix):] for t in top]
                allowed = [ck.ok(prefix + p) for p in pieces]
                mass = sum(math.exp(l) for l, ok in zip(lp, allowed) if not ok)
                pick = next((j for j, ok in enumerate(allowed) if ok), None)
                step = {"n": len(gen), "at": list(at) if valid else None, "mass": mass,
                        "want": top[0], "want_ok": allowed[0],
                        "pick": top[pick] if pick is not None else None}
                if valid and pick is not None and decision(at, ck.params):
                    norm = [p.replace(constrain.ESC, "").strip().lower() for p in pieces]
                    alt = next((j for j in range(pick + 1, TOP) if allowed[j] and not (
                        norm[j].startswith(norm[pick]) or norm[pick].startswith(norm[j]))), None)
                    step["gap"] = lp[pick] - lp[alt] if alt is not None else 99.0
                    step["alt"] = top[alt] if alt is not None else None
                self.steps[row].append(step)
                if self.enforce and pick is not None:
                    keep = scores[row, top[pick]].clone()
                    scores[row].fill_(float("-inf"))
                    scores[row, top[pick]] = keep
            return scores

    tok = AutoTokenizer.from_pretrained(a.model)
    tok.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16).to("cuda").eval()
    stops = [tok.convert_tokens_to_ids(t) for t in ("<end_of_turn>", "<start_function_response>", "<eos>")]
    rows = [r for r in caller.load_rows(a.rows) if not a.split or r.get("split") == a.split]
    rows = [r for r in rows if caller.domain_of(r)]
    prompts = [tok.apply_chat_template(caller.messages(r), tools=caller.DOMAINS[caller.domain_of(r)],
                                       add_generation_prompt=True, tokenize=False) for r in rows]
    pids = [tok(p, add_special_tokens=False)["input_ids"] for p in prompts]

    def generate(seqs, checkers, enforce, max_new=96):
        """Greedy from token-id prefixes; returns new ids per row and the step logs."""
        out_ids, logs = [], []
        for i in range(0, len(seqs), a.batch):
            chunk = seqs[i:i + a.batch]
            width = max(map(len, chunk))
            ids = torch.tensor([[tok.pad_token_id] * (width - len(s)) + s for s in chunk], device="cuda")
            mask = torch.tensor([[0] * (width - len(s)) + [1] * len(s) for s in chunk], device="cuda")
            rec = Recorder(tok, checkers[i:i + a.batch], width, stops, enforce)
            with torch.no_grad():
                gen = model.generate(input_ids=ids, attention_mask=mask, max_new_tokens=max_new,
                                     do_sample=False, eos_token_id=stops, pad_token_id=tok.pad_token_id,
                                     logits_processor=LogitsProcessorList([rec]))
            for row in gen[:, width:].tolist():
                out_ids.append([t for t in row if t != tok.pad_token_id])
            logs += rec.steps
        return out_ids, logs

    def checker(r):
        return constrain.CallChecker(caller.DOMAINS[caller.domain_of(r)], r["text"])

    outs, logs = generate(pids, [checker(r) for r in rows], not a.observe)

    # the deciding steps, and both branches at each: what the model wanted vs what was allowed
    branch_jobs = []  # (row index, which, seq, enforce)
    sig = []
    for k, (r, ids, steps) in enumerate(zip(rows, outs, logs)):
        m = max(steps, key=lambda s: s["mass"], default=None)
        g = min((s for s in steps if "gap" in s), key=lambda s: s["gap"], default=None)
        sig.append((m, g))
        if m is not None and not m["want_ok"]:
            branch_jobs.append((k, "wanted", pids[k] + ids[:m["n"]] + [m["want"]], False))
        if g is not None and g.get("alt") is not None:
            branch_jobs.append((k, "runner_up", pids[k] + ids[:g["n"]] + [g["alt"]], True))
    branches = {}
    for which, enforce in (("runner_up", True), ("wanted", False)):  # the wanted one is the model's own
        jobs = [j for j in branch_jobs if j[1] == which]
        if not jobs:
            continue
        tails, _ = generate([j[2] for j in jobs], [checker(rows[j[0]]) for j in jobs], enforce, max_new=48)
        for (k, _, seq, _), tail in zip(jobs, tails):
            n = sig[k][0 if which == "wanted" else 1]["n"]
            text = tok.decode(outs[k][:n] + [seq[-1]] + tail, skip_special_tokens=False)
            branches.setdefault(k, {})[which] = text.replace("<pad>", "")

    results = []
    for k, (r, ids) in enumerate(zip(rows, outs)):
        text = tok.decode(ids, skip_special_tokens=False).replace("<pad>", "")
        calls, said = caller.parse(text)
        m, g = sig[k]
        results.append({
            "id": r["id"], "text": r["text"], "expect": r["expect"], "out": text,
            "calls": calls, "said": said, "ok": caller.passes(r, calls, said),
            "mass": m["mass"] if m else 0.0, "mass_at": m["at"] if m else None,
            "mass_want": tok.decode([m["want"]]) if m else None,
            "gap": g["gap"] if g else 99.0, "gap_at": g["at"] if g else None,
            "gap_alt": tok.decode([g["alt"]]) if g and g.get("alt") is not None else None,
            "fallbacks": sum(s["pick"] is None for s in logs[k]),
            **{w: caller.parse(t)[0] for w, t in branches.get(k, {}).items()},
            **{w + "_text": t for w, t in branches.get(k, {}).items()},
        })
    with open(a.out, "w") as f:
        json.dump(results, f, indent=0)
    print(f"{len(results)} rows -> {a.out}")


# --- scoring -------------------------------------------------------------------------------

def prepare(results: list[dict], rows: dict):
    """Per-row facts the thresholds don't change, as arrays, so the grid is cheap."""
    import numpy as np
    f = {"mass": [], "gap": [], "call": [], "right": [], "said": [], "ok_act": [], "ok_ask": []}
    for r in results:
        row = rows[r["id"]]
        f["mass"].append(r["mass"])
        f["gap"].append(r["gap"])
        f["call"].append(bool(r["calls"]))
        f["right"].append(bool(r["calls"]) and caller.passes(row, r["calls"], r["said"]))
        f["said"].append(not r["calls"] or caller.spans_said(row["text"], r["calls"]))
        f["ok_act"].append(caller.passes(row, r["calls"], r["said"]))
        f["ok_ask"].append(caller.passes(row, [], r["said"]))
    return {k: np.array(v) for k, v in f.items()}


def table(f, mass_t, gap_t) -> dict:
    over = f["call"] & ((f["mass"] >= mass_t) | (f["gap"] <= gap_t))
    said = f["call"] & ~over & ~f["said"]
    act = f["call"] & ~over & ~said
    n = len(f["call"])
    wrong = f["call"] & ~f["right"]
    asks = int((over | said).sum())
    ok = (act & f["ok_act"]) | (~act & f["ok_ask"])
    return {"right": ok.sum() / n, "wrong calls": int(wrong.sum()),
            "caught override": int((over & wrong).sum()), "caught said": int((said & wrong).sum()),
            "execute": int((act & wrong).sum()), "needless asks": int(((over | said) & f["right"]).sum()),
            "asks": asks, "ask rate": asks / n}


def pick(f) -> dict:
    """Thresholds that minimise wrong actions plus needless asks on these rows; fewer asks on a tie."""
    ms = sorted(set(f["mass"][f["call"]].tolist()) | {math.inf})
    gs = sorted(set(f["gap"][f["call"]].tolist()) | {-math.inf})
    grid = {"mass only": [(m, -math.inf) for m in ms], "gap only": [(math.inf, g) for g in gs],
            "both": [(m, g) for m in ms for g in gs]}
    best = {}
    for name, cands in grid.items():
        def cost(t):
            s = table(f, *t)
            return (s["execute"] + s["needless asks"], s["asks"])
        best[name] = min(cands, key=cost)
    return best


def _light(a: dict) -> str:
    t = a.get("target")
    return "everything" if t == "all" else f"the {t} light" if t else "the lights here"


TEMPLATES = {
    "music_play": lambda a: (f"play the {a['kind']} {a.get('query') or ''}" if a.get("kind")
                             else f"play {a.get('query') or 'some music'}"),
    "music_control": lambda a: {"set_volume": f"set the volume to {a.get('level')}" if a.get("level") else "mute it",
                                "skip": "skip this song", "previous": "go back a song", "louder": "turn it up",
                                "quieter": "turn it down"}.get(a.get("action"), f"{a.get('action')} the music"),
    "light_set": lambda a: (f"turn {_light(a)} {a['state']}" if a.get("state")
                            else f"set {_light(a)} to {a.get('color') or str(a.get('brightness')) + ' percent'}"),
    "scene_activate": lambda a: f"start the {a.get('name')} scene",
    "inventory_adjust": lambda a: (f"log {abs(a.get('change') or 0)} {a.get('item')} "
                                   f"{'used' if (a.get('change') or 0) < 0 else 'added'}"),
    "inventory_mark": lambda a: f"mark {a.get('item')} {a.get('level')}",
    "inventory_set": lambda a: f"set {a.get('item')} to {a.get('count')}",
    "inventory_find": lambda a: f"look up the {a.get('item')}",
    "tool_status": lambda a: f"mark the {a.get('tool')} {a.get('status')}",
    "list_add": lambda a: f"add {', '.join(a.get('items') or [])} to the {a.get('list')} list",
    "list_remove": lambda a: f"cross {', '.join(a.get('items') or [])} off the {a.get('list')} list",
    "list_read": lambda a: f"read the {a.get('list')} list",
    "calendar_read": lambda a: f"check the calendar for {a.get('when')}",
    "calendar_add": lambda a: f"put {a.get('title')} on the calendar for {a.get('when')}",
    "weather_get": lambda a: f"check the weather for {a.get('when')}",
    "reminder_add": lambda a: f"remind you to {a.get('text')} {a.get('when')}",
    "alerts_read": lambda a: "read your reminders",
    "alert_act": lambda a: f"{a.get('action')} the alert",
}


NOUN = {"when": "time", "item": "item", "target": "light", "query": "music", "title": "event",
        "text": "reminder", "list": "list", "tool": "tool", "name": "scene", "location": "place",
        "color": "colour", "items": "items", "action": "action", "level": "level", "status": "status",
        "kind": "kind", "aspect": "kind of weather"}


VOCAB = {w for t, ps in caller.PARAMS.items() for w in t.split("_") + [
    v for p in ps.values() for v in p.get("enum", [])]}


def resolve(calls: list[dict], tools: set[str]) -> list[dict]:
    """A value the schema forbids that names a sibling tool ("find" under tool_status) is read as
    that tool, with the same subject: the model's wanted action, in a form the device can do."""
    out = []
    for c in calls:
        props = caller.PARAMS.get(c["tool"], {})
        for k, v in c["args"].items():
            spec = props.get(k, {})
            if "enum" in spec and str(v) not in spec["enum"]:
                sib = next((t for t in sorted(tools) if str(v) in t.split("_")), None)
                if sib:
                    subject = next((x for x in c["args"].values() if isinstance(x, str) and x != v), "")
                    c = {"tool": sib, "args": {next(iter(caller.PARAMS[sib])): subject}}
                break
        out.append(c)
    return out


def say(calls: list[dict]) -> str:
    parts = [" ".join(TEMPLATES[c["tool"]](c["args"]).split()) for c in calls]
    return " and ".join(parts) if parts else "leave it"


def question(r: dict, row: dict, mass_t: float, gap_t: float) -> str:
    """Built from the veto: what the model wanted against what the program allowed. Only options
    the device could act on (they pass the said-words gate) are offered."""
    tools = {t["function"]["name"] for t in caller.DOMAINS[caller.domain_of(row)]}

    def option(calls):
        if calls is None:
            return None
        calls = resolve(calls, tools)
        return say(calls) if not calls or caller.spans_said(row["text"], calls) else None

    which = "wanted" if r["mass"] >= mass_t else "runner_up"
    other, word = r.get(which), (r.get("mass_want" if which == "wanted" else "gap_alt") or "").strip()
    for w in ("wanted", "runner_up"):  # a branch where the model asked a question itself: use it
        if r.get(w) == [] and caller.parse(r.get(w + "_text", ""))[1].endswith("?"):
            return caller.parse(r[w + "_text"])[1]
    opts = [o for o in (option(r["calls"]), option(other)) if o]
    opts = list(dict.fromkeys(opts))
    if len(opts) == 2:
        return f"Should I {opts[0]}, or {opts[1]}?"
    if opts:
        if word.startswith("<"):  # a control token: the model wanted to stop, or say nothing
            return f"Should I {opts[0]}, or leave it?" if opts[0] != "leave it" else "Should I leave it?"
        if word.lower() in VOCAB | set(caller.words(row["text"])) and word.lower() not in opts[0].lower():
            return f"Should I {opts[0]}, or did you mean {word}?"
        return f"Should I {opts[0]}?"
    # nothing the device could do: ask for the value that failed, by name
    for c in r["calls"]:
        props = caller.PARAMS.get(c["tool"], {})
        for k, v in c["args"].items():
            if k in props and not caller.spans_said(row["text"], [{"tool": c["tool"], "args": {k: v}}]):
                return f"What {NOUN.get(k, k)} do you mean?"
    return "What do you mean?"


def score(a):
    vals, tests = json.load(open(a.val)), json.load(open(a.test))
    rows = {r["id"]: r for r in caller.load_rows("train.jsonl") + caller.load_rows("cases.jsonl")}
    fv, ft = prepare(vals, rows), prepare(tests, rows)
    best = pick(fv)
    cols = ["right", "wrong calls", "caught override", "caught said", "execute", "needless asks", "asks", "ask rate"]

    def line(name, f, t):
        s = table(f, *t)
        cells = [f"{s[c]:.0%}" if c in ("right", "ask rate") else str(s[c]) for c in cols]
        return f"| {name} | " + " | ".join(cells) + " |"

    for label, f in (("val", fv), ("test", ft)):
        print(f"\n{label}: {len(f['call'])} rows")
        print("| Gate | " + " | ".join(cols) + " |")
        print("|---" * (len(cols) + 1) + "|")
        print(line("said-words only", f, (math.inf, -math.inf)))
        for name, t in best.items():
            print(line(f"{name} (mass >= {t[0]:.3g}, gap <= {t[1]:.3g})", f, t))
    t = best["both"]
    print(f"\nasks from the override gate on test (mass >= {t[0]:.3g}, gap <= {t[1]:.3g}):")
    for r in tests:
        if r["calls"] and (r["mass"] >= t[0] or r["gap"] <= t[1]):
            row = rows[r["id"]]
            tag = "needless" if caller.passes(row, r["calls"], r["said"]) else "caught"
            print(f"  {tag:8} {r['text']!r}\n{'':11}-> {question(r, row, *t)}")
    if a.sweep:
        print("\ntest sweep (diagnostic only, never used to pick):")
        for key in ("mass", "gap"):
            vs = sorted({round(float(v), 2) for v in ft[key][ft["call"]]})
            for v in vs[::a.sweep]:
                s = table(ft, *((v, -math.inf) if key == "mass" else (math.inf, v)))
                print(f"  {key} {v:6.2f}: execute {s['execute']:3} needless {s['needless asks']:3} asks {s['asks']:3}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("model")
    r.add_argument("--rows", default="cases.jsonl")
    r.add_argument("--split")
    r.add_argument("--batch", type=int, default=32)
    r.add_argument("--observe", action="store_true", help="record the signals without constraining")
    r.add_argument("--out", required=True)
    s = sub.add_parser("score")
    s.add_argument("val")
    s.add_argument("test")
    s.add_argument("--sweep", type=int, default=0, help="also print a coarse test sweep, every Nth value")
    a = ap.parse_args()
    run(a) if a.cmd == "run" else score(a)


if __name__ == "__main__":
    main()
