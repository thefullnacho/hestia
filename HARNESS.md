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
Requests without a client key are not written to the request ledger at all; their
operation receipts are still stored under the generated ID returned in `X-Request-ID`.
A busy, timed-out or backend-down answer ran no write, so it is never stored as a
key's durable answer: the client's retry runs the turn instead of replaying the failure.
Keep keys unique for intentional new turns. Request rows are pruned after one day and
operation receipts after seven; the ledger is private data.

## Context and routing

One prepared plan selects up to three matching skills and unions their tool lists
with explicit home, reminder, shopping, memory, records, search and status intents. Short
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

## Background learning

The note-taker always writes proposals to the inbox. The former
`HESTIA_NOTETAKER_AUTOWRITE` setting is ignored, preserving the approval invariant.
Extraction has one dedicated worker and no waiting queue; busy periods skip optional
extraction. `HESTIA_NOTETAKER_OLLAMA` can select a separate local inference server,
with `HESTIA_NOTETAKER_MODEL` selecting its model. Sharing the foreground GPU can
still add contention after a note job has started; no runtime GPU settings are changed.

A simple single light on/off command returns its verified tool receipt after one
model round; the receipt names the light by its Home Assistant friendly name, since it
is spoken aloud by the voice clients. Successful mixed informational/action requests retain the informational
answer alongside explicit verified receipts. Known failed or unknown writes override
model completion prose. Interrupted streams retain the visible partial answer plus
the failure in the replay receipt and are never sent to background learning.

Empty model replies and unattempted explicit log/record, light, reminder and shopping
commands receive at most one recovery instruction. A second failure produces an honest
non-completion response and is not learned. This catches a measured native Ollama case
that generated tokens but returned an empty message with no parsed tool call. Detection
is intentionally limited to explicit command forms and does not prove all user intents
were completed. A light command counts only when the text names a light or refers back
to one, because the home tool actuates nothing else. Missing arguments may still
require a clarifying question.

When one explicit write tool is missing and was offered for the request, recovery uses
Ollama's schema-constrained JSON output for that tool's arguments, then the ordinary
scope, validation and receipt path. It never executes free-form model prose. This is a
fallback only, consumes the same turn budget, uses temperature zero, and does not
repeat a dispatched write.
See [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

Successful light mutations invalidate the cached prompt catalog immediately, so the
next turn refreshes state instead of treating a pre-action snapshot as current. The
synthetic evaluation catalog follows fixture state for the same reason. Explicit
follow-up actuations such as "turn them back on" require a write attempt too.

A native parser miss that returns a complete JSON read-call object can be normalized
into a validated read call. This accepts exactly `name` and `arguments`, rejects
mutations and extra prose, and is disabled when the user asks for JSON/code/examples.
It addresses an observed model response that printed a records lookup instead of
executing it. Write recovery remains schema-constrained, never parsed from prose.

## Validation snapshot, 2026-09-06

272 offline tests passed. The resident `qwen3:14b` with thinking disabled passed
24/24 synthetic evaluations (eight cases, three repeats each). Case latency
was p50 1.35s and p95 2.40s; the follow-up case includes two turns.
The simple light-command median was 0.55s with one model round. These are
isolated regression checks with mocked household backends, not a production SLA or
a controlled before/after speedup measurement. No service was deployed or restarted.

Further inference tuning should compare context size, concurrency and cache settings
against this suite plus a broader held-out corpus. No inference-engine environment
settings, model weights or thinking defaults were changed. Semantic retrieval remains
a separate experiment, justified by measured misses rather than added by default.

## Conversation and context evaluation

`brain/eval_conversation.py` runs six synthetic three-turn discussions through the
production loop and through a minimal conversational prompt as a diagnostic control.
The control changes the prompt and removes the harness/tool path; it is not a pure
prompt ablation. Both use thinking disabled, temperature 0.3, 768 output tokens and
32K context by default. Other sampling defaults come from the installed model.
Warm-up loads are excluded from conversation latency. Each scenario gets fresh
synthetic stores. No live household tools or web search are available in this test.

```sh
uv run --project brain python brain/eval_conversation.py qwen3:14b gemma4:12b --output /tmp/hestia-conversations
uv run --project brain python brain/eval_conversation.py gemma4:12b --context-only --probe-contexts 32768 65536 131072 262144 --output /tmp/hestia-contexts
```

JSON retains prompts, responses, tool calls, metrics, model digests, and context
allocation snapshots. Markdown transcripts include per-scenario review rubrics.
Review correctness, adaptation, uncertainty, naturalness and unwanted actions;
there is no automatic general-intelligence score. The corpus is synthetic and small,
not a standardized benchmark. Search failures here reflect an intentionally offline
fixture and must not be mistaken for a model knowledge failure.

The context probe measures allocation with a short prompt, not full-window speed or
recall. `--fill-chars N` optionally adds a repeated synthetic input and a beginning/middle/end
code retrieval check, saving actual backend token counts and timings. Characters are
not tokens, and this easy retrieval check does not establish long-context reasoning.
Probing stops after CPU offload or a load error. The `--restore` model (default
`qwen3:14b`) is warmed at `--restore-context` (default 32K) in a finally block; check the saved restoration flag on failure.
These commands change GPU residency temporarily and should run serially when the
resident assistant is idle. They do not change service settings or deploy a model.

### Conversation snapshot, 2026-09-06

Six three-turn scenarios per model and mode produced 72 completed turns and no tool
calls. Through Hestia, Qwen3 14B had median/p95 full-response latency of 1.19/2.68s;
Gemma 4 12B had 1.97/4.32s. Median answer lengths were 43 and 91.5 words respectively.
Gemma gave a clearer analogy, better wet-shoe airflow reasoning, and more specific
conversational engagement. Both tracked corrected fictional facts. Both also made
unsupported or oversimplified claims, and Gemma often exceeded voice-friendly brevity.
The minimal control did not consistently improve accuracy and increased verbosity.
This is a single sample of each discussion, not a statistically ranked knowledge test.

Gemma also loaded 32K, 64K, 128K and 256K contexts entirely on the 5080. NVIDIA
process allocations were approximately 8.9, 9.5, 10.2 and 12.3 GiB; these include
more overhead than Ollama's reported model allocation. A separate 256K-window
probe processed 166,731 tokens and retrieved all three beginning/middle/end codes
in 84.32s, including 83.26s of prompt evaluation. The filler was repetitive and
retrieval was easy; this does not establish full-window reasoning or realistic
conversation latency. This direct Ollama probe bypasses Hestia's conservative input
trimmer and exceeds its default 45-second turn budget. Model capacity is therefore
not the same as end-to-end supported input size or acceptable voice latency.
Qwen3 14B was restored at 32K afterward. No resident configuration changed.

Schema validation accepts advertised JSON type unions while preserving strict boolean
versus numeric checks. The records harvest schema still declares `qty` as a number and
no text quantity parser exists, so "2 lb 7 oz" is rejected before dispatch; the union
support is in place for a tool that chooses to advertise one.

## Resident trial

The Gemma 4 12B trial uses the systemd drop-in at
`deploy/systemd/hestia-brain.service.d/90-resident-trial.conf`, installed in the matching
user systemd directory. It selects `gemma4:12b`, thinking disabled and 32K context.
The larger context probes do not justify changing the default voice latency budget.
Remove that drop-in, reload user systemd and restart only `hestia-brain` to return
to the base unit's resident model. The base unit and Python fallback remain Qwen3 14B.

## Daily briefing continuity

Each non-dry-run briefing saves its collected facts, collection/generation timestamps,
exact narrated text (or raw fallback), narrator model, and delivery receipts in the
private `data/briefings.db`. This is workflow history, not an automatically approved
preference or household fact. Old announcements are not backfilled. The archive is
retained until operator maintenance; recall fetches at most three recent records or
records from an explicit ISO date, today, or yesterday. Context is capped at 10KB and
marked as historical, data-only evidence. Briefing/announcement questions and narrow
follow-ups such as "What about yesterday?" retrieve it across clients, including a
fresh Voice PE conversation. Other date expressions may require clarification.

A source snapshot is not current house state. Fresh-state questions still require live
tools. Delivery `accepted` means HA accepted the service call, not that the user heard
it. `pending` after interruption and `unknown` after an error are unconfirmed. Voice
receipts list each attempted satellite and discovery failures. Disabled delivery and
raw/fallback narration are explicit. A failed push does not suppress voice delivery;
an archive failure does not suppress either channel. No automatic delivery retries
were added. A dry run neither archives nor delivers anything.

The briefing service's resident drop-in is a relative symlink to the brain's drop-in,
so both receive the same `HESTIA_MODEL` and `HESTIA_NUM_CTX`. Install the symlink as
well as its target and reload user systemd. `HESTIA_MODEL` now takes precedence over
the legacy `HESTIA_BRIEFING_MODEL` fallback. Narration uses thinking disabled and a
768-token output cap; truncated narration falls back to the complete fact lines.
To end the trial, remove both installed trial drop-ins, reload user systemd, and
restart the brain. The next scheduled briefing then uses the base fallback too.

`uv run --project brain python brain/eval_briefing.py gemma4:12b` exercises recall,
exact narration, a missing date, delivery uncertainty, a date-changing follow-up,
fresh versus historical state, and instructions embedded in archived narration
through the production loop with synthetic archives and intercepted household tools.
It saves transcripts and review flags under `/tmp`; flags are not a semantic score.

Validation: 299 offline tests and 24/24 synthetic Gemma action checks passed. Seven
briefing scenarios (eight turns) were reviewed for factual recall, date handling,
unknown delivery, fresh state and untrusted narration; no mutations were dispatched.


## Recipe import and approval

The chat client's Recipes button opens `/recipes`. Paste recipe text, upload a PDF,
or import a public URL. Structured Recipe JSON-LD is extracted directly, preserving
its ingredient and instruction strings without model rewriting. Multiple recipes
remain separate candidates. Blog prose is left for manual selection; linked PDFs
are offered as separate imports and never fetched automatically.

Text and PDF imports can use the resident to organize a draft, with tools disabled
and the normal inference admission limit. Busy, long, or unreadable sources remain
editable drafts. PDFs require local `pdftotext`; extraction is limited to 40 pages
and does not perform OCR. Sources are limited to 5 MiB; model organization is limited
to 14 KB of extracted text. URL requests reject private addresses, redirects and
compressed responses, and pin the connection to a validated public IP.

Original bytes and draft metadata live under the private recipes directory in
`.review/sources` and `.review/drafts`. Review shows the original text, downloadable
source, and warnings for missing details and suspicious quantities. These checks
are heuristics, not a guarantee that an extraction is faithful. The human checks
quantities and steps, edits as needed, and explicitly approves in the browser.
Only approved Markdown files enter recipe lookup and listing. Chat `save` and
`import_url` create drafts; the model has no approval tool. Conversational approval
does not bypass browser review.

Replacing an existing recipe requires a separate replacement checkbox and an
unchanged content hash. Previous bytes are retained in `.review/versions`; writes
are atomic and serialized, and duplicate approvals are idempotent. Sources, drafts
and versions are retained until manually removed. Existing recipe files remain
readable without migration. The review API shares the brain's private network
boundary and requires same-origin browser mutation headers.
