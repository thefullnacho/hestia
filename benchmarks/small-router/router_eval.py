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


def embed(url, model, texts):
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
    return tools, cases


def report(name, cases, preds):
    """preds: list of (domain, score). Picks the threshold that best separates none."""
    scored = [(c, p) for c, p in zip(cases, preds) if c["gold"] and not c.get("context")]
    best = None
    for t in sorted({s for _, (_, s) in scored} | {0.0}):
        ok = sum((p if s >= t else "none") == c["gold"] for c, (p, s) in scored)
        if not best or ok > best[1]:
            best = (t, ok)
    t, _ = best
    by = defaultdict(Counter)
    misses = []
    for c, (p, s) in scored:
        got = p if s >= t else "none"
        by[c["gold"]]["n"] += 1
        by[c["gold"]]["ok"] += got == c["gold"]
        if got != c["gold"]:
            misses.append(f"  {c['id']:8} {c['gold']:>9} -> {got:9} {s:.3f}  {c['text']}")
    domain_only = [(c, p) for c, p in scored if c["gold"] != "none"]
    top1 = sum(p == c["gold"] for c, (p, _) in domain_only)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--model", default="embeddinggemma")
    ap.add_argument("--no-prefix", action="store_true", help="skip EmbeddingGemma's task prefixes")
    a = ap.parse_args()
    q, d = ("", "") if a.no_prefix else (QUERY, DOC)

    tools, cases = load()
    vecs = embed(a.ollama, a.model, [q + c["text"] for c in cases])

    names = list(tools)
    docs = [d + " ".join(t["description"] for t in tools[n]) for n in names]
    dvecs = embed(a.ollama, a.model, docs)
    preds = []
    for v in vecs:
        sims = [cos(v, dv) for dv in dvecs]
        i = max(range(len(names)), key=sims.__getitem__)
        preds.append((names[i], sims[i]))
    report("describe", cases, preds)

    labelled = [i for i, c in enumerate(cases) if c["gold"] not in (None, "none")]
    preds = []
    for i, v in enumerate(vecs):
        j = max((j for j in labelled if j != i), key=lambda j: cos(v, vecs[j]))
        preds.append((cases[j]["gold"], cos(v, vecs[j])))
    report("loo-knn (ceiling, leaky)", cases, preds)


if __name__ == "__main__":
    main()
