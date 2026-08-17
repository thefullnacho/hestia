"""Shopping tool — item splitting/deduping and remove-matching, with HA stubbed by a
recorder. The list lives in HA (single source of truth); a duplicate inside one request
('milk, milk') must file one row, and substring removes only fire when unambiguous."""
from __future__ import annotations

import tools.shopping as shopping


class FakeHA:
    """Records service calls and keeps the item list, like HA's todo entity does."""

    def __init__(self, items=None):
        self.items = list(items or [])
        self.calls = []

    def svc(self, service, data, response=False):
        self.calls.append((service, data))
        if service == "get_items":
            return {"service_response": {shopping.ENTITY: {"items": list(self.items)}}}
        if service == "add_item":
            self.items.append({"summary": data["item"], "status": "needs_action",
                               "uid": f"uid-{data['item']}"})
        if service == "remove_item":
            want = data["item"]
            self.items = [i for i in self.items
                          if i.get("uid") != want and i["summary"] != want]
        return {}


def _ha(monkeypatch, items=None):
    ha = FakeHA(items)
    monkeypatch.setattr(shopping, "_svc", ha.svc)
    return ha


def test_duplicates_in_one_request_add_once(monkeypatch):
    ha = _ha(monkeypatch)
    out = shopping.execute("add", items="milk, milk and eggs")
    adds = [d["item"] for s, d in ha.calls if s == "add_item"]
    assert adds == ["milk", "eggs"]
    assert "Added to the shopping list: milk, eggs." in out


def test_add_skips_items_already_on_the_list(monkeypatch):
    ha = _ha(monkeypatch, [{"summary": "Milk", "status": "needs_action", "uid": "u1"}])
    out = shopping.execute("add", items="milk, eggs")
    adds = [d["item"] for s, d in ha.calls if s == "add_item"]
    assert adds == ["eggs"]
    assert "Already on it: milk" in out


def test_remove_unique_substring(monkeypatch):
    ha = _ha(monkeypatch, [{"summary": "whole milk", "status": "needs_action", "uid": "u1"},
                           {"summary": "eggs", "status": "needs_action", "uid": "u2"}])
    out = shopping.execute("remove", items="milk")
    removed = [d["item"] for s, d in ha.calls if s == "remove_item"]
    assert removed == ["u1"]
    assert "Took off the list: whole milk" in out


def test_remove_ambiguous_substring_refuses(monkeypatch):
    ha = _ha(monkeypatch, [{"summary": "whole milk", "status": "needs_action", "uid": "u1"},
                           {"summary": "oat milk", "status": "needs_action", "uid": "u2"}])
    out = shopping.execute("remove", items="milk")
    assert not [c for c in ha.calls if c[0] == "remove_item"]
    assert "Not on the list: milk" in out
