"""Central configuration + path resolution for the Hestia brain.

One place for every filesystem path and secret location, so the brain is
*relocatable*: move the repo (or set HESTIA_ROOT) and nothing else needs editing —
the win that makes restore-on-new-hardware a one-liner instead of a grep-and-edit
across six files. Importing this module also guarantees brain/ is on sys.path, which
retires the per-file `sys.path.insert("~/hestia/brain")` hacks the scripts
used to carry.

Every path is env-overridable, but the defaults are *derived* from this file's own
location, so the common case needs no env at all. Service URLs / tokens / thresholds
still live next to the tools that use them (they're already env-overridable and don't
block relocation); this module owns paths + the secret bundle loading.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo root. config.py lives in <root>/brain/, so parent.parent is the root. Deriving
# it (rather than hardcoding ~/hestia) is what makes the brain relocatable;
# HESTIA_ROOT is the escape hatch for an unusual layout.
ROOT = Path(os.environ.get("HESTIA_ROOT") or Path(__file__).resolve().parent.parent)
BRAIN_DIR = ROOT / "brain"
CLIENTS_DIR = ROOT / "clients"   # thin web clients served by the brain (e.g. the chat PWA)
SECRETS_DIR = Path(os.environ.get("HESTIA_SECRETS_DIR") or ROOT / "secrets")
DATA_DIR = Path(os.environ.get("HESTIA_DATA_DIR") or ROOT / "data")
MEMORY_DIR = Path(os.environ.get("HESTIA_MEMORY_DIR") or ROOT / "memory")
SKILLS_DIR = BRAIN_DIR / "skills"

DB_PATH = Path(os.environ.get("HESTIA_DB") or DATA_DIR / "hestia.db")
PHOTO_DIR = Path(os.environ.get("HESTIA_PHOTO_DIR") or DATA_DIR / "photos")
# The household recipe collection (one markdown file per recipe). Private household data,
# so it lives under DATA_DIR (gitignored) — never the public tree, same posture as photos.
RECIPES_DIR = Path(os.environ.get("HESTIA_RECIPES_DIR") or DATA_DIR / "recipes")
# Proposals from the background note-taker await review here (one md per proposal).
INBOX_DIR = Path(os.environ.get("HESTIA_INBOX_DIR") or MEMORY_DIR / "inbox")
# Runtime state for the proactive garden-watch streak machine (XDG state dir by default).
_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
GARDEN_STATE = Path(os.environ.get("GARDEN_STATE") or _STATE_HOME / "hestia" / "garden_watch.json")

# Guarantee sibling modules (memory_store, records_store, the tools package, ...) import
# whether the brain is launched by uvicorn, a systemd script, or pytest — done once here
# instead of a sys.path.insert in every entrypoint.
if str(BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(BRAIN_DIR))

# The secret bundles, in load order. load_dotenv does NOT override real env vars, so
# systemd `Environment=` lines still win over the files. hosts.env carries deployment-specific
# service addresses (e.g. where Whisper/Piper bind) so they stay out of the public tree; see
# deploy/hosts.env.example. Missing bundles are skipped, so a single-box default just works.
_SECRET_FILES = ("ha.env", "media.env", "ingest.env", "hosts.env")


def load_secrets() -> None:
    """Load the secret .env bundles from SECRETS_DIR. Call this before importing tool
    modules that read tokens/URLs at import time. Missing files are silently skipped,
    and it's idempotent — safe to call from every entrypoint."""
    from dotenv import load_dotenv
    for name in _SECRET_FILES:
        load_dotenv(SECRETS_DIR / name)
