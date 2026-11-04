# The box renders the phone's prompt (2026-11-04)

The phone runs the model on plain text; the box renders the prompt with the model's chat template
and reads the tool calls back out (`brain/chat_format.py`, PHONE-APP.md). `parity.py` holds both
halves against llama-server (build 11946) on real Hestia turns: 25 sentences across every domain,
each with the brain's real system prompt and scoped tools, plus the second model call of every
turn that made a call (the call and a tool result). LFM3-1.7B QAD-Q4_0, the template file given to
both sides, thinking off.

| | identical |
|---|---|
| Rendered prompt vs `/apply-template`, byte for byte | **48/48** |
| Tool calls read from `/completion` text vs llama-server's chat endpoint | 44/48 |

The four tool-call differences are the two endpoints writing different text, not the same text
read two ways:

- llama-server's chat endpoint steers generation once a tool call starts. On "Should I water the
  carrots?" it wrote two calls where free generation wrote one; on a goal it wrote "crops" where
  free generation wrote "growth".
- One answer ran past the 256-token cap mid-call. llama-server returns the half call with broken
  arguments; `chat_format.parse` returns no call, which is the safer reading.
- One second call answered a fake "Done." result with thinking-style text before its call.

One reader bug found and fixed: a value declared number-or-string (`qty`) came back as text where
llama.cpp reads it as a number.

**What this changes:** the phone generates freely, like `/completion`, but the 91.1% whole-turn
held-out score was measured through llama-server's chat endpoint, which steers tool calls. The
score has to be measured again through the phone's own path before it stands for the phone.
