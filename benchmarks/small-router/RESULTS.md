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

## Caller: data and seeds (2026-10-15)

Room-aware labels (roomless "lights off" leaves target out), five seeds per training set,
scored on the 235 caller cases. "Wrong actions" counts schema-valid calls that would execute,
after the said-words gate.

| Training set | Fully right, per seed | Mean | Right tool | Wrong actions after gate |
|---|---|---|---|---|
| Templates + 624 Home Assistant sentences (1,876) | 80 79 77 81 79 | 79.2% | 94% | 28 29 27 31 33 (29.6) |
| + ~1,060 agent-written phrasing templates (3,737) | 78 77 77 81 77 | 78.0% | 95% | 31 33 28 26 29 (29.4) |

Run-to-run spread is about 4 points, so the two sets are a tie: doubling the data with new
phrasing did not move the caller. Data volume is not the bottleneck at this size; the misses
left are judgment and policy (negation, cancel/move with no tool, implicit requests). Before
the room-aware fix, a v3 run scored 76% because a null target trained as the word "None".

## Constrained decoding probe (2026-10-15)

Same checkpoint, scored with and without constrained decoding (constrain.py: names and enums
from the schema, digits for numbers, free text must continue a phrase the person said).

| | Fully right | Wrong calls | Caught by the gate | Wrong actions that execute |
|---|---|---|---|---|
| Model alone | 79% | 47 | 16 | 31 |
| Constrained | 79% | 46 | 8 | 38 |

Constraining did not reduce wrong calls (47 to 46). It moved them: 4 fixed, 2 right ones
broken, and 5 that the gate caught became valid-looking wrong actions it cannot see ("what's
playing?" muted the music; "can't find the good hammer" logged it returned; "hey hestia"
made a calendar event). An invalid output is the signal that the model is confused, and the
constraint erases the signal by forcing the nearest legal action. Kept: the gate. Not used:
constrained decoding, unless a future version turns "the program had to override the model"
into a reason to ask. (The probe also let "all" into every free-text field, a checker bug.)

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
