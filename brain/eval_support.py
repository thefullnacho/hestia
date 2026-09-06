"""Synthetic fixtures and shared production entry points for safe local model evaluation."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import config

# Import the brain without loading deployment credentials. Evaluations only need Ollama.
with patch.object(config, "load_secrets"):
    import hestia
    import memory_store
    import records_store
    import tools


@contextmanager
def fixtures():
    """Each case gets fresh local stores; every external tool is intercepted."""
    with TemporaryDirectory(prefix="hestia-eval-") as tmp, ExitStack() as stack:
        root = Path(tmp)
        stack.enter_context(patch.object(config, "DATA_DIR", root))
        stack.enter_context(patch.object(records_store, "DB_PATH", root / "records.db"))
        stack.enter_context(patch.object(memory_store, "MEMORY_DIR", root / "memory"))
        stack.enter_context(patch.object(config, "ALMANAC_DIR", root / "almanac"))
        stack.enter_context(patch.object(tools.recipe, "RECIPES_DIR", root / "recipes"))
        # reminders_store uses records_store._conn, and shares its isolated database.
        records_store.upsert_entity("pet", "Biscuit", attrs={"breed": "corgi"})
        memory_store.write("The good coffee comes in the orange bag.", type="preference")
        state = {"light": "on", "shopping": [], "calls": []}
        light = "light.light_kitchen_lights (Kitchen lights)=on; light.light_outside_lights (Outside lights)=off"
        soil = "Carrot bed=25%"

        async def catalogs(*, lights=False, soil=False):
            return (light.replace("Kitchen lights)=on", f"Kitchen lights)={state['light']}") if lights else "", "Carrot bed=25%" if soil else "")

        original = tools.dispatch

        def dispatch(name, args):
            state["calls"].append({"name": name, "args": args})
            if name in {"records", "memory", "reminder", "recipe"}:
                return original(name, args)
            if name == "home":
                if args.get("entity_id") not in (None, "light.light_kitchen_lights"):
                    return "Error: unknown fixture light."
                action = args.get("action")
                if action in ("turn_on", "turn_off"):
                    state["light"] = "on" if action == "turn_on" else "off"
                return f"Done - Kitchen lights are {state['light']}."
            if name == "shopping":
                if args.get("action") == "add":
                    state["shopping"].extend(tools.shopping._split(args.get("items", "")))
                return "Shopping list: " + ", ".join(state["shopping"])
            if name == "weather":
                return "Forecast fixture: no rain in the next 24 hours."
            return f"Error: {name} is disabled in this isolated evaluation."

        stack.enter_context(patch.object(tools, "dispatch", dispatch))
        stack.enter_context(patch.object(tools.home, "context_catalogs", catalogs))
        stack.enter_context(patch.object(tools, "soil_catalog", lambda: soil))
        stack.enter_context(patch.object(tools, "light_catalog", lambda: light))
        yield state


async def first_message(model: str, prompt: str):
    """Selection probes use production prompt construction and generation settings."""
    trace = hestia.TurnTrace(model=model, think=False)
    token = hestia._trace.set(trace)
    prepared_token = hestia._prepared.set(None)
    try:
        system = await hestia._build_system_prompt(prompt)
        schemas = hestia._request_schemas(prompt)
        msg = await hestia._ollama_chat([
            {"role": "system", "content": system}, {"role": "user", "content": prompt}], schemas)
        return msg, len(schemas)
    finally:
        hestia._trace.reset(token)
        hestia._prepared.reset(prepared_token)
