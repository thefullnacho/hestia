"""`inventory` tool: what the house has, how much, how old, and where it was last noted.

The model relays what the person said; code reads the amount, keeps the count, judges age from
a table and never invents a number (inventory_store.py, INVENTORY.md).
"""
from __future__ import annotations

import inventory_store as inv

SCHEMA = {
    "type": "function",
    "function": {
        "name": "inventory",
        "description": (
            "The household's own supplies: what the house has, how much, how old, and where it was "
            "last put. 'add' when something was bought or found ('we bought 2 bags of mulch', 'got "
            "some neem oil', 'found some tomato seeds from 2021 in a drawer': bought '2021', "
            "location 'a drawer'). Supplies found or bought are inventory, not notes. 'use' when some or all was used ('used half the fertilizer', 'used "
            "the last of the neem oil': amount 'the last'). 'count' when the person says how much "
            "is there now ('we have 3 jars left', 'none left'). 'move' when it was put somewhere "
            "('the neem oil is in the shed now'). 'find' for one item ('where's the neem oil', "
            "'do we have any twine'). 'list' for everything, or one place, or old stock only. "
            "Pass the amount exactly as the person said it ('a couple', 'some in a bag', '1.5 lb'); "
            "never turn a vague amount into a number. If an answer depends on how much there is and "
            "the record says only 'some', say so and ask; don't ask otherwise. Where something is: "
            "say where it was LAST NOTED and when, in the tool's own words ('last noted by the "
            "shed on Nov 5'); never say it IS there, things move."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "use", "count", "move", "find", "list"]},
                "item": {"type": "string", "description": "the item, as the person named it"},
                "amount": {"type": "string",
                           "description": "the amount verbatim: '2 bags', 'a couple', 'some', 'the last'"},
                "location": {"type": "string",
                             "description": "where it is, only if the person said ('the shed')"},
                "bought": {"type": "string",
                           "description": "add only: when it was bought, if said: YYYY-MM-DD, or just the year ('2021')"},
                "old_only": {"type": "boolean", "description": "list only: just old stock"},
            },
            "required": ["action"],
        },
    },
}


def execute(action: str, item: str | None = None, amount: str | None = None,
            location: str | None = None, bought: str | None = None, old_only: bool = False) -> str:
    need_item = action in ("add", "use", "count", "move", "find")
    if need_item and not (item or "").strip():
        return f"Error: '{action}' needs the item."
    if action == "add":
        return inv.bought(item, amount, location, bought)
    if action == "use":
        return inv.used(item, amount)
    if action == "count":
        if not (amount or "").strip():
            return "Error: 'count' needs how much is there, for example '3 bags' or 'none'."
        return inv.counted(item, amount)
    if action == "move":
        if not (location or "").strip():
            return "Error: 'move' needs where it is now."
        return inv.moved(item, location)
    if action == "find":
        return inv.lookup(item)
    if action == "list":
        return inv.overview(location, bool(old_only))
    return f"Error: unknown action '{action}'."
