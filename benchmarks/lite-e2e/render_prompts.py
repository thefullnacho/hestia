"""Render the caller's prompt for each domain with the official chat template, once, here.

The Pi has no jinja or transformers, and needs neither: a domain's prompt is fixed except for
the user's words, so each is stored as the text before and after them. Writes prompts.json.

    uv run --with transformers --with jinja2 python render_prompts.py ~/models/functiongemma-hestia-v4
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "small-router"))

import caller  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

MARK = "\x00USER\x00"


def main():
    tok = AutoTokenizer.from_pretrained(sys.argv[1])
    out = {}
    for domain, tools in caller.DOMAINS.items():
        row = {"id": "render", "text": MARK, "calls": [], "domain": domain}
        text = tok.apply_chat_template(caller.messages(row), tools=tools,
                                       add_generation_prompt=True, tokenize=False)
        before, after = text.split(MARK)
        out[domain] = [before, after]
    (HERE / "prompts.json").write_text(json.dumps(out, indent=1))
    print(f"{len(out)} domains -> prompts.json")


if __name__ == "__main__":
    main()
