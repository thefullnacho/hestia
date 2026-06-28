"""Reminders — one-shot, time-triggered phone pushes.

The determinism play: the brain never *holds* a reminder, it writes a row here; a dumb
one-minute systemd timer (reminders_tick.py) fires anything due via a Home Assistant
phone push. A reminder is just another local record, so this shares hestia.db — and its
single connection authority + 0600 perms — with records_store (the `reminders` table is
created by records_store._SCHEMA). seed_garden.py already reaches into store internals the
same way; this follows that precedent rather than standing up a second DB connection.
"""
from __future__ import annotations

import records_store as store  # shared _conn() (schema + perms) and _now()


def add(due_at: str, text: str) -> int:
    """File a reminder; returns its id. `due_at` is an ISO local timestamp."""
    with store._conn() as c:
        cur = c.execute("INSERT INTO reminders(due_at,text,created_at) VALUES(?,?,?)",
                        (due_at, text, store._now()))
        return cur.lastrowid


def pending(limit: int = 50) -> list[dict]:
    """Not-yet-fired reminders, soonest first."""
    with store._conn() as c:
        rows = c.execute("SELECT id,due_at,text FROM reminders WHERE fired_at IS NULL "
                         "ORDER BY due_at LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def due(now_iso: str) -> list[dict]:
    """Unfired reminders whose time has come (due_at <= now)."""
    with store._conn() as c:
        rows = c.execute("SELECT id,due_at,text FROM reminders "
                         "WHERE fired_at IS NULL AND due_at<=? ORDER BY due_at",
                         (now_iso,)).fetchall()
    return [dict(r) for r in rows]


def mark_fired(rid: int, when_iso: str) -> None:
    with store._conn() as c:
        c.execute("UPDATE reminders SET fired_at=? WHERE id=?", (when_iso, rid))


def cancel(rid: int) -> bool:
    """Drop a pending reminder; True if one was removed (already-fired ones are left alone)."""
    with store._conn() as c:
        cur = c.execute("DELETE FROM reminders WHERE id=? AND fired_at IS NULL", (rid,))
        return cur.rowcount > 0
