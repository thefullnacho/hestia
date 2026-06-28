# Hestia's memory store

This directory is the brain's long-term memory — one markdown file per fact, written
and read at runtime by the `memory` tool (`brain/tools/memory_tool.py` →
`brain/memory_store.py`). Design: [`../MEMORY-DESIGN.md`](../MEMORY-DESIGN.md).

Each record is frontmatter (type, confidence, source, last_seen, links, pinned) plus
free text. `INDEX.md` is auto-generated, one line per record.

The records themselves are **gitignored** here — they're runtime data, not code, and
shouldn't pollute the code repo's history. If you want them versioned (the design's
"every learned fact is an auditable diff"), point `HESTIA_MEMORY_DIR` at a dedicated
git repo. The store path is configurable via that env var (default: this dir).

v1 recall is keyword overlap; vector recall is a planned upgrade (markdown stays the
source of truth, the index is derived).
