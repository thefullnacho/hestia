# Tool-firing SFT dataset — scope

> **★ OUTCOME (2026-06-22): the agency cliff was NOT an SFT problem.** Diagnosis showed the
> raw 4B can produce a perfect `home` call when scoped to the home tool alone, but drowns when
> offered all 8 tools (it rambles "is home part of media?" and never fires). The real fix is
> **deterministic tool-scoping** — `brain/skills/home_control/SKILL.md` (`tools: home`), the
> skills-router pattern that already scopes garden requests. Raw qwen3:4b + that skill took home
> actuation **0/5 → 5/5** with zero training. SFT still *complements* it: it firmed up general
> under-calling on the non-scoped cases (flaky 3–4/5 → solid 5/5), lifting `qwen3-4b-toolfire-v2`
> + skill to **95%** key-match (home 5/5, no over-firing). The two are orthogonal: scoping cures
> selection-under-load; SFT cures under-calling. The training below remains the SFT half.


Goal: close the **agency cliff** found in the size-ladder eval (2026-06-21). qwen3:4b
grounds with zero confabulation (97% stable) but won't *act* — it chats instead of firing
the tool ("turn off the lights" → empty/`final`, 0/5) and burns latency on hidden
reasoning. That failure is *selective* (it routes 80% fine, grounds perfectly), which is
the signature of an **elicitation gap, not a capacity wall** — exactly what SFT fixes. We
already proved the disposition is movable: sharpening two tool descriptions took qwen3:8b
under-calling 91%→100%. SFT does the same reweighting at the weight level, for the residual
prompting can't reach.

This trains the **fixed tool vocabulary once**; per-user customization stays config
(`_request_schemas` enabling/disabling pre-trained tools), never per-house SFT.

## What we're training (3 dispositions, priority order)

1. **Act, don't chat** — a stated intent fires the tool. (the 4B actuation cliff, the
   under-calling we saw across 8b/14b/4b)
2. **Don't over-fire** — chitchat and context-answerable questions go to `final`, no tool.
   (the inverse failure SFT can *introduce* if the negatives are too thin)
3. **Fire directly** — emit the call with no reasoning preamble. (trains out the 4B's
   ~9.6s latency at the same time — it was token *count*, not token *speed*)

## Format — match production exactly

Native tool-calling shape, `think=False`, OpenAI messages + `tools` (most LoRA trainers
ingest this). Targets are either an `assistant` tool_call or an `assistant` `final` with
**no** tool_calls. The tool schemas in each record are the real `tools.SCHEMAS` (single
source of truth), so we never drift from what production serves.

```jsonc
{"messages": [
   {"role":"system","content":"<Hestia prompt + injected GENERIC catalog>"},
   {"role":"user","content":"turn off the kitchen lights"},
   {"role":"assistant","content":null,
    "tool_calls":[{"type":"function","function":{"name":"home",
       "arguments":"{\"action\":\"turn_off\",\"entity_id\":\"light.kitchen_lights\"}"}}]}],
 "tools": [ ... real schemas ... ],
 "meta": {"stratum":"actuation","tool":"home","heldout":false}}
```

## Composition (strata + rough share)

| stratum | maps to failure | ~share | target |
|---|---|---|---|
| **actuation** (home on/off/set/open/close) | 4B actuation cliff | 15% | `home` call |
| **passive-capture** (records.log/.remember) | 14B puppy residual | 12% | `records` call, *unasked* |
| **recall** (memory.recall, records.recent/entity) | confabulate-instead-of-lookup | 10% | `memory`/`records` call |
| **reminders** (reminder.create) | memory/reminder bleed | 8% | `reminder` call |
| **media/live** (media, weather, search, status) | media↔search bleed | 10% | right live tool |
| **boundary-contrast** (minimal pairs) | all the bleed | **25%** | mixed — the core |
| **hard-negatives → final** | over-firing | **15%** | `final`, no tool |
| **ambiguity → clarify** | model guesses a target | 5% | `final` asking |

Heavy weight on **contrast + negatives** — bulk coverage is cheap; the discriminating
examples are what stop the whack-a-mole and the over-firing.

## The core: contrast sets

Minimal pairs differing **only** in the discriminating feature, emitted as *groups* so the
boundary is learned at the weight level instead of fought in the prompt:

- `"remember the coffee is the orange bag"` → **memory.write** · `"remind me to buy coffee
  at 5"` → **reminder** · `"we got a dog named Coffee"` → **records.remember**
- `"is the kitchen light on?"` (state in catalog) → **final from context** · `"turn the
  kitchen light off"` → **home.turn_off**  *(read vs act — also fixes the MAX_STEPS loop)*
- `"what's in bed 3?"` (question) → **final, no log** · `"I pulled weeds from bed 3"`
  (event) → **records.log**

## Multi-step / loop-termination

Production is a loop, and the 8B **looped `home` 6× to MAX_STEPS** on "is the light on?".
So ~15% of examples carry a `tool → tool-result → assistant FINALIZES (stops)` turn, to
train "read the block, answer, stop" instead of re-querying.

## How it's built (cheap, because verifiable)

- **Templated gold (`sft_gen.py`, this commit):** intent → correct `(tool,args)` is
  deterministic, so we *construct* the gold — no grading. House-agnostic GENERIC entities
  (generic beds/devices/pets), paraphrase-augmented. Covers actuation, passive-capture,
  recall, reminders, media/live, boundary, ambiguity, and read-from-context negatives.
- **Teacher-distilled finals (phase 2, TODO):** richer natural `final` phrasing sampled
  from a teacher, kept if it fires no tool + passes the English check (VibeThinker
  "Spectrum" diversity; reward = "didn't fire + English").
- **Real-trace mining (phase 2, TODO):** anchor phrasing distribution to production —
  `journalctl --user -u hestia-brain` logs `start <text> tools=[...]`, and
  `brain/eval_results/*.json` hold real traces. Successful calls = gold; failed
  (under-called) prompts = intents to author gold for.

## Size, held-out, anti-goals

- **Size:** ~2–4k examples is plenty to move a *disposition* via **LoRA** (not full FT).
- **Held-out generalization test** (answers "does agency generalize?"): v1 withheld the
  `home` **open/close** *action* (only 2 examples — too thin to score). **v2 (`sft_gen_v2.py`)
  withholds a whole *room* instead** — `HELDOUT_ROOM = "outside"`, 63 examples: no train
  example ever actuates `light.light_outside_lights`, but the entity IS in the live scoped
  prompt at inference, so it measures pure transfer of the tool *shape* to an unseen entity.
  Scored by `eval_heldout.py` (fired / action / entity / exact). The exact
  `eval_keymatch`/`eval_models` prompts are on a blocklist so there's no train/test leak.
- **Result (2026-06-22, `eval_heldout.py`, single pass, temp 0.3):**

  | model | fired home | EXACT (held-out) |
  |---|---|---|
  | `qwen3-4b-toolfire-v2` (adapter) | 54/63 (85.7%) | **54/63 (85.7%)** |
  | `qwen3:4b` (base, no SFT) | 54/63 (85.7%) | 53/63 (84.1%) |

  **SFT bought ~nothing on held-out generalization** (+1 example, within noise). The base
  4B already transfers actuation to the never-trained room at 84% *once the home_control
  skill scopes the prompt* — direct confirmation of **scoping > SFT**. The ~14% miss is not
  the agency cliff: all 9 failures are the **noun-elided** phrasings ("turn off *the outside*"
  with no "light"/"lights"), where the model can't bind "the outside" to the outside *lights*
  and either stays in prose (∅) or grabs the wrong tool. That's an ambiguous-reference /
  dataset-design artifact, not a firing-disposition gap.
- **Anti-goals:**
  - *Overfit to this house* → generic entities only; train the tool *shape*, not the garden.
  - *Forgetting / losing conversation* → LoRA + low LR + small + the negatives protect chat.
  - *Language drift* → all targets English (harness checks).
  - *Over-firing* → the 15% hard-negatives + read-from-context finals are the counter-pressure.

## Validation loop (already built)

LoRA on the free 4060 Ti → serve adapter → re-run `eval_keymatch.py` (agency cliff off
0/5) + `eval_models.py` (no grounding regression, English 100%, latency drop) + the
held-out room probe via `eval_heldout.py` (did agency generalize?). The harness *is* the eval.

## Usage

```
uv run --project brain python brain/sft_gen.py --per-intent 8 \
    --out brain/sft_data/toolfire.jsonl
# -> toolfire.jsonl (train) + toolfire.heldout.jsonl + a stratum/tool summary
```

## Status (v1 — `sft_gen.py`)

Templated gold is **live and verifiable by construction**: all strata generate, including
multi-step loop-termination and the read-from-context "driest bed" aggregation that
directly targets the failing live case. Honest limits of v1, deferred to phase 2:

- **Size saturates ~300–400 unique** — dedup `(user, target)` flattens identical templates
  on the small generic entity pools. Reaching the 2–4k target needs the paraphrase/teacher/
  real-trace expansion above, not a bigger `--per-intent`.
- **Thin tools** — `memory`, `recall`, `search` are under-covered (few phrasings). Bump
  their phrasing lists or lean on teacher-distillation before training.
- **Finals are templated**, not natural — fine for the *don't-fire* signal, but phase-2
  teacher text will read better.

The shape, format, contrast sets, and held-out probe are correct and trainer-ready; v1 is a
solid base to LoRA against and iterate via the harness.
