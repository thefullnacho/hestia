import datetime as dt

import breeding_followups
import puppy_watch
import reminders_store
import tools.records as records

NOW = dt.datetime(2026, 3, 20, 20, 30)


def dam(db, name="Juniper", **attrs):
    return db.upsert_entity("pet", name, attrs={"sex": "female", **attrs})


def tie(db, name="Juniper", ts="2026-03-02T12:00:00", action="tied"):
    return db.log_event("breeding", subject=name, action=action, ts=ts)


def test_a_tie_files_the_day_28_check_and_the_day_56_whelp_watch(db):
    dam(db)
    tie(db)
    made = breeding_followups.ensure(NOW)
    assert [(m["what"], m["due_at"]) for m in made] == [("pregnancy check", "2026-03-30T08:00:00"),
                                                        ("whelp-watch", "2026-04-27T08:00:00")]
    assert "Juniper pregnancy check: bred March 2, day 28 today." in made[0]["text"]
    assert "palpation or ultrasound" in made[0]["text"]
    assert "due around May 4" in made[1]["text"] and "below 99 degrees" in made[1]["text"]
    pending = reminders_store.pending()
    assert [r["due_at"] for r in pending] == ["2026-03-30T08:00:00", "2026-04-27T08:00:00"]
    assert all(r["announce"] == 1 for r in pending)  # spoken in the kitchen, not only pushed


def test_running_it_again_files_nothing(db):
    dam(db)
    tie(db)
    assert len(breeding_followups.ensure(NOW)) == 2
    assert breeding_followups.ensure(NOW) == []
    assert len(reminders_store.pending()) == 2


def test_a_milestone_already_past_is_not_filed(db):
    dam(db)
    tie(db, ts="2026-02-01T12:00:00")  # day 28 was Mar 1, day 56 is Mar 29
    made = breeding_followups.ensure(NOW)
    assert [m["what"] for m in made] == ["whelp-watch"]


def test_a_reminder_set_by_hand_is_not_doubled_but_an_unrelated_one_does_not_hide_it(db):
    dam(db)
    tie(db)
    reminders_store.add("2026-03-30T09:00:00", "Juniper ultrasound with the vet")
    reminders_store.add("2026-04-27T07:00:00", "Juniper: heartworm pill")  # same day, different job
    made = breeding_followups.ensure(NOW)
    assert [m["what"] for m in made] == ["whelp-watch"]


def test_a_second_tie_days_later_is_the_same_breeding(db):
    dam(db)
    tie(db)
    tie(db, ts="2026-03-04T08:00:00")
    assert len(breeding_followups.ensure(NOW)) == 2


def test_only_a_tie_by_a_dam_counts(db):
    dam(db)
    tie(db, action="paired")
    tie(db, action="tie date corrected")
    db.upsert_entity("pet", "Rowan", attrs={"sex": "male", "role": "sire"})
    tie(db, name="Rowan")
    db.log_event("breeding", subject="a typo", action="tied", ts="2026-03-02T12:00:00")  # minted as a thing
    assert breeding_followups.ensure(NOW) == []


def test_a_dry_run_files_nothing(db):
    dam(db)
    tie(db)
    assert len(breeding_followups.ensure(NOW, dry_run=True)) == 2
    assert reminders_store.pending() == []


def test_logging_a_tie_through_the_tool_sets_the_reminders_and_says_so(db):
    dam(db)
    when = (dt.datetime.now() - dt.timedelta(days=3)).replace(microsecond=0).isoformat()
    out = records.execute("log", kind="breeding", subject="Juniper", did="tied", ts=when)
    assert out.startswith("Logged breeding · Juniper · tied.")
    assert "Reminders set: Juniper pregnancy check on" in out and "Juniper whelp-watch on" in out
    assert len(reminders_store.pending()) == 2
    assert "Reminders set" not in records.execute("log", kind="breeding", subject="Juniper", did="tied", ts=when)


def test_the_verb_alone_is_enough_even_if_the_kind_is_wrong(db):
    dam(db)
    when = (dt.datetime.now() - dt.timedelta(days=3)).replace(microsecond=0).isoformat()
    out = records.execute("log", kind="health", subject="Juniper", did="tied", ts=when)
    assert "Reminders set" in out


def test_other_logs_leave_reminders_alone(db):
    dam(db)
    assert records.execute("log", kind="health", subject="Juniper", did="weighed") == "Logged health · Juniper · weighed."
    assert reminders_store.pending() == []


def test_a_failed_follow_up_never_fails_the_log(db, monkeypatch):
    dam(db)

    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(breeding_followups, "ensure", boom)
    out = records.execute("log", kind="breeding", subject="Juniper", did="tied", ts="2026-03-18T10:00:00")
    assert out.startswith("Logged breeding") and "could not be set (RuntimeError)" in out
    assert len(db.recent_events(kind="breeding")) == 1  # the tie itself is on the books


def test_the_puppy_watch_backstops_a_tie_that_skipped_the_tool(db, capsys):
    dam(db)
    tie(db, ts=(dt.datetime.now() - dt.timedelta(days=3)).replace(microsecond=0).isoformat())
    puppy_watch.follow_ups(dry_run=True)
    assert "would file reminder: Juniper pregnancy check" in capsys.readouterr().out
    assert reminders_store.pending() == []
    puppy_watch.follow_ups()
    assert "filed reminder: Juniper whelp-watch" in capsys.readouterr().out
    assert len(reminders_store.pending()) == 2


def test_a_broken_backstop_does_not_stop_the_pup_alerts(db, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(breeding_followups, "ensure", boom)
    puppy_watch.follow_ups()  # must not raise
    assert "breeding follow-ups failed: RuntimeError" in capsys.readouterr().err
