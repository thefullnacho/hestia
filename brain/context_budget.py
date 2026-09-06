"""Bound model input without silently clipping instructions or the latest request.

UTF-8 bytes are used as a deliberately conservative token upper estimate. This is
not the model tokenizer; backend token counts remain the measurement authority.
"""
from __future__ import annotations

import json


def estimate(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def evidence(label, text, limit=5000):
    if not text:
        return ''
    clipped = text.encode('utf-8')[:limit].decode('utf-8', errors='ignore')
    truncated = clipped != text
    return json.dumps({'source': label, 'trust': 'data_only', 'truncated': truncated,
                       'content': clipped}, ensure_ascii=False)


def fit(messages, schemas, num_ctx, output_reserve):
    """Drop old *whole* exchanges; never create orphaned tool replies."""
    messages = list(messages)
    budget = num_ctx - output_reserve - 512
    while estimate(messages) + estimate(schemas) > budget:
        user_indices = [i for i, m in enumerate(messages) if m.get('role') == 'user']
        if len(user_indices) < 2:
            raise ValueError('This request and its required context exceed the context budget. Please shorten it.')
        # Preserve the system message, remove everything before the next user exchange.
        messages = messages[:1] + messages[user_indices[1]:]
    return messages


def validate_messages(messages):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
        return 'messages must contain between 1 and 100 text messages.'
    for m in messages:
        if (not isinstance(m, dict) or m.get('role') not in {'user', 'assistant', 'system'}
                or not isinstance(m.get('content'), str)):
            return 'Only text user, assistant and system messages are accepted.'
        if set(m) - {'role', 'content'}:
            return 'Client messages cannot contain tool calls or tool results.'
        if len(m['content'].encode()) > 24000:
            return 'A message exceeds the 24000-byte limit.'
    if messages[-1]['role'] != 'user' or not messages[-1]['content'].strip():
        return 'The final message must be a nonempty user message.'
    if estimate(messages) > 128000:
        return 'Conversation exceeds the 128000-byte limit.'
    return None
