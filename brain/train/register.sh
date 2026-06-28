#!/usr/bin/env bash
# Register the trained LoRA adapter with ollama so the harness can eval it by name.
# Converts the PEFT adapter to a GGUF LoRA, then `ollama create`s a model that is just
# qwen3:4b + the adapter (no merge, no re-quantize). See README.md.
#
#   ./register.sh [ADAPTER_DIR] [MODEL_NAME]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_DIR="${1:-$HERE/out/qwen3-4b-toolfire/adapter}"
MODEL_NAME="${2:-qwen3-4b-toolfire}"
LLAMA="$HOME/llama.cpp"  # full source tree (the odysseus vendor dir is built libs only)
BASE_HF="Qwen/Qwen3-4B"
GGUF_OUT="$HERE/out/toolfire-lora.gguf"

[ -d "$ADAPTER_DIR" ] || { echo "no adapter at $ADAPTER_DIR — run train_lora.py first" >&2; exit 1; }

echo "1/3 converting PEFT adapter -> GGUF"
python "$LLAMA/convert_lora_to_gguf.py" "$ADAPTER_DIR" \
    --base-model-id "$BASE_HF" --outfile "$GGUF_OUT"

echo "2/3 writing Modelfile"
MODELFILE="$HERE/out/Modelfile"
sed "s#__ADAPTER_GGUF__#$GGUF_OUT#" "$HERE/Modelfile.tmpl" > "$MODELFILE"

echo "3/3 ollama create $MODEL_NAME"
ollama create "$MODEL_NAME" -f "$MODELFILE"

echo
echo "done. re-eval with:"
echo "  cd $HOME/hestia"
echo "  EVAL_REPEATS=5 uv run --project brain python brain/eval_keymatch.py $MODEL_NAME qwen3:8b"
echo "  set -a; . secrets/ha.env; set +a"
echo "  EVAL_REPEATS=10 uv run --project brain python brain/eval_models.py ${MODEL_NAME}:nothink"
