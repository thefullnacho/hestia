"""Tool registry + dispatch for the Hestia agent loop.

Each tool module exposes a `SCHEMA` (OpenAI function-calling format) and an
`execute(**args) -> str`. `dispatch` runs a tool by name. New tools plug in here.

The `bash` tool was deliberately removed: Hestia is a home/records assistant, not a
sysadmin shell, and an unauthenticated brain with arbitrary shell access is a far bigger
liability than a denylist could safely contain. Every remaining tool is scoped and
non-arbitrary. Do not reintroduce a general shell tool.
"""
from __future__ import annotations

from . import home, media, memory_tool, records, reminder, search, skill, status, weather

# skill is NOT a model-facing tool — it's the deterministic router used by the brain to
# inject the matching skill's knowledge into a request's system prompt before the loop.
_TOOLS = {
    "home": home,
    "media": media,
    "memory": memory_tool,
    "records": records,
    "reminder": reminder,
    "search": search,
    "status": status,
    "weather": weather,
}

# OpenAI/Ollama tool schemas, in the order advertised to the model.
SCHEMAS = [m.SCHEMA for m in _TOOLS.values()]


def dispatch(name: str, args: dict) -> str:
    mod = _TOOLS.get(name)
    if mod is None:
        return f"Error: no such tool '{name}'."
    try:
        return mod.execute(**args)
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}"


def light_catalog() -> str:
    return home.catalog()


def soil_catalog() -> str:
    return home.soil_catalog()


def active_skill(user_text: str) -> str:
    """The matched skill's knowledge to inline for this request, or '' if none matches."""
    return skill.active_block(user_text)
