"""Goals: the model drafts, a person approves, code tracks. Store, tool, routes, wiring."""
from __future__ import annotations

import datetime as dt

import pytest

import goals_store
import reminders_store
import tool_contract
from tools import goals as tool

TODAY = dt.date.today()


def day(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def draft(db, steps=None):
    return goals_store.propose("Greenhouse winter-ready", steps or [
        {"text": "Order the greenhouse heater", "due": day(3)},
        {"text": "Seal the door gaps", "due": day(10)},
        {"text": "Test the heater overnight", "due": None},
    ], why="Peppers overwinter", target=day(30))


# ----- store -----------------------------------------------------------------------------

def test_a_draft_is_inert_until_approved(db):
    g = draft(db)
    assert g["status"] == "proposed" and len(g["steps"]) == 3
    assert reminders_store.pending() == []
    assert db.recent_events(kind="goal") == []


def test_approval_records_edits_schedules_dated_steps_and_logs(db):
    g = draft(db)
    first, second, third = g["steps"]
    goal = goals_store.approve(g["id"], g["title"], [
        {"id": first["id"], "text": first["text"], "due": first["due"]},           # kept
        {"id": second["id"], "text": "Seal the door and vent gaps", "due": day(12)},  # edited + redated
        {"text": "Buy a min/max thermometer", "due": day(5)},                    # added
    ], why=g["why"], target=g["target"])                                       # third removed
    assert goal["status"] == "active"
    assert goal["review"] == {"drafted": 3, "kept": 1, "edited": 1, "redated": 1, "added": 1,
                              "removed": 1, "approved": 3, "title_changed": False}
    assert goal["draft"]["steps"][2]["text"] == third["text"]      # the original survives
    pending = reminders_store.pending()
    assert len(pending) == 3 and all(p["due_at"].endswith("T09:00:00") for p in pending)
    assert db.recent_events(kind="goal")[0]["action"] == "approved"


def test_done_cancels_the_push_logs_it_and_closes_the_goal_on_the_last(db):
    g = draft(db, [{"text": "Order the heater", "due": day(3)}, {"text": "Seal the door", "due": None}])
    g = goals_store.approve(g["id"], g["title"], [{"id": s["id"], "text": s["text"], "due": s["due"]} for s in g["steps"]])
    a, b = g["steps"]
    assert len(reminders_store.pending()) == 1
    r = goals_store.mark_done(a["id"])
    assert not r["goal_done"] and reminders_store.pending() == []
    assert goals_store.mark_done(b["id"])["goal_done"]
    assert goals_store.get(g["id"])["status"] == "done"
    actions = [e["action"] for e in db.recent_events(kind="goal")]
    assert "step done" in actions and "goal done" in actions


def test_undo_reopens_the_goal_and_rearms_the_push(db):
    g = draft(db, [{"text": "Order the heater", "due": day(3)}])
    g = goals_store.approve(g["id"], g["title"], [{"id": g["steps"][0]["id"], "text": "Order the heater", "due": day(3)}])
    sid = g["steps"][0]["id"]
    goals_store.mark_done(sid)
    goals_store.undo_done(sid)
    assert goals_store.get(g["id"])["status"] == "active"
    assert len(reminders_store.pending()) == 1


def test_overdue_and_due_today_are_date_comparisons(db):
    g = goals_store.propose("Garage", [{"text": "Sort the shelves", "due": day(-2)},
                                       {"text": "Haul the scrap", "due": day(0)}])
    goals_store.approve(g["id"], "Garage", [{"id": s["id"], "text": s["text"], "due": s["due"]} for s in g["steps"]])
    assert [(s["text"], s["days"]) for s in goals_store.overdue()] == [("Sort the shelves", 2)]
    assert [s["text"] for s in goals_store.due_on()] == ["Haul the scrap"]
    assert reminders_store.pending() == []   # past and today's 9am already gone: no stale pushes


def test_discard_keeps_the_row_for_the_record(db):
    g = draft(db)
    assert goals_store.discard(g["id"])["status"] == "dropped"
    with pytest.raises(ValueError):
        goals_store.approve(g["id"], "x", [{"text": "y"}])


def test_bad_plans_are_refused(db):
    with pytest.raises(ValueError):
        goals_store.propose("", [{"text": "a"}])
    with pytest.raises(ValueError):
        goals_store.propose("t", [])
    with pytest.raises(ValueError):
        goals_store.propose("t", [{"text": "a"}] * 13)


# ----- tool ------------------------------------------------------------------------------

def test_propose_resolves_date_phrases_in_code(db):
    out = tool.execute("propose", title="Greenhouse winter-ready", target="in 4 weeks",
                       steps=[{"text": "Order the heater", "due": "in 3 days"}, {"text": "Seal the door"}])
    assert out.startswith("Drafted goal #") and "Goals page" in out
    g = goals_store.goals()[0]
    assert g["target"] == day(28) and g["steps"][0]["due"] == day(3) and g["steps"][1]["due"] is None


def test_an_unreadable_date_costs_the_date_not_the_plan(db):
    """Gemma put 'Ongoing consistency on AKC and GoodDog' in target (2026-10-20); refusing the
    whole plan over it lost a good draft."""
    out = tool.execute("propose", title="Puppy content habit", target="Ongoing consistency",
                       steps=[{"text": "Pick two shoot days", "due": "whenever-ish"}, {"text": "First shoot", "due": "in 3 days"}])
    assert out.startswith("Drafted goal #") and "'Ongoing consistency'" in out and "'whenever-ish'" in out
    g = goals_store.goals()[0]
    assert g["target"] is None and g["steps"][0]["due"] is None and g["steps"][1]["due"] == day(3)


def approved(db):
    g = goals_store.propose("Greenhouse winter-ready", [{"text": "Order the greenhouse heater"},
                                                        {"text": "Seal the door gaps"}])
    return goals_store.approve(g["id"], g["title"], [{"id": s["id"], "text": s["text"], "due": None} for s in g["steps"]])


def test_done_from_chat_matches_the_step_by_its_words(db):
    g = approved(db)
    out = tool.execute("done", step="finally ordered the heater for the greenhouse")
    assert out.startswith("Marked done: Order the greenhouse heater") and "1 of 2" in out
    assert tool.execute("done", step="something unrelated entirely").startswith("No open goal step")


def test_done_refuses_a_draft(db):
    g = goals_store.propose("Garage", [{"text": "Sort the garage shelves"}])
    assert tool.execute("done", step=f"#{g['steps'][0]['id']}").startswith("Error:")


def test_list_and_show(db):
    approved(db)
    assert "0/2 steps, active" in tool.execute("list")
    assert "Seal the door gaps" in tool.execute("show", goal="greenhouse")


# ----- contract and guard ----------------------------------------------------------------

def test_receipts_count_only_real_writes():
    ok = tool_contract.receipt("goals", {"action": "propose"}, "Drafted goal #1 for review: x")
    assert ok.status == "succeeded"
    assert tool_contract.receipt("goals", {"action": "done"}, "Marked done: x").status == "succeeded"
    assert tool_contract.receipt("goals", {"action": "done"}, "No open goal step matches 'x'.").status == "failed"


def test_a_plan_claimed_without_a_tool_is_caught():
    import guards
    assert guards.claims_action("I've drafted a plan with five steps for the greenhouse.")
    assert guards.claims_action("Marked the heater step as done.")


def test_finishing_a_step_offers_the_goals_tool(db):
    import routing  # noqa: E402
    import hestia
    approved(db)
    assert routing.finishes_open_step("finally ordered the greenhouse heater")
    assert not routing.finishes_open_step("turn off the kitchen lights")


# ----- routes ----------------------------------------------------------------------------

@pytest.fixture
def client(db):
    testclient = pytest.importorskip("fastapi.testclient")
    import hestia
    return testclient.TestClient(hestia.app)


H = {"X-Hestia-Goals": "1"}


def test_writes_need_the_page_header(client):
    g = draft(client)
    assert client.post(f"/api/goals/{g['id']}/discard", json={}).status_code == 409
    assert client.post(f"/api/goals/{g['id']}/discard", json={}, headers=H).json()["goal"]["status"] == "dropped"


def test_page_flow_approve_tick_untick(client):
    g = draft(client)
    listed = client.get("/api/goals").json()["goals"]
    assert listed[0]["status"] == "proposed"
    steps = [{"id": s["id"], "text": s["text"], "due": s["due"]} for s in g["steps"]]
    r = client.post(f"/api/goals/{g['id']}/approve", json={"title": g["title"], "steps": steps}, headers=H)
    assert r.status_code == 200 and r.json()["goal"]["status"] == "active"
    sid = r.json()["goal"]["steps"][0]["id"]
    assert client.post(f"/api/goals/steps/{sid}/done", json={}, headers=H).json()["goal"]["done"] == 1
    assert client.post(f"/api/goals/steps/{sid}/undo", json={}, headers=H).json()["goal"]["done"] == 0
    assert client.post("/api/goals/steps/99999/done", json={}, headers=H).status_code == 404


def test_today_carries_goals(client):
    g = goals_store.propose("Garage", [{"text": "Sort the shelves", "due": day(-1)}])
    goals_store.approve(g["id"], "Garage", [{"id": g["steps"][0]["id"], "text": "Sort the shelves", "due": day(-1)}])
    goals_ = client.get("/today").json()["goals"]
    assert goals_["active"][0]["next"]["text"] == "Sort the shelves"
    assert goals_["overdue"][0]["days"] == 1


def test_briefing_states_overdue_steps(db):
    import briefing
    g = goals_store.propose("Garage", [{"text": "Sort the shelves", "due": day(-2)}])
    goals_store.approve(g["id"], "Garage", [{"id": g["steps"][0]["id"], "text": "Sort the shelves", "due": day(-2)}])
    assert briefing._goal_facts(dt.datetime.now()) == ["Goal step overdue by 2 days: Sort the shelves (goal: Garage)."]


# ----- dating after approval (the first real plan came back with no dates, 2026-10-20) --------

def test_a_person_can_date_and_redate_an_approved_step(db):
    g = approved(db)
    sid = g["steps"][0]["id"]
    goals_store.set_due(sid, day(4))
    assert [p["due_at"] for p in reminders_store.pending()] == [day(4) + "T09:00:00"]
    goals_store.set_due(sid, day(6))
    assert [p["due_at"] for p in reminders_store.pending()] == [day(6) + "T09:00:00"]
    goals_store.set_due(sid, None)
    assert reminders_store.pending() == [] and goals_store.get(g["id"])["steps"][0]["due"] is None
    assert "step redated" in [e["action"] for e in db.recent_events(kind="goal")]


def test_redating_refuses_drafts_and_done_steps(db):
    d = draft(db)
    with pytest.raises(ValueError):
        goals_store.set_due(d["steps"][0]["id"], day(2))
    g = approved(db)
    goals_store.mark_done(g["steps"][0]["id"])
    with pytest.raises(ValueError):
        goals_store.set_due(g["steps"][0]["id"], day(2))


def test_an_all_undated_plan_is_flagged_to_the_model(db):
    out = tool.execute("propose", title="Puppy content", steps=[{"text": "Pick themes"}, {"text": "First shoot"}])
    assert out.startswith("Drafted goal #") and "No step has a date" in out
    dated = tool.execute("propose", title="Garage", steps=[{"text": "Sort", "due": "in 2 days"}])
    assert "No step has a date" not in dated


def test_page_redates_a_step(client):
    g = approved(client)
    sid = g["steps"][1]["id"]
    r = client.post(f"/api/goals/steps/{sid}/due", json={"due": day(5)}, headers=H)
    assert r.status_code == 200 and r.json()["goal"]["steps"][1]["due"] == day(5)
    assert client.post(f"/api/goals/steps/{sid}/due", json={"due": "soon"}, headers=H).status_code == 409


def test_two_quick_redates_leave_one_reminder(db):
    """The page fired two change events for one pick and filed two 9am pushes (2026-10-20)."""
    import threading
    g = approved(db)
    sid = g["steps"][0]["id"]
    threads = [threading.Thread(target=goals_store.set_due, args=(sid, day(4))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reminders_store.pending()) == 1
