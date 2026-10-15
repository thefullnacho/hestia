"""Turn tools.json's shorthand parameters into JSON Schema.

tools.json keeps parameters readable for review ("on | off", "integer 0-100 (optional)").
Models and chat templates want JSON Schema, so this is the one place that translates.
"""
import json
import re
from pathlib import Path

TOOLS = Path(__file__).with_name("tools.json")


def param_schema(spec: str) -> tuple[dict, bool]:
    """Return (schema, required) for one shorthand spec."""
    optional = "(optional)" in spec or "only with" in spec
    spec = spec.replace("(optional)", "").strip().rstrip(",").strip()
    if spec.startswith("array of strings"):
        return {"type": "array", "items": {"type": "string"}}, not optional
    for kind in ("integer", "string"):
        if spec.startswith(kind):
            rest = spec[len(kind):].lstrip(" ,").strip()
            out = {"type": kind}
            if rest:
                out["description"] = rest
            return out, not optional
    if "|" in spec:
        return {"type": "string", "enum": [v.strip() for v in spec.split("|")]}, not optional
    raise ValueError(f"unrecognised parameter shorthand: {spec!r}")


def tool_schema(tool: dict) -> dict:
    props, required = {}, []
    for name, spec in tool["parameters"].items():
        props[name], req = param_schema(spec)
        if req:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def load(path: Path = TOOLS) -> dict[str, list[dict]]:
    """Domain name -> list of OpenAI-style tool schemas."""
    data = json.loads(path.read_text())
    return {dom: [tool_schema(t) for t in tools] for dom, tools in data["domains"].items()}


def all_tools(path: Path = TOOLS) -> list[dict]:
    return [t for tools in load(path).values() for t in tools]


if __name__ == "__main__":
    print(json.dumps(all_tools(), indent=1))
