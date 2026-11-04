"""chat_format: the box renders the phone's prompt and reads its tool calls."""
import json
from pathlib import Path

import pytest

import chat_format

TOOLS = [{"type": "function", "function": {"name": "records", "description": "Log <things>, 'quoted'",
          "parameters": {"type": "object", "properties": {
              "action": {"type": "string"}, "qty": {"type": ["number", "string"]},
              "count": {"type": "integer"}, "done": {"type": "boolean"}, "items": {"type": "array"}}}}}]
LFM3 = Path("/home/alex/models/lfm3/chat_template.jinja")


def call(name, **params):
    body = "".join(f"<parameter={k}>\n{v}\n</parameter>\n" for k, v in params.items())
    return f"<tool_call>\n<function={name}>\n{body}</function>\n</tool_call>"


def test_values_take_the_type_their_schema_allows():
    out = chat_format.parse(call("records", action="harvest", qty="6", count="3", done="true",
                                 items='["a", "b"]'), TOOLS)
    assert out["tool_calls"] == [{"function": {"name": "records", "arguments": {
        "action": "harvest", "qty": 6, "count": 3, "done": True, "items": ["a", "b"]}}}]


def test_text_stays_text_where_the_schema_wants_text_or_the_value_does_not_parse():
    out = chat_format.parse(call("records", action="42", qty="2 lb 7 oz", count="three"), TOOLS)
    assert out["tool_calls"][0]["function"]["arguments"] == {"action": "42", "qty": "2 lb 7 oz", "count": "three"}


def test_two_calls_and_the_words_around_them():
    out = chat_format.parse("On it.\n" + call("records", action="a") + "\n\n" + call("records", action="b"), TOOLS)
    assert out["content"] == "On it." and [c["function"]["arguments"]["action"] for c in out["tool_calls"]] == ["a", "b"]


def test_a_call_cut_off_before_its_end_is_not_a_call():
    out = chat_format.parse("<tool_call>\n<function=records>\n<parameter=action>\nhar", TOOLS)
    assert "tool_calls" not in out


def test_thinking_that_slips_through_is_dropped():
    assert chat_format.parse("<think>hmm</think>Lights are off.", TOOLS) == {"role": "assistant", "content": "Lights are off."}


def test_tojson_matches_llama_cpp():
    tpl = "{{ tools | tojson }}"
    assert chat_format.render([], [{"a": "é<'"}], tpl) == json.dumps([{"a": "é<'"}], ensure_ascii=False)


def test_the_generation_block_renders_its_body():
    tpl = "{%- for m in messages -%}{%- generation -%}[{{ m.content }}]{%- endgeneration -%}{%- endfor -%}"
    assert chat_format.render([{"role": "user", "content": "hi"}], [], tpl) == "[hi]"


@pytest.mark.skipif(not LFM3.exists(), reason="the LFM3 template is not on this machine")
def test_lfm3_renders_a_turn_with_a_call_and_its_result():
    template = LFM3.read_text()
    messages = [{"role": "system", "content": "You are Hestia."}, {"role": "user", "content": "log it"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "records", "arguments": {"action": "harvest", "qty": 6}}}]},
                {"role": "tool", "content": "Logged."}]
    prompt = chat_format.render(messages, TOOLS, template, bos_token="<|startoftext|>")
    assert prompt.startswith("<|startoftext|><|im_start|>system\nYou are Hestia.\n\n<tools>\n")
    assert "<parameter=qty>\n6\n</parameter>" in prompt
    assert "<tool_response>\nLogged.\n</tool_response><|im_end|>" in prompt
    assert prompt.endswith("<|im_start|>assistant\n<think></think>")
    parsed = chat_format.parse(call("records", action="harvest", qty="6"), TOOLS)
    assert parsed["tool_calls"] == messages[2]["tool_calls"]
