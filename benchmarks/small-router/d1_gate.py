"""d1-2B as the act-or-ask gate: one yes/no question per proposed call, P(yes) = "this call does
what the person said". Scored through override.py's own pick/table, with 1 - P(yes) standing in
for override mass, so thresholds come from val only and the said-words gate still runs after.

    uv run --no-project --with 'transformers>=5.19' --with torch --with torchvision --with pillow \
        --with numpy --with jinja2 python d1_gate.py MODEL_DIR val.json test.json OUT_DIR

val.json and test.json come from `override.py run MODEL --observe` (the model-alone rows).
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch

model_dir, val_p, test_p, out = sys.argv[1:5]
sys.path.insert(0, str(Path(__file__).parent))
import caller  # noqa: E402
import override  # noqa: E402
from transformers import AutoModel  # noqa: E402

QUESTION = {"ok": {"type": "noul", "instructions":
    "Will the device's action do exactly what the person asked, with the right thing, item, "
    "amount and setting? Answer no if it does something else, picks the wrong item or value, "
    "misses part of the request, or the person did not ask the device to do anything."}}


def state(r):
    try:
        will = override.say(r["calls"])
    except Exception:  # noqa: BLE001  a call the templates can't voice: show it raw
        will = json.dumps(r["calls"])
    return {"person said": r["text"], "device will": will}


def score_rows(model, results):
    idx = [k for k, r in enumerate(results) if r["calls"]]
    ps = {}
    for i in range(0, len(idx), 32):
        chunk = idx[i:i + 32]
        outs = model.system_one_batch([(state(results[k]), QUESTION) for k in chunk])
        for k, o in zip(chunk, outs):
            ps[k] = float(o["answers"]["ok"]["noul"])
    for k, r in enumerate(results):
        r["p_ok"] = ps.get(k)
        r["mass"], r["gap"] = (1 - ps[k] if k in ps else 0.0), 99.0
    return results


def reliability(results, rows):
    p, y = [], []
    for r in results:
        if r["calls"]:
            p.append(r["p_ok"])
            y.append(caller.passes(rows[r["id"]], r["calls"], r["said"]))
    p, y = np.array(p), np.array(y, dtype=float)
    bins = [0, .1, .3, .5, .7, .9, .97, 1.0001]
    lines = [f"  calls {len(p)}, right {int(y.sum())}, Brier {np.mean((p - y) ** 2):.3f}"]
    for lo, hi in zip(bins, bins[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            lines.append(f"  P(yes) {lo:.2f}-{min(hi, 1):.2f}: n={int(m.sum()):3d}  mean P {p[m].mean():.2f}  actually right {y[m].mean():.2f}")
    # AUROC: how well P(yes) ranks right calls above wrong ones
    pos, neg = p[y == 1], p[y == 0]
    auc = np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg]) if len(pos) and len(neg) else float("nan")
    lines.append(f"  AUROC {auc:.3f}")
    return "\n".join(lines)


def main():
    model = AutoModel.from_pretrained(model_dir, trust_remote_code=True, dtype=torch.bfloat16).to("cuda")
    rows = {r["id"]: r for name in ("train.jsonl", "cases.jsonl") for r in caller.load_rows(name)}
    sets = {}
    for name, path in (("val", val_p), ("test", test_p)):
        res = score_rows(model, json.loads(Path(path).read_text()))
        Path(out, f"d1-{name}.json").write_text(json.dumps(res, indent=0))
        sets[name] = (res, override.prepare(res, rows))
        print(f"\n{name} reliability (calls only):\n{reliability(res, rows)}")
    t = override.pick(sets["val"][1])["mass only"]
    print(f"\nthreshold from val: ask when P(yes) <= {1 - t[0]:.3f}")
    for name in ("val", "test"):
        print(f"{name}: {override.table(sets[name][1], *t)}")
    print("\ntest sweep (diagnostic, not used to pick):")
    for pt in (.5, .7, .8, .9, .95, .98):
        print(f"  ask if P(yes) < {pt}: {override.table(sets['test'][1], 1 - pt, -float('inf'))}")


if __name__ == "__main__":
    main()
