# Tool-firing LoRA — training run

Closes the **agency cliff** from the size ladder (the 4B grounds but won't act). See
`../SFT_DATASET.md` for the dataset scope and `../eval_keymatch.py` / `../eval_models.py`
for the validation loop. The whole point: turn the 4B's under-calling (an elicitation gap,
not a capacity wall) into reliable tool-firing, and confirm it on the same harness that
found the problem.

## Hardware / isolation

- Train on the **4060 Ti** (CUDA index 1 under `PCI_BUS_ID` order); the **5080** keeps
  serving the resident brain. Pin every command with
  `CUDA_VISIBLE_DEVICES=1 CUDA_DEVICE_ORDER=PCI_BUS_ID`.
- Deps live in **this dir's own venv**, never the brain runtime venv (heavy CUDA stack).

## Pipeline

```bash
cd ~/hestia

# 0) env (isolated)
uv venv brain/train/.venv && . brain/train/.venv/bin/activate
uv pip install -r brain/train/requirements.txt

# 1) data (scale up; phase-2 expansion still TODO — see SFT_DATASET.md)
uv run --project brain python brain/sft_gen.py --per-intent 24 \
    --out brain/sft_data/toolfire.jsonl

# 2) sanity-check data + assistant-only masking WITHOUT training (fast, no GPU needed beyond load)
CUDA_VISIBLE_DEVICES=1 CUDA_DEVICE_ORDER=PCI_BUS_ID \
    python brain/train/train_lora.py --check

# 3) train the LoRA (4060 Ti)
CUDA_VISIBLE_DEVICES=1 CUDA_DEVICE_ORDER=PCI_BUS_ID \
    python brain/train/train_lora.py --epochs 3

# 4) register with ollama (adapter on top of qwen3:4b — no merge/re-quantize)
brain/train/register.sh

# 5) re-eval on the SAME harness that found the cliff
EVAL_REPEATS=5 uv run --project brain python brain/eval_keymatch.py \
    qwen3-4b-toolfire qwen3:4b qwen3:8b
set -a; . secrets/ha.env; set +a
EVAL_REPEATS=10 uv run --project brain python brain/eval_models.py \
    qwen3-4b-toolfire:nothink
```

## What success looks like

- **`eval_keymatch.py`**: the agency cases move off 0/5 — `home` "turn off the lights"
  fires, health-log fires, recall fires. Target: ≥ qwen3:8b's 100%.
- **`eval_models.py`**: stable correctness holds (no grounding regression), English 100%,
  and **latency drops** (training direct tool-calls strips the hidden-reasoning tax that
  made the raw 4B 9.6s).
- **Held-out generalization** (`toolfire.heldout.jsonl` = `home` open/close, never trained):
  if the garage open/close fires correctly, agency *generalized* beyond the trained tools —
  the answer to "does it inherit agency outside the dataset?". If it doesn't, fold open/close
  back into training. Either way, you get a real datapoint.

## Notes

- `train_lora.py --check` validates the data + the chat template's `{% generation %}`
  assistant-masking before you spend GPU time. If it errors that there's no assistant mask,
  the base's chat template lacks generation markers — switch to a response-template collator.
- QLoRA (4-bit nf4) keeps a 4B comfortably on 16 GB. r=16/alpha=32, lr=2e-4, cosine, 3
  epochs are sensible starts for a small disposition-shift; watch for over-firing (regression
  on the hard-negatives) and dial epochs down if it appears.
- Adapter approach means the experiment is cheap to throw away: `ollama rm qwen3-4b-toolfire`
  and you're back to baseline.
