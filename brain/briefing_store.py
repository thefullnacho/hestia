"""Private briefing history: generated snapshots and delivery receipts, not learned facts."""
from __future__ import annotations

from contextlib import closing
import datetime as dt
import json
import os
import re
import sqlite3
import uuid

import config


def _path():
    return config.DATA_DIR / 'briefings.db'


def _connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _path()
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=2)
    conn.execute('CREATE TABLE IF NOT EXISTS briefings (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)')
    return conn


def create(facts, text, model, narration, delivery, *, collected_at=None):
    now = dt.datetime.now().astimezone().isoformat()
    entry = {'id': uuid.uuid4().hex, 'created_at': now, 'facts': facts,
             'collected_at': collected_at or now, 'text': text, 'model': model, 'narration': narration, 'delivery': delivery}
    with closing(_connect()) as conn, conn:
        conn.execute('INSERT INTO briefings VALUES (?,?,?)', (entry['id'], now, json.dumps(entry)))
    return entry['id']


def update_delivery(identifier, delivery):
    with closing(_connect()) as conn, conn:
        row = conn.execute('SELECT payload FROM briefings WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Unknown briefing id')
        entry = json.loads(row[0])
        entry['delivery'] = delivery
        entry['delivery_updated_at'] = dt.datetime.now().astimezone().isoformat()
        conn.execute('UPDATE briefings SET payload=? WHERE id=?', (json.dumps(entry), identifier))


def recent(limit=30, day=None):
    if not _path().exists():
        return []
    # Read-only connections: recall must not create or alter the archive.
    with closing(sqlite3.connect(_path().resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        clause = ' WHERE substr(created_at,1,10)=?' if day else ''
        params = ([day] if day else []) + [min(max(limit, 1), 100)]
        rows = conn.execute('SELECT payload FROM briefings' + clause + ' ORDER BY created_at DESC, rowid DESC LIMIT ?', params).fetchall()
    return [json.loads(row[0]) for row in rows]


def requested(text):
    return bool(re.search(r'\b(?:briefings?|announcements?|announced)\b|\b(?:you|hestia) (?:say|said|mention(?:ed)?|tell me|told me)\b.*\b(?:morning|earlier|yesterday)\b', text, re.I))


def context(text):
    if not requested(text):
        return ''
    today = dt.datetime.now().astimezone().date()
    followup = text.rsplit('\nFollow-up:', 1)[-1]
    date_text = followup if re.search(r'\byesterday\b|\btoday\b|this morning|\d{4}-\d{2}-\d{2}', followup, re.I) else text
    dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', date_text)
    target = dates[-1] if dates else None
    if not target and re.search(r'\byesterday\b', date_text, re.I):
        target = (today - dt.timedelta(days=1)).isoformat()
    elif not target and re.search(r'\btoday\b|\bthis morning\b', date_text, re.I):
        target = today.isoformat()
    try:
        entries = recent(limit=3, day=target)
    except (OSError, sqlite3.Error, ValueError):
        return 'Briefing archive unavailable. Do not invent an announcement or its delivery.'
    if target:
        entries = [e for e in entries if e['created_at'][:10] == target]
    if not entries:
        return f'No saved briefing found for {target or "the recent archive"}. Older announcements were not backfilled. Do not reconstruct what was said from current facts.'
    selected = entries[:1] if target or re.search(r'\b(?:latest|last)\b', text, re.I) else entries[:3]
    return json.dumps({'selection': target or 'most recent (up to three)', 'records': selected}, ensure_ascii=False)
