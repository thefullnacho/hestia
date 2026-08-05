"""`shopping` tool — the household shopping list, backed by Home Assistant's todo list.

"Add milk to the list" files a row in HA (`todo.shopping_list`), so the same list shows
in the HA app/dashboard and survives brain restarts. The model never holds the list —
it only relays adds/removes; HA is the single source of truth. Item splitting is done
here (commas / "and"), not by the model, so multi-item requests land deterministically.
"""
from __future__ import annotations

import os
import re

import httpx

HA_URL = os.environ.get("HA_URL", "http://hl-relay:8124").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
ENTITY = os.environ.get("HESTIA_SHOPPING_ENTITY", "todo.shopping_list")
_HDRS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "shopping",
        "description": ("The household shopping/grocery list (shared, lives in Home Assistant). "
                        "'add' puts items on it — pass the items exactly as the user said them, "
                        "several at once is fine ('milk, eggs and dog food'); the tool splits "
                        "them itself. 'remove' takes items off (use it when something was bought "
                        "or isn't needed: 'got the milk', 'take eggs off the list'). 'show' reads "
                        "the list back. 'clear' empties it. Use this for ANY mention of needing, "
                        "buying, or being out of a grocery/household item — 'we're out of X' and "
                        "'we need X' mean add X. Never track the list in memory or records."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "remove", "show", "clear"]},
                "items": {"type": "string",
                          "description": "for add/remove: the item(s), verbatim — e.g. 'milk' or 'milk, eggs and dog food'"},
            },
            "required": ["action"],
        },
    },
}

# "milk, eggs and dog food" -> ["milk", "eggs", "dog food"]. Split on commas and the word
# "and" — item names with a real "and" in them ("salt and vinegar chips") are rare enough
# that a comma-separated correction beats making the model do the splitting.
_SPLIT_RE = re.compile(r",|\band\b", re.I)


def _split(items: str) -> list[str]:
    return [p.strip() for p in _SPLIT_RE.split(items) if p.strip()]


def _svc(service: str, data: dict, response: bool = False) -> dict:
    url = f"{HA_URL}/api/services/todo/{service}"
    if response:
        url += "?return_response"
    r = httpx.post(url, headers=_HDRS, json={"entity_id": ENTITY, **data}, timeout=10)
    r.raise_for_status()
    return r.json() if response else {}


def _items() -> list[dict]:
    resp = _svc("get_items", {}, response=True)
    return (resp.get("service_response", {}).get(ENTITY, {}) or {}).get("items", [])


def _fmt(items: list[dict]) -> str:
    open_items = [i["summary"] for i in items if i.get("status") != "completed"]
    if not open_items:
        return "The shopping list is empty."
    return (f"Shopping list ({len(open_items)}): " + ", ".join(open_items) + ".")


def _recently_grown(added: list[str]) -> str:
    """Mention anything just added to the list that we picked out of the garden lately.

    Advisory only — the item still goes on the list. The point is that a house that knows
    about Bed 4 shouldn't let you buy tomatoes the week you pulled four pounds of them. Never
    raises and never blocks the add: a records hiccup must not break the shopping path."""
    notes = []
    try:
        import records_store as store
        for item in added:
            hit = store.harvested_recently(item, days=10)
            if not hit:
                continue
            amt = (f"{hit['total_lb']} lb" if hit["unit_class"] == "weight"
                   else f"{hit['total_qty']:g}")
            when = ("today" if hit["days_ago"] == 0 else
                    "yesterday" if hit["days_ago"] == 1 else f"{hit['days_ago']} days ago")
            where = f" from {hit['bed']}" if hit["bed"] else ""
            notes.append(f"{item} — you picked {amt}{where} ({when})")
    except Exception:  # noqa: BLE001 — a nicety, never a failure mode
        return ""
    if not notes:
        return ""
    return " Heads up: " + "; ".join(notes) + "."


def execute(action: str, items: str | None = None) -> str:
    try:
        if action == "show":
            return _fmt(_items())

        if action == "clear":
            n = 0
            for it in _items():
                _svc("remove_item", {"item": it.get("uid") or it["summary"]})
                n += 1
            return f"Cleared the shopping list ({n} item{'s' if n != 1 else ''} removed)."

        if action == "add":
            if not items:
                return "Error: 'add' needs items."
            wanted = _split(items)
            have = {i["summary"].lower() for i in _items()}
            added, dupes = [], []
            for w in wanted:
                (dupes if w.lower() in have else added).append(w)
                if w.lower() not in have:
                    _svc("add_item", {"item": w})
            msg = f"Added to the shopping list: {', '.join(added)}." if added else ""
            if dupes:
                msg += f" Already on it: {', '.join(dupes)}."
            grown = _recently_grown(added)
            return (msg.strip() or "Nothing to add.") + grown + f" {_fmt(_items())}"

        if action == "remove":
            if not items:
                return "Error: 'remove' needs items."
            current = [i for i in _items()]
            removed, missing = [], []
            for w in _split(items):
                # exact (case-insensitive) first, then unique substring — "milk" should
                # take off "whole milk" when that's the only milk on the list.
                exact = [i for i in current if i["summary"].lower() == w.lower()]
                subs = [i for i in current if w.lower() in i["summary"].lower()]
                hit = exact[0] if exact else (subs[0] if len(subs) == 1 else None)
                if hit is None:
                    missing.append(w)
                    continue
                _svc("remove_item", {"item": hit.get("uid") or hit["summary"]})
                current.remove(hit)
                removed.append(hit["summary"])
            msg = f"Took off the list: {', '.join(removed)}." if removed else ""
            if missing:
                msg += f" Not on the list: {', '.join(missing)}."
            return (msg.strip() or "Nothing removed.") + f" {_fmt(current)}"

        return f"Error: unknown action '{action}' (use add, remove, show, or clear)."
    except httpx.HTTPError as e:
        return f"Shopping list backend error (Home Assistant): {e}"
