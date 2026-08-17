"""Tool registry + dispatch for the Hestia agent loop.

Each tool module exposes a `SCHEMA` (OpenAI function-calling format) and an
`execute(**args) -> str`. `dispatch` runs a tool by name. New tools plug in here.

`dispatch` never raises: an unknown tool, bad arguments, or *any* exception out of a
tool comes back as an error string for the model to relay. A tool that forgets to guard
itself cannot take down the chat turn.

The `bash` tool was deliberately removed: Hestia is a home/records assistant, not a
sysadmin shell, and an unauthenticated brain with arbitrary shell access is a far bigger
liability than a denylist could safely contain. Every remaining tool is scoped and
non-arbitrary. Do not reintroduce a general shell tool.
"""
from __future__ import annotations

import inspect

from . import home, media, memory_tool, records, recipe, reminder, search, shopping, skill, status, weather

# skill is NOT a model-facing tool — it's the deterministic router used by the brain to
# inject the matching skill's knowledge into a request's system prompt before the loop.
_TOOLS = {
    "home": home,
    "media": media,
    "memory": memory_tool,
    "records": records,
    "recipe": recipe,
    "reminder": reminder,
    "search": search,
    "shopping": shopping,
    "status": status,
    "weather": weather,
}

# OpenAI/Ollama tool schemas, in the order advertised to the model.
SCHEMAS = [m.SCHEMA for m in _TOOLS.values()]


def dispatch(name: str, args: dict) -> str:
    mod = _TOOLS.get(name)
    if mod is None:
        return f"Error: no such tool '{name}'."
    args = args or {}
    # Bind first, so "bad arguments" means exactly that. A TypeError raised *inside* the
    # tool is a bug, and reporting it as bad args just sends the model retrying with
    # permuted arguments.
    try:
        inspect.signature(mod.execute).bind(**args)
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}"
    try:
        return mod.execute(**args)
    except Exception as e:  # noqa: BLE001 — the loop expects a string back, never a raise
        return f"Error: {name} failed: {e}"


def light_catalog() -> str:
    return home.catalog()


def soil_catalog() -> str:
    return home.soil_catalog()


def active_skill(user_text: str) -> str:
    """The matched skill's knowledge to inline for this request, or '' if none matches."""
    return skill.active_block(user_text)
