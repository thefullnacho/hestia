"""Score an embedding model as the domain router in front of the tool caller.

    uv run --no-project python router_eval.py --ollama http://localhost:11435

Gold domain for a case comes from its expected call's tool, so multi cases route
like any other. `none` cases should fall under the confidence threshold. Ambiguous
cases and cases with alert context are reported apart: the first have no referent in
the words, and the second are device state that code already knows.

Methods:
  describe  each domain is its tool descriptions, embedded once (zero-shot, honest)
  loo-knn   nearest other case, leave-one-out. Paraphrases leak between cases, so
            treat this as a ceiling for what an exemplar file could reach.
"""
import argparse
import json
import math
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
QUERY = "task: classification | query: "
DOC = "title: none | text: "


CACHE = Path.home() / ".cache" / "small-router-embeddings.json"


def embed(url, model, texts):
    """Embeddings, cached by model and exact text so reruns on the Pi are cheap."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [t for t in dict.fromkeys(texts) if f"{model}\t{t}" not in cache]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(todo), 64):  # small requests, saved as they land
        chunk = todo[i:i + 64]
        for t, v in zip(chunk, _embed(url, model, chunk)):
            cache[f"{model}\t{t}"] = v
        CACHE.write_text(json.dumps(cache))
    return [cache[f"{model}\t{t}"] for t in texts]


def _embed(url, model, texts):
    req = urllib.request.Request(
        f"{url}/api/embed",
        data=json.dumps({"model": model, "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["embeddings"]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.hypot(*a) * math.hypot(*b))


def load():
    tools = json.loads((HERE / "tools.json").read_text())["domains"]
    tool_domain = {t["name"]: d for d, ts in tools.items() for t in ts}
    cases = [json.loads(l) for l in (HERE / "cases.jsonl").read_text().splitlines() if l.strip()]
    for c in cases:
        if c["expect"] == "call":
            c["gold"] = tool_domain[c["calls"][0]["tool"]]
        elif c["expect"] == "none":
            c["gold"] = "none"
        else:
            c["gold"] = None  # ask: no router answer is right on the words alone
        # domains that also pass: the gold one plus any the case's `accept` list allows
        c["ok"] = {c["gold"]} | {
            "none" if a == "none" else tool_domain[a["tool"]]
            for alt in c.get("accept", []) for a in alt if a != "ask"}
    return tools, cases, tool_domain


def report(name, cases, preds):
    """preds: list of (domain, score). Picks the threshold that best separates none."""
    scored = [(c, p) for c, p in zip(cases, preds) if c["gold"] and not c.get("context")]
    best = None
    for t in sorted({s for _, (_, s) in scored} | {0.0}):
        ok = sum((p if s >= t else "none") in c["ok"] for c, (p, s) in scored)
        if not best or ok > best[1]:
            best = (t, ok)
    t, _ = best
    by = defaultdict(Counter)
    misses = []
    for c, (p, s) in scored:
        got = p if s >= t else "none"
        by[c["gold"]]["n"] += 1
        by[c["gold"]]["ok"] += got in c["ok"]
        if got not in c["ok"]:
            misses.append(f"  {c['id']:8} {c['gold']:>9} -> {got:9} {s:.3f}  {c['text']}")
    domain_only = [(c, p) for c, p in scored if c["gold"] != "none"]
    top1 = sum(p in c["ok"] for c, (p, _) in domain_only)
    total = sum(v["ok"] for v in by.values())
    print(f"\n== {name}")
    print(f"top-1 domain, threshold off: {top1}/{len(domain_only)} = {top1 / len(domain_only):.0%}")
    print(f"with none threshold {t:.3f} (tuned on these cases): {total}/{len(scored)} = {total / len(scored):.0%}")
    for d, v in sorted(by.items()):
        print(f"  {d:10} {v['ok']:3}/{v['n']:<3}")
    print("misses:")
    print("\n".join(misses))
    amb = [(c, p) for c, p in zip(cases, preds) if c["gold"] is None]
    print(f"ambiguous (reported, not scored), under threshold {sum(s < t for _, (_, s) in amb)}/{len(amb)}:")
    for c, (p, s) in amb:
        print(f"  {c['id']:8} -> {p:9} {s:.3f}  {c['text']}")


def gate(cases, ranked, floors, gaps):
    """Score the act / ask-which-of-two / drop decision.

    ranked: per case, [(sim, domain), ...] best first. Under the floor the device drops it
    (nothing to do); a top-two gap under `gap` asks which of the two; otherwise it acts.
    """
    print("\n== gate: drop under floor, ask top-two when the gap is small, else act")
    print("requests: act right / ask with the right one offered / act WRONG / ask without it / dropped")
    print("none: dropped / asked / acted     ambiguous: asked or dropped / acted")
    for floor in floors:
        for g in gaps:
            req, non, amb = Counter(), Counter(), Counter()
            for c, r in zip(cases, ranked):
                if c.get("context"):
                    continue
                (s1, d1), (s2, d2) = r[0], r[1]
                act = "drop" if s1 < floor else ("ask" if s1 - s2 < g else "act")
                if c["gold"] == "none":
                    non[act] += 1
                elif c["gold"] is None:
                    amb["act" if act == "act" else "ok"] += 1
                elif act == "drop":
                    req["drop"] += 1
                elif act == "act":
                    req["act_ok" if d1 in c["ok"] else "act_bad"] += 1
                else:
                    req["ask_ok" if c["ok"] & {d1, d2} else "ask_bad"] += 1
            n = sum(req.values())
            print(f"floor {floor:.2f} gap {g:.2f} | {req['act_ok']:3} {req['ask_ok']:3} "
                  f"{req['act_bad']:3} {req['ask_bad']:3} {req['drop']:3} of {n}"
                  f" (resolved {(req['act_ok'] + req['ask_ok']) / n:.0%}, asked {(req['ask_ok'] + req['ask_bad']) / n:.0%})"
                  f" | none {non['drop']:2} {non['ask']:2} {non['act']:2} | amb {amb['ok']:2} {amb['act']:2}")


def by_domain(vec, ex_vecs, ex_dom):
    """Rank domains by their single closest example: [(sim, domain), ...] best first."""
    best = {}
    for ev, d in zip(ex_vecs, ex_dom):
        s = cos(vec, ev)
        if s > best.get(d, -1):
            best[d] = s
    return sorted(((s, d) for d, s in best.items()), reverse=True)


def gate_class(cases, ranked, gap):
    """Gate where `none` is a domain with its own examples: top is none -> drop,
    top-two gap under `gap` -> ask which of the two, else act. Returns counts."""
    req, non, amb = Counter(), Counter(), Counter()
    for c, r in zip(cases, ranked):
        if c.get("context"):
            continue
        (s1, d1), (s2, d2) = r[0], r[1]
        act = "drop" if d1 == "none" and s1 - s2 >= gap else ("ask" if s1 - s2 < gap else "act")
        if c["gold"] == "none":
            non[act if not (act == "ask" and "none" in c["ok"] & {d1, d2}) else "ask"] += 1
        elif c["gold"] is None:
            amb["act" if act == "act" else "ok"] += 1
        elif act == "drop":
            req["act_ok" if "none" in c["ok"] else "drop"] += 1
        elif act == "act":
            req["act_ok" if d1 in c["ok"] else "act_bad"] += 1
        else:
            req["ask_ok" if c["ok"] & {d1, d2} else "ask_bad"] += 1
    return req, non, amb


def show(label, req, non, amb):
    n = sum(req.values())
    print(f"{label} | {req['act_ok']:4} {req['ask_ok']:4} {req['act_bad']:4} {req['ask_bad']:4} {req['drop']:4} of {n}"
          f" (resolved {(req['act_ok'] + req['ask_ok']) / n:.0%}, asked {(req['ask_ok'] + req['ask_bad']) / n:.0%},"
          f" wrong act {req['act_bad'] / n:.1%}) | none {non['drop']:3} {non['ask']:3} {non['act']:3} | amb {amb['ok']:3} {amb['act']:3}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--model", default="embeddinggemma")
    ap.add_argument("--no-prefix", action="store_true", help="skip EmbeddingGemma's task prefixes")
    a = ap.parse_args()
    q, d = ("", "") if a.no_prefix else (QUERY, DOC)

    tools, cases, tool_domain = load()
    vecs = embed(a.ollama, a.model, [q + c["text"] for c in cases])

    names = list(tools)
    docs = [d + " ".join(t["description"] for t in tools[n]) for n in names]
    dvecs = embed(a.ollama, a.model, docs)
    ranked = [sorted(((cos(v, dv), n) for dv, n in zip(dvecs, names)), reverse=True) for v in vecs]
    report("describe", cases, [(r[0][1], r[0][0]) for r in ranked])
    gate(cases, ranked, floors=(0.25, 0.28, 0.31), gaps=(0.0, 0.02, 0.04, 0.06, 0.08))

    labelled = [i for i, c in enumerate(cases) if c["gold"] not in (None, "none")]
    preds = []
    for i, v in enumerate(vecs):
        j = max((j for j in labelled if j != i), key=lambda j: cos(v, vecs[j]))
        preds.append((cases[j]["gold"], cos(v, vecs[j])))
    report("loo-knn (ceiling, leaky)", cases, preds)

    train_path = HERE / "train.jsonl"
    if not train_path.exists():
        return
    rows = [json.loads(l) for l in train_path.read_text().splitlines() if l.strip()]
    for r in rows:
        r["gold"] = tool_domain[r["calls"][0]["tool"]] if r["calls"] else (
            "none" if r["expect"] == "none" else r["domain"])
        r["ok"] = {r["gold"]}
    ex = [r for r in rows if r["split"] == "train"]
    val = [r for r in rows if r["split"] == "val"]
    ex_vecs = embed(a.ollama, a.model, [q + r["text"] for r in ex])
    val_vecs = embed(a.ollama, a.model, [q + r["text"] for r in val])
    ex_dom = [r["gold"] for r in ex]
    val_ranked = [by_domain(v, ex_vecs, ex_dom) for v in val_vecs]
    test_ranked = [by_domain(v, ex_vecs, ex_dom) for v in vecs]

    print(f"\n== examples: {len(ex)} training utterances as the router's examples, none is a domain")
    print("requests: act right / ask with the right one offered / act WRONG / ask without it / dropped")
    print("gap picked on the val split, then applied once to the test cases")
    best = None
    for g in (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08):
        req, non, amb = gate_class(val, val_ranked, g)
        show(f"val  gap {g:.2f}", req, non, amb)
        n = sum(req.values())
        # cheapest gap that keeps wrong actions at or under 2%
        if best is None and req["act_bad"] / n <= 0.02:
            best = g
    best = 0.04 if best is None else best
    for g in sorted({0.0, best}):
        show(f"TEST gap {g:.2f}", *gate_class(cases, test_ranked, g))
    report("examples, top-1 (no gate)", cases, [(r[0][1], 1.0) for r in test_ranked])


if __name__ == "__main__":
    main()
