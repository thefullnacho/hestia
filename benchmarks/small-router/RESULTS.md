# Small-model router: results

The design under test: EmbeddingGemma picks the domain, FunctionGemma (270M) makes the call
with only that domain's tools, and deterministic code gates between them. Floor hardware is a
Pi 4 with 4GB. Test set: the 247 cases in `cases.jsonl` (see `REVIEW.md`). Training set:
`train.jsonl` from `build_train.py`, template-built on held-out vocabulary, with a val tenth.

## Router (EmbeddingGemma on the Pi 4, about 0.7s per utterance)

| Method | Top-1 domain | Wrong action | Asked | Chit-chat that acted |
|---|---|---|---|---|
| Tool descriptions, no task prefixes | 72% | | | |
| Tool descriptions, task prefixes | 85% | 2% with gate | 18% | 15 of 30 |
| Leave-one-out over test cases (leaky ceiling) | 88% | | | |
| Training utterances as examples, `none` a domain | **93%** | **1.1%** with gate | 19% | **0 of 30** |

- EmbeddingGemma's task prefixes are not optional: without them `none` cannot be separated.
- The gate: `none` on top drops it; a top-two similarity gap under 0.08 asks "which of these
  two" (answered by code); otherwise act. The gap was picked on the val split, not the tests.
- Remaining misses are mostly list wording ("I got the eggs", "I already did the dishes") and
  negation, which embeddings do not see.
- Ask rate is 6% on val but 19% on the tests: template examples are cleaner than real speech.

## Caller (FunctionGemma 270M, scored on GPU through transformers with the official template)

| Model | Test cases fully right | Right tool | Val split |
|---|---|---|---|
| Stock `functiongemma-270m-it` | 34% | 57% | |
| Fine-tune v1 (4 epochs, lr 5e-5, template set) | **74%** | **93%** | 96% |

Of v1's 61 failures, 49 are schema-valid calls that would execute. Some are span wording an
item matcher would absorb ("the 2x4s"); the rest are real: roomless "lights off" turning off
everything instead of asking, the item taken from the wrong noun ("for the cake"), one call
where the sentence needed two, and chit-chat that reached the caller.

## Notes

- Ollama's FunctionGemma GGUF ships without a chat template; the official one is in the
  Hugging Face repo's tokenizer files. Ollama's raw output also drops special tokens, so
  accuracy is scored through transformers and the Pi checks latency and the quantized model.
- `tools.json` writes parameters in shorthand; `schema.py` turns them into JSON Schema. With
  the shorthand, the template renders every tool with no parameters.
- Gemma's 262k vocabulary makes the logits the memory cost of training: batch 1 with 16 steps
  of accumulation and gradient checkpointing fits beside the voice services on a 4060 Ti.
- Every round of reading test failures and teaching their patterns spends some of the test
  set's independence. The final exam should be utterances the household actually says.
