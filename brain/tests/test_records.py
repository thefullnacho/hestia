"""Records tool — model-supplied numbers/strings are validated at the tool edge before
they reach the store. `limit=-1` is sqlite's 'no limit' (one bad arg dumps the whole
event log into context), a non-positive harvest qty would pollute permanent season
totals, and a malformed attrs string was silently dropped while the event logged
anyway. Store-level behavior is pinned in test_records_store.py; these are tool-level."""
from __future__ import annotations

import tools.records as records


def test_recent_limit_is_capped(db):
    """The tool advertises max 100; a huge model-supplied limit must not pull more."""
    for i in range(150):
        db.log_event("note", subject=f"thing-{i}")
    out = records.execute("recent", limit=10000)
    assert out.startswith("Recent records (100):")


def test_recent_limit_garbage_defaults(db):
    for i in range(25):
        db.log_event("note", subject=f"thing-{i}")
    out = records.execute("recent", limit="lots")
    assert out.startswith("Recent records (20):")


def test_harvest_rejects_nonpositive_qty(db):
    out = records.execute("harvest", bed="Bed 1", crop="Tomatoes", qty=-2)
    assert "positive" in out
    assert db.harvest_totals() == []


def test_harvest_rejects_zero_qty(db):
    out = records.execute("harvest", bed="Bed 1", crop="Tomatoes", qty=0)
    assert "positive" in out
    assert db.harvest_totals() == []


def test_malformed_attrs_refused_not_dropped(db):
    out = records.execute("log", kind="sighting", subject="deer", did="observed",
                          attrs="{not json")
    assert "Error" in out and "attrs" in out
    assert db.recent_events() == []


def test_non_object_attrs_refused(db):
    """A valid-JSON non-dict ('[1,2]') used to flow a list into the store as attrs."""
    out = records.execute("remember", name="Momo", kind="pet", attrs="[1, 2]")
    assert "Error" in out and "attrs" in out
    assert db.entity_profile("Momo") is None


def test_valid_attrs_still_recorded(db):
    out = records.execute("remember", name="Momo", kind="pet", attrs={"breed": "Lhasa Apso"})
    assert "Remembered Momo" in out
    assert db.entity_profile("Momo")["attrs"]["breed"] == "Lhasa Apso"


# ── what a log reply says it stored ──────────────────────────────────────────────────────
# After a write the brain answers with the tool's own text, so this is what the operator sees
# and hears. It has to show the date the event landed on and the start of its detail, because
# a wrong date, a missing detail or a mistyped name is caught there or not at all.

import datetime as dt

import config
import operation_store
import tool_contract
import tools


def _ago(days):
    return (dt.datetime.now() - dt.timedelta(days=days)).replace(microsecond=0)


def test_a_log_reply_says_the_date_it_landed_on_and_how_long_ago(db):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    when = _ago(20).replace(hour=12, minute=0, second=0)
    out = records.execute("log", kind="breeding", subject="Juniper", did="tied", ts=when.isoformat(),
                          detail="Tie with Rowan.")
    assert out.startswith(f'Logged breeding · Juniper · tied · {when:%b} {when.day}, {when.year} 12:00 PM '
                          '(20 days ago) · "Tie with Rowan."')


def test_a_log_with_no_ts_lands_today_and_a_date_only_ts_has_no_clock(db):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    out = records.execute("log", kind="note", subject="Juniper", did="weighed")
    assert out.endswith("(today).")
    day = _ago(3).date()
    out = records.execute("log", kind="note", subject="Juniper", did="weighed", ts=day.isoformat())
    assert f"{day:%b} {day.day}, {day.year} (3 days ago)." in out and "PM" not in out and "AM" not in out


def test_a_date_in_the_future_reads_as_one(db):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    tomorrow = (dt.datetime.now() + dt.timedelta(days=1)).replace(microsecond=0).isoformat()
    assert "(tomorrow)" in records.execute("log", kind="note", subject="Juniper", did="x", ts=tomorrow)
    soon = (dt.datetime.now() + dt.timedelta(days=5)).replace(microsecond=0).isoformat()
    assert "(in 5 days)" in records.execute("log", kind="note", subject="Juniper", did="x", ts=soon)


def test_a_timestamp_that_is_not_a_date_is_shown_as_given_not_hidden(db):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    out = records.execute("log", kind="note", subject="Juniper", did="x", ts="last Tuesday")
    assert out.startswith("Logged note · Juniper · x · last Tuesday.")  # visible, so it can be caught


def test_the_detail_is_echoed_trimmed_and_the_place_is_kept(db):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    long = "A very long note about the day. " * 6
    out = records.execute("log", kind="note", subject="Juniper", did="observed", detail=long, location="the yard")
    quoted = out.split('"')[1]
    assert len(quoted) == 60 and quoted.endswith("...") and "  " not in quoted
    assert out.endswith('" @ the yard.')


def test_a_name_the_records_have_never_seen_is_flagged_when_it_is_minted(db):
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    assert "⚠" not in records.execute("log", kind="health", subject="Juniper", did="weighed")
    out = records.execute("log", kind="health", subject="Junipr", did="weighed")
    assert "⚠ 'Junipr' wasn't a known name, so a new record was created." in out


def test_the_echo_is_still_recognised_as_a_successful_write():
    plain = "Logged breeding · Juniper · tied · Mar 2, 2026 12:00 PM (5 days ago) · \"Tie.\"."
    warned = plain + "  ⚠ 'Junipr' wasn't a known name, so a new record was created. Correct me if that's a mishear."
    for text in (plain, warned):
        assert tool_contract.receipt("records", {"action": "log"}, text).status == "succeeded"


def test_the_stored_receipt_keeps_the_values_the_model_chose(db, tmp_path, monkeypatch):
    """operations.db keeps a digest of the arguments, not the arguments, so the receipt text is
    the audit trail of what the model chose. The echo is what puts the choices in it."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db.upsert_entity("pet", "Juniper", attrs={"sex": "female"})
    args = {"action": "log", "kind": "health", "subject": "Juniper", "did": "weighed",
            "ts": "2026-03-02T12:00:00", "detail": "6 lb 2 oz"}
    operation_store.execute_once("req-1", "records", args, tools.dispatch)
    (op,) = operation_store.operations("req-1")
    assert op["status"] == "succeeded"
    assert "Logged health · Juniper · weighed · Mar 2, 2026 12:00 PM" in op["result"]
    assert '"6 lb 2 oz"' in op["result"]
