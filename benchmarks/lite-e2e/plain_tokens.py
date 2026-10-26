"""Copy a FunctionGemma checkpoint with its tool-call tokens marked plain, for Ollama.

Ollama's raw output drops special tokens, and FunctionGemma writes its calls in them
(<start_function_call>, <escape>, ...), so the Pi saw "light_set{}" with the call and the
string fences gone. Marking those seven tokens non-special changes only how they print: the
ids, the weights and the template are untouched. Then convert and import:

    python plain_tokens.py ~/models/functiongemma-hestia-v4 /tmp/v4-plain
    python ~/llama.cpp/convert_hf_to_gguf.py /tmp/v4-plain --outtype q8_0 --outfile v4-q8_0.gguf
    # on the Pi: Modelfile = FROM ./v4-q8_0.gguf / TEMPLATE {{ .Prompt }}; ollama create ...
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PLAIN = ("function", "escape")  # every <..._function_...> token and <escape>


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("training_args.bin"))
    tok = json.loads((dst / "tokenizer.json").read_text())
    n = 0
    for a in tok["added_tokens"]:
        if any(k in a["content"] for k in PLAIN):
            a["special"], n = False, n + 1
    (dst / "tokenizer.json").write_text(json.dumps(tok, ensure_ascii=False))
    cfg = json.loads((dst / "tokenizer_config.json").read_text())
    for v in cfg.get("added_tokens_decoder", {}).values():
        if any(k in v["content"] for k in PLAIN):
            v["special"] = False
    (dst / "tokenizer_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1))
    print(f"{n} tokens marked plain -> {dst}")


if __name__ == "__main__":
    main()
