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
from the schema, digits for numbers, free text must be words the person said).

| | Fully right | Wrong calls | Caught by the gate | Wrong actions that execute |
|---|---|---|---|---|
| Model alone | 79% | 47 | 16 | 31 |
| Constrained | 80% | 43 | 8 | 35 |

Constraining cut wrong calls from 47 to 43, but every one it fixed was a garbled copy the gate
already caught ("greenlight", "haylor"). It also turned 4 errors the gate caught into
valid-looking wrong actions it cannot see ("what's playing?" muted the music; "can't find the
good hammer" logged it returned; "hey hestia" made a calendar event), so wrong actions that
execute went up, 31 to 35. An invalid output is the signal that the model is confused, and the
constraint erases the signal by forcing the nearest legal action. Kept: the gate. Not used:
constrained decoding, unless a future version turns "the program had to override the model"
into a reason to ask.

The first run of this probe had two checker bugs, both fixed and rerun above: "all" was allowed
in every free-text field (only light_set's target may say it), and free text had to continue
one contiguous phrase, so it vetoed right answers like "thursday 2" for "thursday at 2". The
labels and the gate only ask that every word was said, in any order; the checker now does too.

## Override gate (2026-10-15)

Constrained decoding where "the program had to override the model" is a reason to ask
(override.py). Two signals, ask if either fires: **override mass**, the probability the model
put on tokens the mask threw away (disagreement with the program), and **gap**, the
log-probability margin between the best allowed token and the best allowed token that says
something different, at decision points (call or talk, tool name, enum value, first word of a
span; uncertainty within the allowed set). Thresholds picked on the val split only, minimising
wrong actions plus needless questions, each counting one. The said-words gate still runs after.

Checkpoint: a fresh fine-tune (seed 1015, the 3,737-row set, 79% fully right, inside the
five-seed spread). v3b came from the 1,876-row set, whose shuffle puts today's val rows in its
training. Val picked mass >= 0.07 and gap <= 0.25 (gap alone, unconstrained: <= 0.25).

| Decoding | Gate | Right after gates | Wrong calls | Caught | Wrong actions that execute | Needless asks | Asks |
|---|---|---|---|---|---|---|---|
| Model alone | said-words | 81% | 44 | 17 | 27 | 1 | 18 (8%) |
| Constrained | said-words | 80% | 42 | 5 | 37 | 0 | 5 (2%) |
| Constrained | override, both signals | 75% | 42 | 26 | 16 | 21 | 47 (20%) |
| Model alone | gap only | 80% | 44 | 19 | 25 | 3 | 22 (9%) |
| Model alone | both signals | 75% | 44 | 28 | 16 | 19 | 47 (20%) |

The signals see what the output gate cannot: under the constraint they caught 23 wrong calls
that had been forced valid. But at the thresholds val picked, the trade is poor: 11 fewer wrong
actions than the model alone, for 18 more needless questions, and one utterance in five gets
a question. Val is template-clean (17 wrong calls in 374 rows), so its thresholds fire far more
often on test speech, the same gap as the router's ask rate (6% on val, 19% on test). The
signal has a usable region: a diagnostic test sweep, not used to pick, shows mass >= 0.5 on
the model alone catching 3 more wrong actions with no extra needless asks. Val can't find it.

The constraint still doesn't pay. With the signals on, constrained and unconstrained land in
the same place (16 wrong actions either way), because override mass can be read off the
model's own distribution without forcing anything. Constraining alone made it worse again on
this checkpoint, 27 wrong actions to 37.

Questions are built from the veto (examples from the unconstrained run). Good: "keep playing" ->
"Should I pause the music, or leave it?"; when a branch was the model asking itself, its own
words: "add stuff for tacos" -> "What should I add for tacos?"; with no legal option left, the
field that failed: "remind me to buy milk" -> "What time do you mean?". A forbidden value that
names a sibling tool is read as that tool: in a dry run on v3b, "can't find the good hammer
anywhere" -> "Should I mark the good hammer returned, or look up the good hammer?" (this
checkpoint wanted "lent", and asks "Should I mark the hammer lent?"). Bad: an option only has to
pass the said-words gate, so "I hate this song" offers "play the song hate this song".

Not shipped. Next: pick the thresholds on real speech. The utterance log's week, split into a
calibration half and a test half, is the val set this needs. One checkpoint and greedy decoding,
so the table is K=1; the threshold call should be made at K>=5.

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
