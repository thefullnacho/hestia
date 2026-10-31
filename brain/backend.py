"""The brain's model calls: one contract, two wire formats.

chat() sends a conversation and the offered tool schemas to the configured backend and returns
{"role", "content", "tool_calls"} with arguments as dicts. Ollama's native /api/chat is the
default; HESTIA_BACKEND=openai speaks OpenAI-compatible /v1/chat/completions instead (llama-server,
mlx_lm.server). The agent loop in hestia.py owns everything else: prompts, tools, guards.
"""
from __future__ import annotations

import json
import os
from contextvars import ContextVar

import httpx

import tools

OLLAMA = os.environ.get("HESTIA_OLLAMA", "http://127.0.0.1:11434")
MODEL = os.environ.get("HESTIA_MODEL", "gemma4:12b")
# Model backend: "ollama" (native /api/chat) or "openai" (OpenAI-compatible
# /v1/chat/completions, e.g. mlx_lm.server on the tailnet MacBook).
BACKEND = os.environ.get("HESTIA_BACKEND", "ollama")
# Normalized: request paths carry the full "/v1/chat/completions", so a "/v1"
# suffix here would double up (httpx merges base + request paths).
_OPENAI_RAW = os.environ.get("HESTIA_OPENAI_BASE", "http://127.0.0.1:8080/v1").rstrip("/")
OPENAI_BASE = _OPENAI_RAW[:-3] if _OPENAI_RAW.endswith("/v1") else _OPENAI_RAW
OPENAI_KEY = os.environ.get("HESTIA_OPENAI_KEY", "not-needed")
OPENAI_MODEL = os.environ.get("HESTIA_OPENAI_MODEL", MODEL)
# Explicit, not left to each model's undocumented Ollama default — some GGUFs default far
# below what the real system prompt + tool surface needs (~5.7k tokens worst case) and
# silently 400 instead of truncating. See brain/eval_keymatch.py's num_ctx fix.
NUM_CTX = int(os.environ.get("HESTIA_NUM_CTX", "32768"))
# qwen3 thinking mode: off by default (fast, no eval gain). HESTIA_THINK=1 to enable.
THINK = os.environ.get("HESTIA_THINK", "0") not in ("0", "", "false", "False")
OUTPUT_TOKENS = int(os.environ.get("HESTIA_OUTPUT_TOKENS", "768"))

# The turn in progress (hestia.TurnTrace) and, for a streaming client, where text goes. Set by
# the agent loop; read here for the model name, the repair tool, streaming and token counts.
turn_trace: ContextVar[object] = ContextVar("turn_trace", default=None)
stream_sink: ContextVar[object] = ContextVar("stream_sink", default=None)

client = httpx.AsyncClient(base_url=OLLAMA, timeout=httpx.Timeout(300.0, connect=10.0))
oai_client = httpx.AsyncClient(
    base_url=OPENAI_BASE,
    headers={"Authorization": f"Bearer {OPENAI_KEY}"},
    timeout=httpx.Timeout(300.0, connect=10.0),
)


def openai_messages(messages: list[dict]) -> list[dict]:
    """The conversation in the OpenAI shape. The loop keeps Ollama's (a tool call without a type
    or id, its result keyed by tool_name), which a strict server refuses on the second model call
    of a turn: llama-server answers 500 "Missing tool call type". Each call gets an id here, and
    each tool result the id of the call it answers, in order."""
    out, pending, n = [], [], 0
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function", {})
                args = fn.get("arguments", {})
                n += 1
                cid = c.get("id") or f"call_{n}"
                calls.append({"id": cid, "type": "function", "function": {
                    "name": fn.get("name", ""),
                    "arguments": args if isinstance(args, str) else json.dumps(args)}})
            pending = [c["id"] for c in calls]
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif m.get("role") == "tool":
            cid = m.get("tool_call_id") or (pending.pop(0) if pending else f"call_{n}")
            out.append({"role": "tool", "tool_call_id": cid, "content": m.get("content", "")})
        else:
            out.append(m)
    return out


async def openai_chat(messages: list[dict], schemas: list | None = None) -> dict:
    """OpenAI-compatible backend (POST /v1/chat/completions), e.g. mlx_lm.server.

    Same contract as ollama_chat: returns {"role","content","tool_calls"} with
    arguments as dicts. Ollama-only fields (think, options/num_ctx, format) are
    dropped; the repair prompt text is unchanged. Selected by HESTIA_BACKEND=openai.
    """
    trace = turn_trace.get()
    body = {"model": OPENAI_MODEL, "messages": openai_messages(messages),
            "tools": tools.SCHEMAS if schemas is None else schemas,
            "stream": False, "temperature": 0.3, "max_tokens": OUTPUT_TOKENS}
    repair = trace.repair_tool if trace else ''
    if trace:
        trace.repair_tool = ''
    schema = next((s['function']['parameters'] for s in body['tools']
                   if s['function']['name'] == repair), None) if repair else None
    if schema is None:
        repair = ''   # never repair through a tool this request did not offer
    if repair:
        body['tools'] = []
        body['temperature'] = 0
        body['messages'] = [*body['messages'], {'role': 'system', 'content':
            f'Return only the JSON argument object for the {repair} tool, matching the supplied schema. '
            'The harness will validate it before execution. Do not claim completion. '
            'Omit optional fields the user did not specify, especially location and timestamps. '
            'For events today or now, omit ts so the datastore supplies its own clock. '
            'Choose the event kind from its meaning (health, chore, sighting, or note). '
            'Argument schema: ' + json.dumps(schema)}]
    sink = stream_sink.get()
    # Only a final synthesis with tools disabled can emit user-visible text safely.
    if sink and not repair and not body['tools'] and not (trace and trace.actions):
        body['stream'] = True
        content = []
        async with oai_client.stream('POST', '/v1/chat/completions', json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                delta = ((event.get('choices') or [{}])[0].get('delta') or {}).get('content', '')
                if delta:
                    content.append(delta)
                    await sink(delta)
                    if trace:
                        trace.emitted = True
                        trace.streamed_text += delta
        message = {'role': 'assistant', 'content': ''.join(content)}
    else:
        r = await oai_client.post("/v1/chat/completions", json=body)
        r.raise_for_status()
        payload = r.json()
        oai_msg = (payload.get('choices') or [{}])[0].get('message') or {}
        message = {'role': oai_msg.get('role', 'assistant'),
                   'content': oai_msg.get('content') or ''}
        norm_calls = []
        for tc in oai_msg.get('tool_calls') or []:
            fn = tc.get('function') or {}
            args = fn.get('arguments')
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    pass  # leave the raw string; downstream validation rejects it safely
            norm_calls.append({'function': {'name': fn.get('name'), 'arguments': args}})
        if norm_calls:
            message['tool_calls'] = norm_calls
        if trace:
            usage = payload.get('usage') or {}
            trace.prompt_tokens += usage.get('prompt_tokens', 0)
            trace.completion_tokens += usage.get('completion_tokens', 0)
    if trace:
        trace.model_calls += 1
    if repair:
        try:
            args = json.loads(message.get('content', ''))
        except (ValueError, TypeError):
            return {'content': ''}
        return {'content': '', 'tool_calls': [{'function': {'name': repair, 'arguments': args}}]}
    return message


async def chat(messages: list[dict], schemas: list | None = None) -> dict:
    """Backend dispatch: Ollama native or OpenAI-compatible. Same contract."""
    if BACKEND == "openai":
        return await openai_chat(messages, schemas)
    return await ollama_chat(messages, schemas)


async def ollama_chat(messages: list[dict], schemas: list | None = None) -> dict:
    # think=False keeps qwen3 in fast mode (thinking on cost ~4s/turn for no eval gain;
    # see brain/eval_models.py — qwen3:14b no-think scored 100%/100% English at 1.5s).
    trace = turn_trace.get()
    body = {"model": trace.model if trace else MODEL, "messages": messages,
            "tools": tools.SCHEMAS if schemas is None else schemas,
            "stream": False, "think": trace.think if trace else THINK,
            "options": {"temperature": 0.3, "num_ctx": NUM_CTX, "num_predict": OUTPUT_TOKENS}}
    repair = trace.repair_tool if trace else ''
    if trace:
        trace.repair_tool = ''
    schema = next((s['function']['parameters'] for s in body['tools']
                   if s['function']['name'] == repair), None) if repair else None
    if schema is None:
        repair = ''   # never repair through a tool this request did not offer
    if repair:
        body['tools'] = []
        body['format'] = schema
        body['options']['temperature'] = 0
        body['messages'] = [*messages, {'role': 'system', 'content':
            f'Return only the JSON argument object for the {repair} tool, matching the supplied schema. '
            'The harness will validate it before execution. Do not claim completion. '
            'Omit optional fields the user did not specify, especially location and timestamps. '
            'For events today or now, omit ts so the datastore supplies its own clock. '
            'Choose the event kind from its meaning (health, chore, sighting, or note). '
            'Argument schema: ' + json.dumps(schema)}]
    sink = stream_sink.get()
    # Only a final synthesis with tools disabled can emit user-visible text safely.
    # Tool-selection rounds stay buffered, as a late tool call may change the outcome.
    if sink and not repair and not body['tools'] and not (trace and trace.actions):
        body['stream'] = True
        content, payload = [], {}
        async with client.stream('POST', '/api/chat', json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event.get('error'):
                    raise RuntimeError('Model streaming failed')
                delta = event.get('message', {}).get('content', '')
                if delta:
                    content.append(delta)
                    await sink(delta)
                    if trace:
                        trace.emitted = True
                        trace.streamed_text += delta
                if event.get('done'):
                    payload = event
        if not payload.get('done'):
            raise RuntimeError('Model stream ended before completion')
        payload['message'] = {'role': 'assistant', 'content': ''.join(content)}
    else:
        r = await client.post("/api/chat", json=body)
        r.raise_for_status()
        payload = r.json()
    if trace:
        trace.model_calls += 1
        trace.prompt_tokens += payload.get("prompt_eval_count", 0)
        trace.completion_tokens += payload.get("eval_count", 0)
        for key in ("load_duration", "prompt_eval_duration", "eval_duration", "total_duration"):
            trace.backend[key] = trace.backend.get(key, 0) + payload.get(key, 0)
    if repair:
        try:
            args = json.loads(payload['message'].get('content', ''))
        except (ValueError, TypeError):
            return {'content': ''}
        return {'content': '', 'tool_calls': [{'function': {'name': repair, 'arguments': args}}]}
    return payload["message"]
