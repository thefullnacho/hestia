"""The brain's model calls: one door, two wire formats.

chat() sends a conversation and the offered tool schemas to the configured backend and returns
{"role", "content", "tool_calls"} with arguments as dicts. Ollama's native /api/chat is the
default; HESTIA_BACKEND=openai speaks OpenAI-compatible /v1/chat/completions instead (llama-server,
mlx_lm.server). complete() and acomplete() are the same door for text with no tools: the timed
jobs, the note-taker and the recipe organizer. Nothing else in the brain talks to a model server.
The agent loop in hestia.py owns everything else: prompts, tools, guards.
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
# "completion": the box renders the prompt with the model's chat template and sends plain text
# to a llama.cpp-style /completion endpoint, then reads the tool calls back out (chat_format.py).
# The phone backend does the same over its socket; this runs it against a llama-server on the
# box so the phone's exact path can be measured before the phone exists.
COMPLETION_BASE = os.environ.get("HESTIA_COMPLETION_BASE", "http://127.0.0.1:8080").rstrip("/")
CHAT_TEMPLATE = os.environ.get("HESTIA_CHAT_TEMPLATE", "")   # path to the model's chat template
BOS_TOKEN = os.environ.get("HESTIA_BOS_TOKEN", "")
EOS_TOKEN = os.environ.get("HESTIA_EOS_TOKEN", "<|im_end|>")

# The turn in progress (hestia.TurnTrace) and, for a streaming client, where text goes. Set by
# the agent loop; read here for the model name, the repair tool, streaming and token counts.
turn_trace: ContextVar[object] = ContextVar("turn_trace", default=None)
stream_sink: ContextVar[object] = ContextVar("stream_sink", default=None)

client = httpx.AsyncClient(base_url=OLLAMA, timeout=httpx.Timeout(300.0, connect=10.0))
completion_client = httpx.AsyncClient(base_url=COMPLETION_BASE, timeout=httpx.Timeout(300.0, connect=10.0))
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
    if BACKEND in ("completion", "phone"):
        return await text_chat(messages, schemas)
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


def deferred() -> bool:
    """True when the model lives on a phone that may not be connected (HESTIA_BACKEND=phone).
    Jobs then queue their model step in model_jobs instead of calling complete(); chat turns and
    the recipe organizer reach the phone directly, since someone is in the app for those."""
    return BACKEND == "phone"



_template_text: dict[str, str] = {}


def chat_template() -> str:
    """The model's chat template, read once from HESTIA_CHAT_TEMPLATE."""
    if not CHAT_TEMPLATE:
        raise RuntimeError("HESTIA_CHAT_TEMPLATE is not set: the completion backend needs the model's template")
    if CHAT_TEMPLATE not in _template_text:
        with open(CHAT_TEMPLATE, encoding="utf-8") as f:
            _template_text[CHAT_TEMPLATE] = f.read()
    return _template_text[CHAT_TEMPLATE]


async def text_chat(messages: list[dict], schemas: list | None = None) -> dict:
    """The phone's path, run against a llama.cpp /completion endpoint: render, generate, parse.
    Same contract as ollama_chat. A repair asks for the argument object under a JSON schema."""
    import chat_format
    trace = turn_trace.get()
    offered = tools.SCHEMAS if schemas is None else schemas
    repair = trace.repair_tool if trace else ''
    if trace:
        trace.repair_tool = ''
    schema = next((s['function']['parameters'] for s in offered
                   if s['function']['name'] == repair), None) if repair else None
    body = {"n_predict": OUTPUT_TOKENS, "temperature": 0.3, "stop": [EOS_TOKEN], "cache_prompt": True}
    if schema is not None:
        convo = [*messages, {'role': 'system', 'content':
            f'Return only the JSON argument object for the {repair} tool, matching the supplied schema. '
            'The harness will validate it before execution. Do not claim completion. '
            'Omit optional fields the user did not specify, especially location and timestamps. '
            'For events today or now, omit ts so the datastore supplies its own clock. '
            'Choose the event kind from its meaning (health, chore, sighting, or note). '
            'Argument schema: ' + json.dumps(schema)}]
        body.update(prompt=chat_format.render(convo, [], chat_template(), bos_token=BOS_TOKEN, eos_token=EOS_TOKEN),
                    temperature=0, json_schema=schema)
    else:
        body["prompt"] = chat_format.render(messages, offered, chat_template(), bos_token=BOS_TOKEN,
                                            eos_token=EOS_TOKEN)
    sink = stream_sink.get()
    # Only a final synthesis with tools disabled can emit user-visible text safely.
    if not (sink and schema is None and not offered and not (trace and trace.actions)):
        sink = None
    payload = await _send_text(body, sink)
    if sink and trace:
        trace.emitted = True
        trace.streamed_text += payload.get("content") or ""
    if trace:
        trace.model_calls += 1
        trace.prompt_tokens += payload.get("tokens_evaluated", 0)
        trace.completion_tokens += payload.get("tokens_predicted", 0)
    text = payload.get("content") or ""
    if schema is not None:
        try:
            return {'content': '', 'tool_calls': [{'function': {'name': repair, 'arguments': json.loads(text)}}]}
        except (ValueError, TypeError):
            return {'content': ''}
    return chat_format.parse(text, offered)


async def _send_text(body: dict, sink=None) -> dict:
    """One /completion-shaped request, to the phone over its socket or to a llama.cpp server."""
    if BACKEND == "phone":
        import phone
        return await phone.hub.complete(body, sink)
    r = await completion_client.post("/completion", json=body)
    r.raise_for_status()
    return r.json()

# --- completions outside a chat turn ------------------------------------------------------------
# The timed jobs (briefing, journal, feed), the note-taker and the recipe organizer ask the model
# for text with no tools. They used to post to Ollama themselves; they come through here now, so a
# new place to run the model (the phone) is a change to this module alone.

def _completion_request(messages: list[dict], *, model: str | None, temperature: float,
                        num_ctx: int | None, max_tokens: int | None, json_format) -> tuple[str, dict]:
    """The path and body for one no-tools completion on the configured backend. `json_format` is
    None, "json", or a JSON schema. In OpenAI mode the server's own model answers (OPENAI_MODEL);
    context size and thinking are Ollama settings and are left out."""
    if BACKEND in ("completion", "phone"):
        return text_request(messages, temperature=temperature, max_tokens=max_tokens, json_format=json_format)
    if BACKEND == "openai":
        body = {"model": OPENAI_MODEL, "messages": messages, "stream": False, "temperature": temperature}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_format == "json":
            body["response_format"] = {"type": "json_object"}
        elif json_format:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": json_format}}
        return "/v1/chat/completions", body
    options = {"temperature": temperature}
    if num_ctx:
        options["num_ctx"] = num_ctx
    if max_tokens:
        options["num_predict"] = max_tokens
    body = {"model": model or MODEL, "messages": messages, "stream": False, "think": False, "options": options}
    if json_format:
        body["format"] = json_format
    return "/api/chat", body


def text_request(messages: list[dict], *, temperature: float = 0.3, max_tokens: int | None = None,
                 json_format=None, **_other) -> tuple[str, dict]:
    """A no-tools /completion request rendered with the model's template: what the phone (or a
    llama.cpp server) runs. Ollama-only options (model, num_ctx, timeout, ollama) don't apply."""
    import chat_format
    body = {"prompt": chat_format.render(messages, [], chat_template(), bos_token=BOS_TOKEN, eos_token=EOS_TOKEN),
            "temperature": temperature, "stop": [EOS_TOKEN], "n_predict": max_tokens or OUTPUT_TOKENS}
    if json_format:
        body["json_schema"] = {"type": "object"} if json_format == "json" else json_format
    return "/completion", body


def _completion_result(payload: dict) -> dict:
    """{"content", "truncated"} from any wire format. Truncated means the output cap cut it off."""
    if BACKEND in ("completion", "phone"):
        return {"content": payload.get("content") or "", "truncated": payload.get("stop_type") == "limit"}
    if BACKEND == "openai":
        choice = (payload.get("choices") or [{}])[0]
        return {"content": (choice.get("message") or {}).get("content") or "",
                "truncated": choice.get("finish_reason") == "length"}
    return {"content": (payload.get("message") or {}).get("content") or "",
            "truncated": payload.get("done_reason") == "length"}


def complete(messages: list[dict], *, model: str | None = None, temperature: float = 0.3,
             num_ctx: int | None = None, max_tokens: int | None = None, json_format=None,
             timeout: float = 60.0, ollama: str | None = None) -> dict:
    """One no-tools completion, blocking: for the timed jobs and the note-taker's worker thread.
    `ollama` overrides the Ollama address for one caller (the note-taker can run elsewhere).
    With the model on a phone this refuses: those callers queue in model_jobs instead."""
    if deferred():
        raise RuntimeError("the model is on a phone: queue this request in model_jobs")
    path, body = _completion_request(messages, model=model, temperature=temperature, num_ctx=num_ctx,
                                     max_tokens=max_tokens, json_format=json_format)
    if BACKEND == "completion":
        r = httpx.post(COMPLETION_BASE + path, json=body, timeout=timeout)
    elif BACKEND == "openai":
        r = httpx.post(OPENAI_BASE + path, json=body, headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                       timeout=timeout)
    else:
        r = httpx.post((ollama or OLLAMA).rstrip("/") + path, json=body, timeout=timeout)
    r.raise_for_status()
    return _completion_result(r.json())


async def acomplete(messages: list[dict], *, model: str | None = None, temperature: float = 0.3,
                    num_ctx: int | None = None, max_tokens: int | None = None, json_format=None) -> dict:
    """complete() for callers already on the brain's event loop (the recipe organizer)."""
    path, body = _completion_request(messages, model=model, temperature=temperature, num_ctx=num_ctx,
                                     max_tokens=max_tokens, json_format=json_format)
    if BACKEND == "phone":
        return _completion_result(await _send_text(body))
    r = await {"openai": oai_client, "completion": completion_client}.get(BACKEND, client).post(path, json=body)
    r.raise_for_status()
    return _completion_result(r.json())
