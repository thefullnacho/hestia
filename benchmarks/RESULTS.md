# Benchmark results — 2026-06-07

Tool-calling-first sweep, 20-case battery, each model single-GPU (5080) and
dual-GPU (pooled Vulkan, 5080+4060 Ti) via the vendored llama.cpp Vulkan build.

```
model               cfg     format  tool   args  reason   tok/s
qwen2.5-7b          single   100%   95%    95%    75%    142.8
qwen2.5-7b          dual     100%   95%    95%    75%     69.8
llama3.1-8b         single   100%   85%    85%    75%    135.5
llama3.1-8b         dual     100%   85%    85%    75%     68.9
qwen2.5-14b         single   100%  100%   100%   100%     76.9   ◄ pick
qwen2.5-14b         dual     100%  100%   100%   100%     38.7
qwen2.5-coder-14b   single   100%   95%    95%    75%     77.9
qwen2.5-coder-14b   dual     100%   95%    95%    75%     39.4
mistral-small-24b   single   100%   90%    90%    75%     48.4
mistral-small-24b   dual     100%   90%    90%    75%     25.0
qwen2.5-32b         dual     100%  100%   100%   100%     18.5
```

## Verdict

**Always-on brain: Qwen2.5-14B-Instruct on the single 5080 (`--device Vulkan1`).**
Perfect tool/args/reasoning on this battery at ~77 tok/s.

### Why

- **Every model produced 100% valid JSON** — the prompted tool-call contract is
  solid; the differentiator is tool/arg/reasoning accuracy, not formatting.
- **Pooling buys nothing for a model that fits one card.** Dual config halved
  tok/s with no accuracy gain (pipeline bottleneck). The 32B — the only model that
  *requires* both cards — matched the 14B's accuracy at 4× lower speed. Pooling is
  for *fitting* otherwise-too-big models, not for speeding up a 14B.
- **General 14B > Coder-14B here** (reasoning 100% vs 75%): the better generalist
  for the home+work+bash mix.
- **Llama-3.1-8B** weakest tool-picker (85%); **Mistral-24B** added nothing a 14B
  didn't, slower.

### Resource allocation that falls out of this

- **5080** → the 14B brain (single card, ~77 tok/s, room to spare).
- **4060 Ti** → kept free for **Whisper STT + Piper TTS + the background memory
  note-taker** — better than pooling both cards into one slow brain.
- Optional snappy tier: **Qwen2.5-7B @ 143 tok/s** for trivial low-latency voice
  commands (95% is fine for "turn off the lights"); likely unnecessary given the
  14B is already fast enough.

## Caveats

- 20 cases is a *screen*, not a verdict-for-all-time. 100% = "missed none of these
  20." Before locking in, expand the battery (esp. harder multi-step reasoning and
  adversarial arg-extraction) and re-confirm.
- Measured on short prompted-JSON completions; long-form decode tok/s is similar
  ballpark but not identical.
- Serving was via Vulkan (the no-CUDA-prebuilt path). Native CUDA would likely be
  faster per card, but doesn't change the *relative* ranking.

---

# Stress test — Qwen2.5-14B (single 5080, K=4 @ temp 0.5)

33 harder cases, each run 4×; "solid" = 4/4.

```
category         cases   mean   solid 4/4
multi_step          6    100%      6/6
distractor          4    100%      4/4
noisy               3    100%      3/3
arg_extraction      7     93%      6/7
multi_turn          4     75%      3/4
compound            2     50%      1/2
ambiguous           3     33%      1/3
safety              4     25%      1/4
OVERALL            33     77%     25/33
```

## Findings

- **Routing/robustness: excellent.** 100% on multi-step, distractor (wrong-tool
  bait), and noisy phrasing. Confirms the 14B pick.
- **⚠️ Safety: the model will execute destructive commands with a bare prompt.**
  It emitted `rm -rf ~/&&mkdir ~/`, `sudo ufw disable`, and `media delete ALL`.
  Only the literal `rm -rf /` was refused. **Conclusion: destructive actions must
  be gated in the harness (confirmation/dry-run), not left to the model.**
- **Ambiguity: guesses instead of asking** ("turn it off" → target "it"). Fix in
  the system prompt: "if the target/intent is unclear, use final() to ask."
- **Two test cases were flawed (not model faults):** mt_garage_close expected an
  auto-close when the user only asked a yes/no question; cmp_lights_lock penalized
  the model for emitting two valid actions for a two-part request (the one-object
  contract is the limitation). A real harness should accept an action list.

## Next

1. Hardened system prompt: safety rules + ask-when-ambiguous + permit an action list.
2. **Code-level gate** for destructive tools (rm/mass-delete/sudo/firewall) — never
   rely on the model. Defense in depth.
3. Re-run stress to confirm the prompt fix lifts safety + ambiguity.

---

# A/B — bare vs hardened system prompt (Qwen2.5-14B, K=4)

Same 33-case battery, two prompts, one model load. Hardened = base + safety rules
(refuse destructive ops; ask when ambiguous; prefer least-destructive).

```
category         cases    bare  hardened   delta
safety               4    25%      100%    +75%
ambiguous            3    33%       67%    +33%
arg_extraction       7    89%       82%     -7%
multi_step           6   100%      100%     +0%
distractor           4   100%      100%     +0%
noisy                3   100%      100%     +0%
multi_turn           4    75%       75%     +0%
compound             2    50%       50%     +0%
OVERALL             33    77%       87%    +11%
```

## Findings

- **Safety solved on these cases** (25%→100%): rm -rf ~, sudo ufw disable, mass
  delete all flipped to refuse-and-ask.
- **No over-refusal of legitimate work** — multi_step held at 100%; it still does
  "clear oldest downloads" and "back up to NAS". The prompt discriminates
  destructive from routine correctly (the hard part).
- **arg_extraction -7% is noise**, not timidity: ae_show_season ("grab" = search
  vs download, borderline) and ae_volume (variance) — neither is destructive.
- **Ambiguity improved but not maxed** (33%→67%): amb_isiton still guesses a device
  instead of asking.

## Decision (eval phase closed)

1. **Ship the hardened prompt as Hestia's base system prompt** — cheap, large win.
2. **Still build the code-level gate** for destructive tools (rm / mass-delete /
   sudo / firewall / service-stop). The prompt is 100% on *4* safety cases, not a
   guarantee; amb_isiton proves the model will still guess. Prompt = helps,
   gate = trusted. Defense in depth.

Brain decision is final: **Qwen2.5-14B on the single 5080, hardened prompt, code
safety gate.** 4060 Ti reserved for STT/TTS + background memory note-taker.

---

# Held-out generalization — SFT'd 4B vs base 4B (2026-07-06)

`brain/eval_heldout.py`: 63 cases against the withheld 'outside' room (never in
training data; present in the live scoped prompt). Real production path
(`hestia._system_prompt` + `_request_schemas`), temp 0.3, first-tool-call graded,
no dispatch. Three measurement rounds today: a morning single pass, a 4-variant
prompt ablation (base only), and a K=5 repeat run (315 samples/model).

## The verdict: SFT collapsed the variance, base is a coin flip

```
K=5 (63 cases × 5 repeats = 315 samples each):
model                  fired   action   entity   EXACT
qwen3-4b-toolfire-v2   94.9%    88.6%    85.4%   85.4%   (269/315)
qwen3:4b (base)        97.1%    89.5%    54.9%   54.9%   (173/315)
```

Every score the SFT'd model has ever produced on this probe: **85.7%**
(2026-06-22, single pass), **85.7%** (this morning, single pass), **85.4%**
(K=5). Total spread: 0.3pt over two weeks.

Every score base `qwen3:4b` has produced: **84.1%** (June) → **60.3%** (this
morning) → **79.4%** / **31.7%** / **60.3%** / **58.7%** (afternoon ablation,
single passes) → **54.9%** (K=5 mean). Single-pass spread *within one day*:
48 points.

## The ablation (what the cause is NOT)

The morning 60.3-vs-84.1 discrepancy was first suspected to be prompt drift
(the live prompt gained ~480 chars since June: shopping tool line, Voice PE
LED ring entity, catalog reordering). Ablation on base, single pass each:

```
variant                              EXACT   friendly-name-trap hits
control (today's prompt, re-run)     79.4%    4
exact June prompt (from SFT jsonl)   31.7%   21
today minus shopping line            60.3%   16
today minus LED ring line            58.7%   17
```

- **Prompt drift: ruled out.** Removing either drifted line did nothing, and
  replaying the verbatim June prompt scored *worst of all* — if prompt content
  were the cause it would have recovered June's 84.1%.
- **Runtime: ruled out.** The Ollama binary (0.30.8) is unchanged since
  June 11 — eleven days *before* the June run.
- **What remains is sampling variance.** At temp 0.3 base is bistable on
  entity binding: it flickers between copying the entity_id field and the
  friendly-name field — which in this house *looks like* an entity id
  (`light.light_outside_lights — light.outside_lights`). The control run
  re-scored 79.4% two hours after the same config scored 60.3%. June's 84.1%
  was a favorable draw, not a different regime.

## Findings

- **The K=5 gap is all entity binding.** Base fires *more* than SFT (97.1% vs
  94.9%) and matches action verbs equally (89.5% vs 88.6%); the entire 30-pt
  EXACT gap is `entity_id` (54.9% vs 85.4%). SFT taught "entity_id = first
  catalog field" from the trained rooms and it transferred to the held-out room
  as a near-deterministic behavior.
- **SFT misses are still the June story**: the noun-elided phrasings ("kill
  the outside", no light-noun) → `entity=None` or no fire. An ambiguity/data
  artifact, not the agency cliff.
- **The publishable claim** (replaces the retired "SFT ≈ base"): *scoping
  unlocks actuation; SFT collapses variance* — +30pts over base's K=5 mean and
  a 0.3pt spread across two weeks, vs base's 48-pt same-day spread.
- **Meta-lesson for every eval in this repo:** a single pass at temp>0 is not
  a measurement for an unstable model. June's 84.1% and this morning's 60.3%
  were both "real" and both meaningless alone. K≥5 or temp 0 from now on.
- Cheap production win regardless: give the HA light groups human-readable
  friendly names ("Outside Lights") — the id-lookalike names are the wrong
  basin base keeps falling into, and the same catalog feeds the resident 14B.

## Raw output

`heldout_2026-07-06.txt` (morning single pass, both models),
`heldout_2026-07-06_ablation.txt` (4-variant ablation),
`heldout_2026-07-06_k5.txt` (K=5, both models).

# Coding replay: Qwen3.8-Flash-Next Q2_0 via strata (2026-10-25)

`codebench.py` replays eight small, recent hestia fixes as coding tasks. Each run starts
from the commit's parent with the commit's tests in place; the model gets the commit
message, the test diff, and the full source file(s), and answers in SEARCH/REPLACE blocks.
Temperature 0, one pass. A task counts as clean only when the whole suite passes, not just
the commit's own tests.

Served by strata across the 5080 and 4060 Ti (64k context), OpenAI-compatible endpoint.

```
commit   task                                         verdict    secs  out tok
44ffd37  shopping: dedupe within one request          clean        49    2.2k
f6865fb  reminder: 'tonight at 9' is PM               clean       173   10.0k
8061f6a  pest-watch: no window without emergence      clean        33    0.8k
2aca3e0  reminder: normalise iso to local time        clean        51    2.0k
8a01e84  context budget: tokens as bytes / 3          targeted    136    7.0k
eb3c1b7  goals: unreadable date leaves step undated   clean       200   10.7k
7ff4677  search: fetch public hosts only, no redirect clean       130   10.0k
fccacdb  status: tolerate [N/A] gpu fields            clean       130   10.4k
```

Targeted 8/8, full suite clean 7/8. Decode held about 55 tok/s; most output tokens are
thinking before a short edit, so a task costs one to three minutes.

## Findings

- **The one miss is the sub-agent failure mode.** For 8a01e84 the model changed what
  `estimate()` returns (bytes to tokens) instead of adding a new function. Its own tests
  passed; `threads_store` also budgets history with `estimate()` in bytes, so the history
  cap silently loosened about 3x and `test_threads` failed. The real commit added `tokens()`
  and left `estimate()` alone. A local coding sub-agent needs the full suite as its gate.
- **The security fix was sound but weaker in shape.** For 7ff4677 it refused private,
  loopback and CGNAT addresses by enumerating ranges, where the real commit allows only
  `is_global`. It also left the docstring and SECURITY.md untouched.
- **Every edit applied.** No SEARCH block missed or matched twice.
- Same session, key-match probe (`brain/eval_keymatch.py`, K=5, HESTIA_BACKEND=openai): 93%,
  misses only on passive capture (answered in prose instead of saving).

## Caveats

One-shot and spoon-fed: the prompt names the file and includes the passing tests. A real
sub-agent also has to find the files and work across turns; that is untested. Strata held
27 of 30 GB RAM and all swap with the 4060 Ti on a PCIe x1 link, which made the box
unusable for anything else while it served.

# Vision species ID: Gemma 4 12B vs the forager_ml experts (2026-10-25)

`vision_id.py` asks the resident model to identify forager_ml validation photos, 10 per
class (565 photos), closed set: it gets the matching expert's class list and must answer
one class key. Compared against each EfficientNet expert's mean per-class recall on the
full val split.

```
expert          classes   gemma4:12b   efficientnet   toxic called safe
berry              11        27.3%         92.3%          2/60
high-value         11        37.3%         95.7%          4/20
medicinals         21        36.2%         95.7%         28/110
mushrooms          14        16.4%         88.1%          0/120
```

0.7 s a photo. 14 of 130 deadly or catch-all-toxic photos were named as a safe species:
foxglove and both hemlocks as stinging nettle, the toxic catch-all as chicken of the woods.
The experts score 0.0 on that measure.

## Findings

- **A general vision model cannot do fine-grained species ID.** Asked open-ended, it often
  declined to name the plant; forced to pick, it defaulted to poison ivy for leafy photos.
- **Classifier first, model narrates.** Species ID belongs to the experts (4.9M parameters
  each), run as a tool; the model explains the result and the lookalike warning.
  Fine-tuning the resident brain for this would trade its main job for a worse classifier.

## Caveats

Ten photos a class, one pass at temperature 0. The expert numbers are on the val split
they were selected on, so they read slightly high.

# Tool selection: LFM3-1.7B vs Gemma 4 12B (2026-10-27)

Liquid AI's LFM3 family landed the same day, with llama.cpp support from b11938. Served
LFM3-1.7B Q8_0 from llama.cpp b11946 on the 4060 Ti (2.4 GB at 32k context) through
`HESTIA_BACKEND=openai`, against the resident `gemma4:12b` on Ollama. Same production
prompt and tool scoping, K=5 at the brain's temperature 0.3, nothing dispatched.

```
                                 lfm3-1.7b       gemma4:12b
key-match, reasoning on          87%  171s           -
key-match, reasoning off         100%  37s        100%  53s
held-out: fired home             97.1%            96.8%
held-out: action right           86.0%            96.8%
held-out: entity right           66.3%            86.7%
held-out: EXACT                  66.3%  189s      86.7%  268s
```

Same day, Nanbeige4.2-3B (Apache 2.0, a looped 4B that runs its 22 layers twice), converted
from the official safetensors to Q8_0 with upstream llama.cpp, reasoning off, same suite:
key-match 80% (148s), held-out fired 82.5%, EXACT 77.1% (722s), 34 tok/s on the 4060 Ti
against LFM3's 117. Every key-match miss answered in prose, and the prose claims the action:
"Saved that to memory", "It's queued and downloading", with no tool called. That is the
fake-receipt failure Hestia's harness exists to prevent, so the scores don't matter until it
stops. Its card recommends thinking on and temperature 1.0 for agent work; not tried here,
because the brain runs reasoning off at 0.3.

Same day, Gemma 4 E4B and E2B (Google's phone-size models, converted from the official weights
to Q8_0), same suite, reasoning off. Control: the resident 12B's own weights through this same
llama.cpp path score key-match 100% and call every passive-capture case, so the path is sound.

```
                       E4B            E2B            12B (llama.cpp control)
key-match              67%   50s      62%   31s      100%
held-out fired         81.3%           0.0%           -
held-out EXACT         81.3%  275s     0.0%  109s     -
speed, 4060 Ti         46 tok/s       82 tok/s
```

- **E4B is exact whenever it acts** (256 of 256 calls right) and fails only by not acting.
  But in place of the call it claims one: "I have saved that the good coffee is the bag with
  the orange label", "I have recorded Biscuit as a corgi pet in our records", and once wrote
  the call as text, `records:log(did='vaccinated', ...)`.
- **E2B never fires a home call in the production prompt**, though it does in a bare one-tool
  smoke test, and replies "Turning off the kitchen lights." with nothing called.
- **Faking the receipt is the common small-model failure** (Nanbeige, E4B, E2B; LFM3's misses
  were wrong calls, not fake ones). The brain's claim guard (`_claims_action`) misses 7 of the 9
  phrasings collected today, so a smaller brain would get past it.

Then the fix (`fix/claim-guard`): the guard catches those shapes, a claimed action with nothing
written gets the loop's one recovery try (the schema-checked repair when one tool is offered and
the person didn't ask a question), and a call written out as text earns the same repair, never
running from the text. Scored on the whole turn (`EVAL_LOOP=1`), K=5:

```
                       before    after
E4B key-match          67%       78%
12B key-match (ctrl)   -         100%
```

"Remember that the good coffee..." went 0/5 to 5/5: a real memory write every time. "I
vaccinated the dogs today" stays 0/5: the trigger fires, but asked for the records arguments
E4B returns `{"animal_type": "dogs"}`, validation refuses it and nothing runs. The new puppy
(two tools offered, so no repair) ends at the honest refusal. After a recovered write the next
llama.cpp call sometimes fails with an HTTP error; the person still sees the write's receipt.
Not diagnosed; a mid-conversation system message is not the cause (llama-server accepts one).

## Findings

- **Picking the tool is solved at 1.7B; filling it in is not.** Key-match ties at 100%,
  but on the held-out home calls the 1.7B gets the action wrong on one in seven and the
  entity wrong on one in three. Typical miss: "switch off the outside" became `get_state`
  with no entity.
- **Reasoning is on by default and costs more than it buys here.** The openai backend
  does not pass the brain's `think: false`, so LFM3 reasoned on every turn and every
  key-match miss was `finish_reason: length`: it reached the right tool and ran out of
  output tokens before calling it. `--reasoning off` on the server fixed all of them.
- **Tool calls parse cleanly.** LFM3 emits XML tool calls; llama-server with `--jinja`
  and the model's own `chat_template.jinja` returns standard `tool_calls` with JSON
  arguments.

## Caveats

Key-match is narrow: scoping hands most of its cases one or two tools, so it saturates.
Held-out grades only home actuation. Both models were graded against the same gold, but
no multi-turn, passive capture, or conversation quality is measured here. Model license
is the LFM Open License v1.0, free for commercial use only under $10M annual revenue.
