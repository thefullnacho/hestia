#!/usr/bin/env bash
# Pull the benchmark GGUFs into ../models/ using the hf CLI.
# Idempotent: hf skips files already present. ~60 GB total for the full shortlist.
set -euo pipefail
cd "$(dirname "$0")"
MODELS_DIR="$(cd .. && pwd)/models"
mkdir -p "$MODELS_DIR"

# Pull just the one Q4_K_M file from each repo (not the whole repo).
pull() {  # repo  file
  echo ">>> $2"
  hf download "$1" "$2" --local-dir "$MODELS_DIR"
}

# Parsed from models.json. Smallest first so the bench can start early.
pull bartowski/Qwen2.5-7B-Instruct-GGUF            Qwen2.5-7B-Instruct-Q4_K_M.gguf
pull bartowski/Meta-Llama-3.1-8B-Instruct-GGUF     Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
pull bartowski/Qwen2.5-14B-Instruct-GGUF           Qwen2.5-14B-Instruct-Q4_K_M.gguf
pull bartowski/Qwen2.5-Coder-14B-Instruct-GGUF     Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf
pull bartowski/Mistral-Small-24B-Instruct-2501-GGUF Mistral-Small-24B-Instruct-2501-Q4_K_M.gguf
pull bartowski/Qwen2.5-32B-Instruct-GGUF           Qwen2.5-32B-Instruct-Q4_K_M.gguf

echo "done -> $MODELS_DIR"
