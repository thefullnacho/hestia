"""Fine-tune FunctionGemma as the tool caller on train.jsonl.

    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 \\
      uv run --with torch --with transformers --with trl --with datasets --with jinja2 \\
      python finetune.py BASE_DIR OUT_DIR

Each row becomes one prompt (developer turn + the routed domain's tools + the utterance) and
one completion (the tool calls, or the clarifying question, or nothing). Loss is on the
completion only. Tools with few examples are repeated up to a floor so the model doesn't
learn the template mix as a prior. Only the train split is used; val is for checking.
"""
from __future__ import annotations

import argparse
import random
from collections import Counter

from datasets import Dataset
from transformers import AutoTokenizer
from trl import SFTConfig, SFTTrainer

import caller

FLOOR = 40  # examples per tool (or per ask/none) after repeating thin ones
CEIL = 200  # and at most this many, so the big Home Assistant intents don't set the prior


def build(tok, rows):
    data = []
    for r in rows:
        tools = caller.DOMAINS[caller.domain_of(r)]
        prompt = tok.apply_chat_template(caller.messages(r), tools=tools,
                                         add_generation_prompt=True, tokenize=False)
        full = tok.apply_chat_template(caller.messages(r, with_answer=True), tools=tools, tokenize=False)
        assert full.startswith(prompt), (prompt[-200:], full[-300:])
        data.append({"prompt": prompt, "completion": full[len(prompt):], "key":
                     r["calls"][0]["tool"] if r["calls"] else r["expect"]})
    return data


def balance(data, rng):
    counts = Counter(d["key"] for d in data)
    out = list(data)
    for key, n in counts.items():
        pool = [d for d in data if d["key"] == key]
        if n > CEIL:
            drop = set(map(id, rng.sample(pool, n - CEIL)))
            out = [d for d in out if id(d) not in drop]
        out += [rng.choice(pool) for _ in range(max(0, FLOOR - n))]
    rng.shuffle(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("--epochs", type=float, default=4)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--show", action="store_true", help="print two rendered examples and stop")
    ap.add_argument("--seed", type=int, default=1015)
    ap.add_argument("--train", default="train.jsonl", help="training rows (an older set, for comparisons)")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    tok = AutoTokenizer.from_pretrained(a.base)
    rows = [r for r in caller.load_rows(a.train) if r["split"] == "train" and caller.domain_of(r)]
    data = build(tok, rows)
    if a.show:
        for d in data[:2] + [d for d in data if d["key"] == "ask"][:1]:
            print(repr(d["prompt"][-160:]), "\n  ->", repr(d["completion"]))
        return
    data = balance(data, rng)
    print(f"{len(rows)} rows, {len(data)} after balancing:", dict(Counter(d['key'] for d in data)))

    cfg = SFTConfig(
        output_dir=a.out, num_train_epochs=a.epochs, learning_rate=a.lr,
        per_device_train_batch_size=1, gradient_accumulation_steps=16, gradient_checkpointing=True,
        lr_scheduler_type="cosine", warmup_ratio=0.05, weight_decay=0.0,
        bf16=True, logging_steps=10, save_strategy="no", report_to=[],
        max_length=1024, completion_only_loss=True, seed=a.seed,
    )
    trainer = SFTTrainer(model=a.base, args=cfg, processing_class=tok,
                         train_dataset=Dataset.from_list([{"prompt": d["prompt"], "completion": d["completion"]}
                                                          for d in data]))
    trainer.train()
    trainer.save_model(a.out)
    tok.save_pretrained(a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()
