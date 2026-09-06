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
