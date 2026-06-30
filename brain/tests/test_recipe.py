"""Recipe tool — the household collection's lookup/save/list round-trip, plus the matcher.

The tool reads its target dir from a module global (recipe.RECIPES_DIR) at call time, so
monkeypatching that global isolates the test from the real collection — the same override
pattern conftest uses for the DB and memory stores.
"""
from __future__ import annotations

import pytest

import tools.recipe as recipe


@pytest.fixture
def recipes(tmp_path, monkeypatch):
    """A fresh, empty recipe collection for this test."""
    path = tmp_path / "recipes"
    monkeypatch.setattr(recipe, "RECIPES_DIR", path)
    return recipe


def test_lookup_empty_collection_points_to_search(recipes):
    out = recipes.execute("lookup", name="banana bread")
    assert "not in the collection" in out.lower()
    assert "search" in out.lower()


def test_save_then_lookup_round_trips(recipes):
    body = "## Ingredients\n- 2 cups flour\n\n## Steps\n1. Mix.\n2. Bake at 350F for 1 hour."
    saved = recipes.execute("save", name="Banana Bread", content=body,
                            servings="1 loaf", aliases="banana loaf")
    assert "saved" in saved.lower()

    out = recipes.execute("lookup", name="banana bread")
    assert "Banana Bread" in out
    assert "1 loaf" in out          # servings surfaced from frontmatter
    assert "2 cups flour" in out    # the grounded quantity is in front of the model
    assert "Bake at 350F" in out


def test_lookup_matches_via_alias(recipes):
    recipes.execute("save", name="Banana Bread", content="## Steps\n1. Go.", aliases="banana loaf")
    out = recipes.execute("lookup", name="banana loaf")
    assert "Banana Bread" in out


def test_lookup_prefers_shorter_title_on_tie(recipes):
    recipes.execute("save", name="Banana Bread", content="## Steps\n1. Plain.")
    recipes.execute("save", name="Banana Bread French Toast Bake", content="## Steps\n1. Fancy.")
    out = recipes.execute("lookup", name="banana bread")
    assert "Saved recipe: Banana Bread\n" in out  # the exact two-word match, not the compound


def test_save_updates_existing(recipes):
    recipes.execute("save", name="Chili", content="## Steps\n1. v1.")
    again = recipes.execute("save", name="Chili", content="## Steps\n1. v2.")
    assert "updated" in again.lower()
    assert "v2" in recipes.execute("lookup", name="chili")
    # An update overwrites in place — one file, not two.
    assert len(list((recipes.RECIPES_DIR).glob("*.md"))) == 1


def test_list_names_saved_recipes(recipes):
    assert "no recipes saved yet" in recipes.execute("list").lower()
    recipes.execute("save", name="Chili", content="## Steps\n1. Go.")
    recipes.execute("save", name="Banana Bread", content="## Steps\n1. Go.")
    out = recipes.execute("list")
    assert "Banana Bread" in out and "Chili" in out


def test_save_requires_name_and_content(recipes):
    assert "called" in recipes.execute("save", name="", content="x").lower()
    assert "nothing to save" in recipes.execute("save", name="Soup", content="").lower()


def test_unknown_action(recipes):
    assert "unknown recipe action" in recipes.execute("frobnicate").lower()
