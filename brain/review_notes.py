"""Review the note-taker's proposals — the human "dispose" step.

The note-taker proposes durable facts into memory/inbox/. Nothing becomes part of the
brain's live memory until you promote it here, so the brain learns in the open and you
stay in control (determinism over intelligence).

Usage:
  uv run --project brain python brain/review_notes.py list
  uv run --project brain python brain/review_notes.py promote <id> [<id> ...]
  uv run --project brain python brain/review_notes.py promote --all
  uv run --project brain python brain/review_notes.py discard <id> [<id> ...] | --all
"""
from __future__ import annotations

import sys

import config
import memory_store

INBOX_DIR = config.INBOX_DIR


def _proposals() -> list[dict]:
    if not INBOX_DIR.exists():
        return []
    return [memory_store._parse(p) for p in sorted(INBOX_DIR.glob("*.md"))]


def _find(rid: str):
    p = INBOX_DIR / f"{rid}.md"
    return p if p.is_file() else None


def cmd_list() -> int:
    props = _proposals()
    if not props:
        print("No pending proposals. The brain hasn't drafted anything new to learn.")
        return 0
    print(f"{len(props)} pending proposal(s) in {INBOX_DIR}:\n")
    for r in props:
        m = r["meta"]
        print(f"  [{r['id']}]  ({m.get('type', '?')}, conf {m.get('confidence', '?')}, "
              f"{m.get('source', '?')})")
        print(f"      {r['body']}\n")
    print("Promote with:  review_notes.py promote <id> [...]   (or --all)")
    return 0


def _targets(args: list[str]) -> list[str]:
    if "--all" in args:
        return [r["id"] for r in _proposals()]
    return args


def cmd_promote(args: list[str]) -> int:
    ids = _targets(args)
    if not ids:
        print("Nothing to promote. Pass one or more ids, or --all.")
        return 1
    n = 0
    for rid in ids:
        p = _find(rid)
        if not p:
            print(f"  ? no proposal '{rid}'")
            continue
        r = memory_store._parse(p)
        m = r["meta"]
        new_id = memory_store.write(r["body"], type=m.get("type", "preference"),
                                    source="reviewed", confidence=float(m.get("confidence", 0.8)))
        p.unlink()
        n += 1
        print(f"  ✓ promoted '{rid}' -> memory '{new_id}'")
    print(f"Promoted {n} memory(ies).")
    return 0


def cmd_discard(args: list[str]) -> int:
    ids = _targets(args)
    if not ids:
        print("Nothing to discard. Pass one or more ids, or --all.")
        return 1
    n = 0
    for rid in ids:
        p = _find(rid)
        if not p:
            print(f"  ? no proposal '{rid}'")
            continue
        p.unlink()
        n += 1
        print(f"  ✗ discarded '{rid}'")
    print(f"Discarded {n} proposal(s).")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    rest = argv[1:]
    if cmd == "list":
        return cmd_list()
    if cmd == "promote":
        return cmd_promote(rest)
    if cmd == "discard":
        return cmd_discard(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
