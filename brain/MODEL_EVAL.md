# Resident-brain model eval — 2026-06-14

Comparing candidates for Hestia's resident model slot against the incumbent
`qwen3:14b`, using `eval_models.py` (the real brain machinery: live system prompt +
light/soil catalog + tools + temp 0.3 + tool loop). Run in production fast mode
(`:nothink`). The bash tool has been removed from the brain, so the `disk` and
`rm -rf` safety cases are no longer meaningful (see notes).

## Deeper run — 10 repeats/case (the decision data)

Hardware note: these latencies were measured on the **4060 Ti** (see GPU pinning
section); the 5080 is faster, so absolute times are pessimistic. Relative ranking holds.

| Model            | Size        | Real correctness¹ | English   | Avg latency | Notable miss |
|------------------|-------------|-------------------|-----------|-------------|--------------|
| **qwen3:14b** ✅  | 14.8B dense | **100/100**       | 110/110   | 5.9s        | none |
| gemma4:12b       | 12B dense   | 99/100            | 110/110   | 4.9s        | 1× confabulation on open garden summary |
| lfm2.5:8b        | 8B/1B-active| 86/100            | 110/110   | 3.7s        | flaky: driest-bed 9/10, blueberry 9/10, records-log 8/10 |

¹ "Real correctness" excludes the `disk` case, which fails for every model because the
brain no longer exposes a bash tool to call (not a model weakness).

## 3-repeat round (earlier, includes the two rejected models)

| Model            | Real correctness | Avg latency | Verdict |
|------------------|------------------|-------------|---------|
| qwen3:14b        | 10/10            | 7.9s        | incumbent, flawless |
| gemma4:12b       | 10/10            | 5.3s        | strong; one confabulation slip surfaced at 10× |
| qwen3.5:9b       | 9/10             | 4.3s        | drops records-logging of garden observations |
| Mellum2-Instruct | 9/10             | 3.9s        | code model — fails live-soil reasoning |
| lfm2.5:8b        | 9/10             | 4.3s        | flaky on judgment cases at 10× |

## Decision

**Keep `qwen3:14b`.** It went 110/110 including every confabulation trap. `gemma4:12b`
is a near-equal and ~17% faster, but its single slip was exactly the failure mode the
system is built to prevent (inventing a plant on an open-ended garden question), so the
modest latency win didn't justify swapping. `gemma4:12b` retained as the strongest backup.
Rejected: `lfm2.5:8b` (judgment flakiness), `qwen3.5:9b` (records-logging gap),
`Mellum2` (code-specialized, weak on garden reasoning).

## Infra notes uncovered during this eval

- **Ollama upgrade**: 0.24.0 → 0.30.8 (the new Qwen3.5/Mellum2 archs need ≥0.30).
  The installer also drops a *system* `ollama.service`; it was disabled in favor of the
  user-level `hestia-ollama.service` (which owns `~/.ollama`).
- **GPU backend regression**: 0.30.x defaults `OLLAMA_VULKAN=true` and **prefers the
  Vulkan backend**, which bound the brain to the 4060 Ti and ignored `CUDA_VISIBLE_DEVICES`.
  Fix in `hestia-ollama.service`: `OLLAMA_VULKAN=0` + `CUDA_DEVICE_ORDER=PCI_BUS_ID` +
  `CUDA_VISIBLE_DEVICES=0` → forces CUDA on the 5080 (verified: brain on CUDA0, Ti idle).
- `lfm2.5:8b` emits `<think>` blocks even with `think:false` (bakes reasoning into output);
  the harness strips them, but it costs latency.

## Reproduce

```
cd ~/hestia && set -a && . secrets/ha.env && set +a
EVAL_REPEATS=10 uv run --project brain python brain/eval_models.py \
  qwen3:14b:nothink gemma4:12b:nothink lfm2.5:8b:nothink
```
