"""Prompt text in and tool calls out, for a model the box drives as plain text completion.

The phone runs the model on a string and streams tokens back; it never sees messages or tools
(PHONE-APP.md, "Who renders the prompt"). So the box does both halves llama-server does for an
OpenAI-style request: render() turns messages and tools into the model's own prompt text with
its chat template, and parse() reads the tool calls back out of what the model wrote. One
harness, on the box, with nothing on the phone that can drift from it.

The template comes with the model (a GGUF carries it), so render() takes it as text. It is run
the way llama.cpp's own template engine runs it, so the prompt matches llama-server's byte for
byte (benchmarks/phone-render/ checks that). The parser reads the XML tool-call shape LFM3 and
Qwen3-Coder write:

    <tool_call>
    <function=home>
    <parameter=action>
    turn_off
    </parameter>
    </function>
    </tool_call>
"""
from __future__ import annotations

import json
import re

from jinja2.exceptions import TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment


def _tojson(value, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
    """llama.cpp's tojson: Python's json.dumps defaults, but non-ASCII kept as is."""
    return json.dumps(value, ensure_ascii=ensure_ascii, indent=indent, separators=separators,
                      sort_keys=sort_keys)


def _raise(message):
    raise TemplateError(message)


# The template's {% generation %} block only marks assistant text for training; it renders its
# body unchanged, so it becomes a plain if-true block with the same whitespace control.
_GENERATION = re.compile(r"\{%(-?)\s*(end)?generation\s*(-?)%\}")


def _compile(template: str):
    source = _GENERATION.sub(lambda m: "{%" + m[1] + (" endif " if m[2] else " if true ") + m[3] + "%}", template)
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=False)
    env.filters["tojson"] = _tojson
    env.globals["raise_exception"] = _raise
    return env.from_string(source)


_compiled: dict[str, object] = {}


def render(messages: list[dict], tools: list[dict], template: str, *, bos_token: str = "",
           eos_token: str = "", thinking: bool = False) -> str:
    """The prompt text for the next assistant turn. Tool-call arguments must be objects, not JSON
    strings (the brain's loop keeps them as objects). Thinking stays off: it measured worse here
    (benchmarks/RESULTS.md)."""
    if template not in _compiled:
        _compiled[template] = _compile(template)
    return _compiled[template].render(messages=messages, tools=tools or None, bos_token=bos_token,
                                      eos_token=eos_token, add_generation_prompt=True,
                                      enable_thinking=thinking)


_CALL = re.compile(r"<tool_call>\s*<function=([^>\s]+)>(.*?)</function>\s*</tool_call>", re.S)
_PARAM = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)


_JSON_TYPES = {"number": (int, float), "integer": (int,), "boolean": (bool,), "object": (dict,),
               "array": (list,), "null": (type(None),)}


def _typed(raw: str, schema: dict):
    """A parameter's text as the type its schema allows, as llama.cpp reads it: a value parses as
    JSON when the schema allows a non-string type it matches ("6" for number-or-string is 6);
    otherwise it stays as written, for the loop's validation to judge."""
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    if not kinds or kinds == ["string"]:
        return raw
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    allowed = tuple(t for k in kinds for t in _JSON_TYPES.get(k, ()))
    if isinstance(value, bool) and bool not in allowed:
        return raw
    return value if isinstance(value, allowed) else raw


def parse(text: str, tools: list[dict]) -> dict:
    """{"content", "tool_calls"} from what the model wrote, in the loop's own shape: each call is
    {"function": {"name", "arguments"}} with arguments as an object. Thinking, if any slipped
    through, is dropped from the content."""
    params = {t["function"]["name"]: t["function"].get("parameters", {}).get("properties", {})
              for t in tools or []}
    calls = []
    for name, body in _CALL.findall(text):
        props = params.get(name, {})
        args = {key: _typed(value, props.get(key, {})) for key, value in _PARAM.findall(body)}
        calls.append({"function": {"name": name, "arguments": args}})
    content = _CALL.sub("", text)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    out = {"role": "assistant", "content": content}
    if calls:
        out["tool_calls"] = calls
    return out
