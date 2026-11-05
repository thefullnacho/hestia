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

import config  # noqa: E402
import memory_store  # noqa: E402
import note_taker  # noqa: E402
import records_store  # noqa: E402


@pytest.fixture(autouse=True)
def _scratch_db(tmp_path, monkeypatch):
    """Every test gets a throwaway DB, asked for or not. Fifteen tests that never took `db`
    (the agent loop, the claim guard, prompt building, shopping) reached the store through
    the prompt's entity roster or the shopping harvest check, and opened data/hestia.db at
    the default path: the live DB when the suite runs in the live checkout (found 2026-10-20).
    clips.py reads config.DB_PATH directly, so both globals move."""
    path = tmp_path / "hestia.db"
    monkeypatch.setattr(records_store, "DB_PATH", path)
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


@pytest.fixture(autouse=True)
def _scratch_phones(tmp_path, monkeypatch):
    """Phone devices and person ties in a scratch dir, for every test: reminder writes ask
    phone.devices_for whom to wake, and the real files under data/ are private. Pings run inline
    so a test sees them as they happen (push._spawn starts a thread outside tests)."""
    import phone
    import push
    monkeypatch.setattr(phone, "DEVICES", tmp_path / "phone_devices.json")
    monkeypatch.setattr(phone, "PEOPLE", tmp_path / "phone_people.json")
    monkeypatch.setattr(push, "_spawn", lambda fn: fn())


@pytest.fixture
def db(_scratch_db):
    """A fresh, empty records DB for this test. Schema is created lazily on first _conn."""
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
