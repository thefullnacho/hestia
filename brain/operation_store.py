"""Durable request replay and operation receipts. Private data, never model authority.

A crash after dispatch leaves an unknown operation. We do not re-execute it: a
remote side effect and a local SQLite commit cannot be made one transaction.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
import time

import config
from tool_contract import receipt


def _connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / 'operations.db'
    conn = sqlite3.connect(path, timeout=5)
    path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT,
            created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, request_id TEXT NOT NULL, name TEXT NOT NULL,
            status TEXT NOT NULL, result TEXT, created REAL NOT NULL);
    ''')
    return conn


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def claim_request(key, messages):
    fingerprint = digest(messages)
    with closing(_connect()) as conn, conn:
        changed = conn.execute('INSERT OR IGNORE INTO requests VALUES (?, ?, NULL, ?)',
                               (key, fingerprint, time.time())).rowcount
        row = conn.execute('SELECT * FROM requests WHERE id=?', (key,)).fetchone()
    if row['fingerprint'] != fingerprint:
        return 'conflict', None
    if changed:
        return 'new', None
    return ('complete', json.loads(row['response'])) if row['response'] else ('pending', None)


def finish_request(key, response):
    with closing(_connect()) as conn, conn:
        conn.execute('UPDATE requests SET response=? WHERE id=?', (json.dumps(response), key))


def execute_once(request_id, name, args, execute):
    op_id = digest([request_id, name, args])
    with closing(_connect()) as conn, conn:
        changed = conn.execute('INSERT OR IGNORE INTO operations VALUES (?, ?, ?, ?, NULL, ?)',
                               (op_id, request_id, name, 'unknown', time.time())).rowcount
        row = conn.execute('SELECT * FROM operations WHERE id=?', (op_id,)).fetchone()
    if not changed:
        return row['result'] or 'Operation outcome unknown; do not repeat this write.'
    try:
        result = str(execute(name, args))
    except Exception:
        result = 'Operation outcome unknown; the backend raised after dispatch. Do not repeat this write.'
    status = receipt(name, args, result).status
    with closing(_connect()) as conn, conn:
        conn.execute('UPDATE operations SET status=?, result=? WHERE id=?', (status, result, op_id))
    return result


def operations(request_id):
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute(
            'SELECT id, name, status, result FROM operations WHERE request_id=? ORDER BY created', (request_id,))]
