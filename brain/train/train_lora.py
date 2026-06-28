#!/usr/bin/env python3
"""QLoRA fine-tune to elicit tool-firing in a small model — see ../SFT_DATASET.md.

Trains a LoRA adapter on the templated tool-firing JSONL (sft_gen.py output) to close the
agency cliff (the 4B grounds but won't ACT). Assistant-only loss via the chat template's
generation mask, so we train on the tool_call / final tokens — never the system, user, or
tool-result tokens.

Runs on the FREE 4060 Ti (the 5080 keeps serving the resident brain). Pin with:
    CUDA_VISIBLE_DEVICES=1 CUDA_DEVICE_ORDER=PCI_BUS_ID uv run python train_lora.py ...

Pipeline (see README.md): sft_gen.py -> train_lora.py -> register.sh -> re-eval harness.

Deps live in this dir's OWN venv (requirements.txt) — never the brain runtime venv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE.parent / "sft_data" / "toolfire.jsonl"))
    ap.add_argument("--base", default="Qwen/Qwen3-4B", help="HF base (matches ollama qwen3:4b)")
    ap.add_argument("--out", default=str(HERE / "out" / "qwen3-4b-toolfire"))
    ap.add_argument("--epochs", type=float, default=4.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--r", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--dropout", type=float, default=0.05)
    # examples run ~3000 tokens (the full 8-tool schema block dominates); max observed 3051.
    ap.add_argument("--max-len", type=int, default=3200)
    ap.add_argument("--bsz", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=8)
    # Single-turn data only: compute the LM-head + loss on just the last K positions (the
    # assistant span sits at the end). Kills the ~3GB logits OOM and ~10x's the LM-head compute.
    # MUST be 0 for multi-step data (an assistant turn in the middle would be missed).
    ap.add_argument("--logits-keep", type=int, default=0)
    ap.add_argument("--merge", action="store_true", help="also save a merged bf16 model")
    ap.add_argument("--check", action="store_true",
                    help="validate data + chat-template masking on 3 examples, then exit (no train)")
    return ap.parse_args()


def build_encoder(tok, max_len: int):
    """Return fn(example) -> {input_ids, labels, attention_mask} with assistant-only labels.

    Qwen3's chat template has no `{% generation %}` markers, so we mask manually: render the
    ChatML string, then train only the `<|im_start|>assistant ... <|im_end|>` spans (located via
    offset mapping). Correct across the multi-step examples' two assistant turns, and we KEEP the
    closing <|im_end|> in the span so the model learns to STOP (the loop-termination fix)."""
    tok.truncation_side = "left"  # if anything overruns max_len, drop the FRONT, never the tail
    END = "<|im_end|>"

    def encode(ex: dict) -> dict:
        text = tok.apply_chat_template(ex["messages"], tools=ex["tools"], tokenize=False)
        enc = tok(text, return_offsets_mapping=True, add_special_tokens=False,
                  max_length=max_len, truncation=True)
        ids, offs = enc["input_ids"], enc["offset_mapping"]
        spans, i = [], 0
        while True:
            a = text.find("<|im_start|>assistant", i)
            if a == -1:
                break
            nl = text.find("\n", a)
            start = nl + 1 if nl != -1 else a
            e = text.find(END, start)
            end = (e + len(END)) if e != -1 else len(text)
            spans.append((start, end))
            i = end
        if not spans:
            raise SystemExit("no assistant span in rendered ChatML — check the chat template.")
        labels = [-100] * len(ids)
        for j, (s, e) in enumerate(offs):
            if s != e and any(s >= ss and e <= ee for ss, ee in spans):
                labels[j] = ids[j]
        if not any(x != -100 for x in labels):
            raise SystemExit("masking produced no trainable tokens — offset/marker mismatch.")
        return {"input_ids": ids, "labels": labels, "attention_mask": [1] * len(ids)}

    return encode


def main() -> None:
    args = parse_args()
    import torch
    import torch.nn.functional as F
    from datasets import load_dataset
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                              DataCollatorForSeq2Seq, Trainer, TrainingArguments)
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    class KeepTrainer(Trainer):
        """Loss on only the last K positions (single-turn: assistant span is at the end).
        logits[i] predicts token[i+1], so the kept logits[:-1] align to labels[-(K-1):]."""
        def __init__(self, *a, keep: int = 160, **k):
            super().__init__(*a, **k)
            self.keep = keep

        def compute_loss(self, model, inputs, return_outputs=False, **kw):
            labels = inputs.pop("labels")
            out = model(**inputs, logits_to_keep=self.keep)
            logits = out.logits[:, :-1, :]
            tgt = labels[:, -(logits.size(1)):].to(logits.device)
            loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)).float(),
                                   tgt.reshape(-1), ignore_index=-100)
            return (loss, out) if return_outputs else loss

    tok = AutoTokenizer.from_pretrained(args.base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    ds = load_dataset("json", data_files=args.data, split="train")
    encode = build_encoder(tok, args.max_len)

    if args.check:
        # Prove the data loads and the assistant-only masking works, without any training.
        n_tok = n_asst = 0
        for ex in ds.select(range(min(3, len(ds)))):
            out = encode(ex)
            kept = sum(1 for x in out["labels"] if x != -100)
            n_tok += len(out["input_ids"]); n_asst += kept
            print(f"  {ex['meta']['stratum']:<14} tokens={len(out['input_ids']):>4}  "
                  f"assistant(trained)={kept:>3}  user={ex['messages'][1]['content'][:48]!r}")
        print(f"\nOK — {len(ds)} examples load; assistant-only masking active "
              f"({n_asst}/{n_tok} tokens carry loss in the sample).")
        return

    ds = ds.map(encode, remove_columns=ds.column_names, desc="encode+mask")

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.base, quantization_config=bnb, torch_dtype=torch.bfloat16, device_map="auto")
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=args.r, lora_alpha=args.alpha, lora_dropout=args.dropout, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()

    targs = TrainingArguments(
        output_dir=args.out, num_train_epochs=args.epochs, learning_rate=args.lr,
        per_device_train_batch_size=args.bsz, gradient_accumulation_steps=args.grad_accum,
        lr_scheduler_type="cosine", warmup_ratio=0.03, bf16=True, logging_steps=10,
        save_strategy="epoch", report_to="none", gradient_checkpointing=True,
        optim="paged_adamw_8bit")
    collator = DataCollatorForSeq2Seq(tok, label_pad_token_id=-100, padding=True)
    if args.logits_keep > 0:
        trainer = KeepTrainer(model=model, args=targs, train_dataset=ds,
                              data_collator=collator, keep=args.logits_keep)
    else:
        trainer = Trainer(model=model, args=targs, train_dataset=ds, data_collator=collator)
    trainer.train()

    adapter = Path(args.out) / "adapter"
    model.save_pretrained(adapter); tok.save_pretrained(adapter)
    print(f"\nadapter -> {adapter}")
    if args.merge:
        merged = model.merge_and_unload()
        merged.save_pretrained(Path(args.out) / "merged"); tok.save_pretrained(Path(args.out) / "merged")
        print(f"merged  -> {Path(args.out) / 'merged'}")


if __name__ == "__main__":
    main()
