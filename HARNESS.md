# Harness development

The production agent loop is the evaluation entry point. `brain/eval_models.py`
uses fresh synthetic stores per case and intercepts every external tool; only model
inference reaches local Ollama. It never copies household data or stops a resident
model. Run with `uv run --project brain python brain/eval_models.py qwen3:14b:nothink`.
Results default to a temporary JSON file. A failing case makes the command exit 1.
The key-match and held-out probes share production prompt construction and model
settings, but measure first-call selection only, not task completion. Their catalogs
are synthetic; old scores based on live household state are not directly comparable.

Each request collects prompt and completion tokens across all model rounds,
backend load/prefill/generation durations, context preparation time and total time.
The chat response reports actual accumulated usage. Metrics logs contain request IDs
and counts, not prompt or tool argument contents. No telemetry leaves this service.

Use state assertions for writes, and inspect both task success and p50/p95 latency.
Recorded historical model comparisons do not establish a baseline for this harness.

## Actions and retries

The dispatcher validates advertised types, enums and action-specific required fields.
The loop also enforces its selected tool set. Shopping-list clear requires the exact
user confirmation `confirm clear the shopping list`; the model cannot supply that flag.
Tool results carry succeeded/failed/unknown status. Writes return backend receipts,
including partial or unknown outcomes, instead of trusting a model's success claim.
This does not detect a requested action the model never attempted.

Clients can send `Idempotency-Key` (8-128 letters, digits, underscores or hyphens).
Reusing a key with identical messages replays the response; differing messages or a
still-running/crashed request return 409. Operation receipts are stored privately in
`data/operations.db`. `GET /v1/operations/{request_id}` exposes late outcomes under the
same private network boundary as chat. An interrupted write is never automatically
repeated. Exactly-once remote execution cannot be guaranteed across backend crashes.
Requests without a client key receive a generated ID but cannot deduplicate a lost
response unless the client retained the ID. Keep keys unique for intentional new turns.
The ledger currently retains receipts until operator maintenance; it is private data.

## Context and routing

One prepared plan selects up to three matching skills and unions their tool lists
with explicit reminder, shopping, memory, records, search and status intents. Short
pronoun follow-ups include the preceding user topic for routing. This is a bounded
heuristic, not general coreference resolution. Only a single soil-state question
with available readings can disable tools; observations retain records access.

Evidence blocks carry source and truncation metadata and are explicitly data-only.
Stable instructions precede changing evidence. History is trimmed at whole user
exchange boundaries, keeping assistant/tool groups together. The conservative UTF-8
byte estimate reserves `HESTIA_OUTPUT_TOKENS` (default 768) plus protocol headroom.
If the current exchange and required context cannot fit, the harness asks for a
shorter request instead of silently dropping instructions. This estimate intentionally
overestimates typical English token counts; backend counts remain authoritative.
Tool results are bounded by `HESTIA_TOOL_RESULT_BYTES` (default 6000).

The API accepts only bounded text user/assistant/system messages; client-supplied
system messages are ignored and client-supplied tool evidence is rejected. Prompt
boundaries reduce instruction confusion but are not a proof against prompt injection.

Memory recall uses lexical rarity and document-length normalization, ignores common
question words, and treats pins as tie-breakers only after a relevant match. Injected
memories include record ID, source and last-seen date. No embedding service was added;
semantic retrieval remains a measured follow-up if lexical misses warrant it.

## Latency and streaming

`HESTIA_TURN_BUDGET` covers context preparation and all model/tool rounds. New
turns fail promptly when `HESTIA_ACTIVE_TURNS` (default 1) is occupied, avoiding an
unbounded inference queue. `HESTIA_FINAL_RESERVE` (default 3 seconds) prevents new
tools from consuming the final-answer budget. `HESTIA_MAX_TOOL_CALLS` defaults to 16.
Concrete independent reads in one model batch can run concurrently in the existing
bounded worker pool; writes remain sequential. A cancelled write retains its worker
slot and durable unknown receipt until the worker actually completes.

SSE emits real incremental tokens for a tools-disabled final synthesis, including
simple read answers after tool execution. Tool-selection rounds and write receipts
remain buffered so speculative action claims do not reach the user. Client disconnect
cancels further model work; already dispatched writes can still complete. Replaying a
completed request streams its saved answer. Streamed replies use the same note gate.
The bundled browser and HA clients retain request keys for transport retries within
their current session. They continue to use non-streaming replies; API streaming
clients can opt in with `stream: true`.
