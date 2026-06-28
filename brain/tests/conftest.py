"""Shared fixtures: point the stores at throwaway temp locations so tests never touch
the real records DB or the markdown memory. Both stores read their target from a module
global (records_store.DB_PATH / memory_store.MEMORY_DIR) at call time, so monkeypatching
that global fully isolates a test — the same override pattern eval_models.py uses."""
from __future__ import annotations

import sys
from pathlib import Path

# Put brain/ on the path before importing the flat modules, so the suite runs the same
# whether pytest is invoked from the repo root or from brain/ (the pyproject pythonpath
# only resolves when rootdir is brain/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import memory_store  # noqa: E402
import note_taker  # noqa: E402
import records_store  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh, empty records DB for this test. Schema is created lazily on first _conn."""
    path = tmp_path / "hestia.db"
    monkeypatch.setattr(records_store, "DB_PATH", path)
    return records_store


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """A fresh, empty markdown memory dir for this test."""
    path = tmp_path / "memory"
    path.mkdir()
    monkeypatch.setattr(memory_store, "MEMORY_DIR", path)
    return memory_store


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    """A fresh, empty note-taker proposal inbox for this test."""
    path = tmp_path / "inbox"
    monkeypatch.setattr(note_taker, "INBOX_DIR", path)
    return note_taker
