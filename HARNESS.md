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
