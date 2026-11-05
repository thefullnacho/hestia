"""inventory: amounts read by code, counts replayed from events, age from a table, no model.

Pinned: fuzzy counts are numbers kept as approximate, vague amounts stay unknown and are never
turned into a number, lots are used oldest first so the age warning follows the oldest stock
still on hand, and location is read back as "last noted", never required.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

import inventory_store as inv
import records_store
import tools
from tools import inventory as tool


@pytest.mark.parametrize("said, qty, unit, approx, vague", [
    ("2 bags", 2, "bag", False, False),
    ("a couple", 2, None, True, False),
    ("a couple of bags", 2, "bag", True, False),
    ("a few", 3, None, True, False),
    ("a handful", 5, None, True, False),
    ("half a dozen", 6, None, True, False),
    ("about 3 lb", 3, "lb", True, False),
    ("1/2 gallon", 0.5, "gallon", False, False),
    ("three jars", 3, "jar", False, False),
    ("some", None, None, False, True),
    ("some in a bag", None, "bag", False, True),
    ("a bit", None, None, False, True),
])
def test_amounts_as_people_say_them(said, qty, unit, approx, vague):
    a = inv.parse_amount(said)
    assert (a["qty"], a["unit"], a["approx"], a["vague"]) == (qty, unit, approx, vague)
    assert a["said"] == said


def test_a_bought_count_reads_back_and_a_fuzzy_one_says_about(db):
    assert tool.execute("add", "mulch", "2 bags").startswith("Logged 2 bags of mulch")
    assert "2 bags of mulch" in tool.execute("find", "mulch")
    tool.execute("add", "twine", "a couple of spools")
    assert "about 2 spools of twine" in tool.execute("find", "twine")


def test_a_vague_amount_is_a_fact_without_a_number(db):
    tool.execute("add", "neem oil", "some in a bag")
    out = tool.execute("find", "neem oil")
    assert "some neem oil (no count given)" in out
    assert not any(ch.isdigit() for ch in out.split("Last noted")[0])


def test_using_the_last_empties_it_and_unknown_use_makes_counts_fuzzy(db):
    tool.execute("add", "neem oil", "2 bottles")
    tool.execute("use", "neem oil", "some")
    assert "some neem oil" in tool.execute("find", "neem oil")
    out = tool.execute("use", "neem oil", "the last")
    assert out.startswith("Logged the last of neem oil as used up")
    import tool_contract
    assert tool_contract.receipt("inventory", {"action": "use"}, out).status == "succeeded"
    assert "no neem oil on hand" in tool.execute("find", "neem oil")


def test_use_takes_from_the_oldest_purchase_first(db):
    inv.bought("tomato seeds", "2 packets", when="2021-03-01")
    inv.bought("tomato seeds", "3 packets", when=dt.date.today().isoformat())
    out = inv.lookup("tomato seeds")
    assert "5 packets" in out and "Old stock" in out and "was bought March 2021" in out
    assert "seeds usually keep about 3 years" in out
    inv.used("tomato seeds", "2 packets")                      # the 2021 packets go first
    out = inv.lookup("tomato seeds")
    assert "3 packets" in out and "Old stock" not in out


def test_a_hand_added_item_is_dated_by_estimate_and_says_added(db):
    inv.bought("ibuprofen", "1 bottle")
    with records_store._conn() as c:
        c.execute("UPDATE events SET ts='2020-01-05T10:00:00', attrs=json_set(attrs, '$.bought', '2020-01-05')")
    out = inv.lookup("ibuprofen")
    assert "the oldest dates from about 2020" in out and "medicine usually keep" in out


def test_no_category_means_no_guessed_age_warning(db):
    inv.bought("garden twine", "1 spool", when="2015-06-01")
    assert "Old stock" not in inv.lookup("garden twine")


def test_count_sets_what_is_there_and_none_empties(db):
    tool.execute("add", "canning jars", "a dozen")
    assert "3 jars" in tool.execute("count", "canning jars", "3 jars")
    assert "no canning jars" in tool.execute("count", "canning jars", "none")
    assert tool.execute("count", "canning jars", "").startswith("Error")


def test_a_unit_that_cannot_be_subtracted_is_said_not_hidden(db):
    tool.execute("add", "fertilizer", "2 bags")
    out = tool.execute("use", "fertilizer", "1 lb")
    assert "uncertain" in out and "some fertilizer" in out


def test_location_is_last_noted_never_required(db):
    tool.execute("add", "neem oil", "1 bottle")
    assert "Last noted" not in tool.execute("find", "neem oil")
    assert tool.execute("move", "neem oil", location="the shed").startswith("Noted: ")
    out = tool.execute("find", "neem")                           # a word of the name finds it
    assert "Last noted in the shed on " in out
    assert "(last noted in the shed)" in tool.execute("list", location="shed")


def test_using_something_never_recorded_does_not_invent_it(db):
    out = tool.execute("use", "unobtainium", "a bit")
    assert "no unobtainium on record" in out
    assert inv.find_item("unobtainium") is None


def test_list_old_stock_only(db):
    inv.bought("bean seeds", "1 packet", when="2019-04-01")
    inv.bought("mulch", "2 bags")
    out = tool.execute("list", old_only=True)
    assert "bean seeds" in out and "mulch" not in out


def test_writes_are_events_so_the_history_explains_the_number(db):
    tool.execute("add", "mulch", "3 bags", "by the shed")
    tool.execute("use", "mulch", "a couple")
    with records_store._conn() as c:
        ops = [json.loads(r["attrs"])["op"] for r in c.execute("SELECT attrs FROM events WHERE kind='stock' ORDER BY id")]
    assert ops == ["bought", "used"]
    assert "about 1 bag of mulch" in tool.execute("find", "mulch")


def test_registered_and_its_writes_are_mutations():
    import tool_contract
    assert "inventory" in {s["function"]["name"] for s in tools.SCHEMAS}
    assert tool_contract.mutation("inventory", {"action": "add"})
    assert not tool_contract.mutation("inventory", {"action": "find"})


def test_a_year_alone_is_a_rough_date(db):
    assert inv.bought("tomato seeds", "some", location="a drawer", when="2021").startswith("Logged ")
    out = inv.lookup("tomato seeds")
    assert "dates from about 2021" in out and "Last noted in a drawer" in out
    assert inv.bought("x", "1", when="last spring").startswith("Error")
