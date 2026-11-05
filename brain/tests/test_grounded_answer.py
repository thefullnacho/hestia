"""The grounded answer: library passages joined with what the house has on hand, in code.

"How do I treat pests naturally on garlic?" should come back with the extension's passages AND
the neem oil in the garage, with its age and where it was last noted, so the model plans from
the shelf. Pinned: the join is strict (every word of the item's name in the passages), used-up
stock is not offered, old stock carries its note, and a house with no inventory gets nothing extra.
"""
from __future__ import annotations

import pytest

import inventory_store as inv
import library_store as lib
from tools import library as tool

GARLIC = ("Thrips and onion maggots are the main insect pests of garlic. For a natural approach, "
          "cover the bed with floating row covers after planting, and spray neem oil or insecticidal "
          "soap on thrips in the evening, repeating every seven days. Diatomaceous earth around the "
          "base deters slugs. ") * 2


def fake_embed(texts):
    return [[1.0, 0.0] for _ in texts]


@pytest.fixture
def shelf(db, monkeypatch):
    monkeypatch.setattr(lib, "embed", fake_embed)
    lib._vectors.clear()
    lib.index_doc("garlic.pdf", [GARLIC], "Growing Garlic in the Home Garden | Ext")
    lib.embed_missing()
    monkeypatch.setattr(tool, "FLOOR", -1.0)      # the fake embedder scores everything alike


def test_what_the_passages_name_and_the_house_has_comes_with_the_answer(shelf):
    inv.bought("Neem Oil 16 oz", "1 bottle", location="the garage", when="2022-04-01")
    inv.bought("garden twine", "2 spools")
    inv.bought("Insect Killing Soap", "1 bottle")            # not "insecticidal soap": no match
    out = tool.execute("how do I treat pests naturally on garlic")
    assert "Growing Garlic in the Home Garden" in out
    on_hand = out.split(tool.ON_HAND_HEAD)[1]
    assert "1 bottle of Neem Oil 16 oz" in on_hand and "Last noted in the garage" in on_hand
    assert "Old stock" in on_hand and "garden chemical" in on_hand
    assert "twine" not in on_hand and "Insect Killing" not in on_hand


def test_used_up_stock_is_not_offered_and_the_answer_says_what_to_get(shelf):
    inv.bought("neem oil", "1 bottle")
    inv.used("neem oil", "the last")
    out = tool.execute("how do I treat pests naturally on garlic")
    assert tool.ON_HAND_HEAD not in out and tool.NONE_ON_HAND in out


def test_plural_names_match_singular_passages(shelf):
    inv.bought("row covers", "2")
    assert "2 row covers" in tool.execute("garlic pests")


def test_a_house_with_no_inventory_gets_only_the_passages(shelf):
    out = tool.execute("how do I treat pests naturally on garlic")
    assert tool.ON_HAND_HEAD not in out and tool.NONE_ON_HAND not in out


def test_a_short_one_word_item_never_matches_by_accident(shelf):
    inv.bought("oil", "1 can")                                # "oil" is in the passage, but too generic
    assert tool.ON_HAND_HEAD not in tool.execute("garlic pests")
