"""Model work that waits for a model: the queue between the timed jobs and a phone.

On a box whose model runs on the household's phone (HESTIA_BACKEND=phone), a 07:10 job can't
call the model: the phone may be in a pocket, and its connection lives in the brain's process,
not the job's. So the job does everything code can do, writes the model request here with what
it needs to finish, and exits. When a model is reachable the brain drains the queue oldest
first: it runs each request and hands the answer to that kind's finisher, which does exactly
what the job would have done with it.

Nothing deterministic waits here. Facts are gathered, reminders fire and thresholds alert on
time; only the wording, ranking and extraction wait. A newer job with the same key replaces a
waiting one (today's briefing replaces yesterday's), and anything older than its expiry is
dropped rather than delivered stale.

    python model_jobs.py --list     # what is waiting
    python model_jobs.py --drain    # run the queue through backend.complete() now
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys

import records_store as store  # shared hestia.db connection (schema + perms)

# kind -> "module:function". Finishers take (answer_text, context) and are resolved lazily, so
# this module never imports the jobs (they load secrets and tools at import).
FINISHERS = {
    "briefing": "briefing:finish",
    "feed": "feed:finish",
    "note": "note_taker:finish",
}
MAX_ATTEMPTS = 3


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def enqueue(kind: str, request: dict, context: dict, *, key: str | None = None,
            ttl_hours: float = 24.0) -> int:
    """Queue one model request; returns its id. A waiting job with the same kind and key is
    marked superseded first, so only the newest is ever finished."""
    if kind not in FINISHERS:
        raise ValueError(f"unknown model job kind {kind!r}")
    now = _now()
    with store._conn() as c:
        if key:
            c.execute("UPDATE model_jobs SET status='superseded', finished_at=? "
                      "WHERE kind=? AND key=? AND status='pending'", (now.isoformat(), kind, key))
        cur = c.execute("INSERT INTO model_jobs(kind,key,request,context,status,created_at,expires_at) "
                        "VALUES(?,?,?,?,'pending',?,?)",
                        (kind, key, json.dumps(request), json.dumps(context), now.isoformat(),
                         (now + dt.timedelta(hours=ttl_hours)).isoformat()))
        return cur.lastrowid


def pending() -> list[dict]:
    """Waiting jobs, oldest first. Expired ones are marked so and left out."""
    now = _now().isoformat()
    with store._conn() as c:
        c.execute("UPDATE model_jobs SET status='expired', finished_at=? "
                  "WHERE status='pending' AND expires_at<=?", (now, now))
        rows = c.execute("SELECT id,kind,key,request,context,attempts,created_at FROM model_jobs "
                         "WHERE status='pending' ORDER BY created_at, id").fetchall()
    return [{**dict(r), "request": json.loads(r["request"]), "context": json.loads(r["context"])}
            for r in rows]


def _finisher(kind: str):
    module, func = FINISHERS[kind].split(":")
    return getattr(importlib.import_module(module), func)


def _close(job_id: int, status: str, error: str | None = None, *, attempt: bool = False) -> None:
    with store._conn() as c:
        c.execute("UPDATE model_jobs SET status=?, finished_at=?, error=?, attempts=attempts+? WHERE id=?",
                  (status, _now().isoformat(), error, 1 if attempt else 0, job_id))


def drain(run) -> dict:
    """Run every waiting job through `run(request) -> {"content", "truncated"}` and finish it.
    A failure leaves the job waiting for the next drain, up to MAX_ATTEMPTS. Returns counts."""
    counts = {"done": 0, "failed": 0, "retry": 0}
    for job in pending():
        try:
            out = run(job["request"])
            if out.get("truncated"):
                raise ValueError("the model's answer was cut off")
            _finisher(job["kind"])(out.get("content") or "", job["context"])
        except Exception as e:  # noqa: BLE001 - one bad job must not stop the rest
            if job["attempts"] + 1 >= MAX_ATTEMPTS:
                _close(job["id"], "failed", type(e).__name__ + ": " + str(e)[:200], attempt=True)
                counts["failed"] += 1
            else:
                with store._conn() as c:
                    c.execute("UPDATE model_jobs SET attempts=attempts+1, error=? WHERE id=?",
                              (type(e).__name__ + ": " + str(e)[:200], job["id"]))
                counts["retry"] += 1
            continue
        _close(job["id"], "done", attempt=True)
        counts["done"] += 1
    return counts


def run_with_backend(request: dict) -> dict:
    """Run a queued request through backend.complete(), the box's own model door."""
    import backend
    return backend.complete(request["messages"], **request.get("options", {}))


def main() -> int:
    import config
    config.load_secrets()
    if "--drain" in sys.argv:
        print(f"model_jobs: {drain(run_with_backend)}")
        return 0
    for job in pending():
        print(f"#{job['id']} {job['kind']} {job['key'] or ''} queued {job['created_at']} "
              f"attempts={job['attempts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
