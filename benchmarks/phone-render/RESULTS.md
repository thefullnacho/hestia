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

## The held-out suite on the phone's path (same day)

`heldout_turn.py` rebuilds the whole-turn held-out scorer behind the 91.1% (the script was never
committed; the method is in `benchmarks/RESULTS.md`): 63 commands against the withheld 'outside'
room, `run_agent` against the fixtures, exact when the first on/off that ran has the gold action
and an id that resolves to the gold light. Same llama-server, same model and template, thinking
off (`--reasoning off`), K=5.

| Path | exact |
|---|---|
| llama-server's chat endpoint (how 91.1% was measured) | 294/315 = **93.3%** |
| **The phone's path:** the box renders, plain `/completion`, the box parses | 292/315 = **92.7%** |

The phone's path holds: two tries apart in 315, and only five cases score differently, all in the
bare-area family ("flip on the outside"), in both directions. The chat endpoint now scores above
the recorded 91.1%, likely the harness fixes merged since 2026-10-29.

The misses differ in kind. The chat endpoint's 21 are 17 turns with no switch call and 4 with a
light that doesn't resolve. The phone path's 23 are 11 with no call, 8 unresolved, and **4 that
turned the kitchen lights off when asked to put or flip the outside on**: wrong light and wrong
action. Steering kept the chat endpoint from that; free generation didn't. The person never said
"kitchen", which is what Hestia Lite's said-words gate catches; a check like it on light
commands is the fix to try.

A first check pass with thinking on (no `--reasoning off`) scored 85.7%, every miss a turn with no
light call: the thinking used up the output cap before the call was written, as on 2026-10-29.

## Holding light switches to the person's words (same day)

`guards.said_words` sends back a switch whose action contradicts an unambiguous "on" or "off", or
whose light isn't in the area the person named; the model can call again. Same suite, K=5:

| | exact | the misses |
|---|---|---|
| Phone path, before | 292/315 = 92.7% | 11 no call, 8 unresolved light, 4 wrong action |
| **Phone path, with the check** | 290/315 = 92.1% | **25 no call** |
| Gemma 4 12B (Ollama), with the check | 291/315 = 92.4% | 24 no call |

The score holds within run-to-run noise, and the misses change kind: none switched a wrong light
or the wrong way (the kitchen was switched 0 times; before, 4 tries turned it off for "put on
the outside"). A refused call the model doesn't fix on its retry ends the turn with "I haven't
completed the requested action", so a miss now does nothing instead of the wrong thing. Gemma
lands where it was (92.1% recorded), so the check costs the resident nothing.

## Ready for a real phone (same day)

`heldout_turn.py --serve <host:port>` hosts `/v1/model/socket` in the scorer's own process, waits
for a phone with the box's model to connect, and runs every held-out turn through it, timing each
one. `test_box.sh` starts a separate phone-mode brain for developing the app (own port, scratch
household, refuses 0.0.0.0). Checked with `fake_phone.py` answering from a llama-server on the
4060 Ti: 57/63 exact (90.5%, inside the phone path's 56 to 60 per pass), 269 prompts over the
socket, turn median 2.6 s and 90th percentile 6.0 s. The iPhone generates about a third as fast as
that GPU, so its turns will be slower; the run on the 17 Pro will say by how much, and whether
it throttles over 315 turns.
