---
name: garden_bed
description: Use when reasoning about watering, soil moisture, or the state of a vegetable bed. Covers the bed→sensor map, dry/saturated thresholds, and how to combine a live soil reading with the rain forecast into a water / hold / check-drainage decision.
triggers: water, watering, irrigate, soil, moisture, bed, beds, dry, garden, planted, plant, plants, planting, growing, backyard, orchard, guild, guilds, strawberry, strawberries, blueberry, blueberries, carrot, carrots, tomato, tomatoes, beet, beets, potato, potatoes, pepper, peppers, artichoke, pea, peas
tools: home, weather, records
metadata:
  domain: gardening
  version: 0.1.0
---

# Garden Bed

Use this skill for any decision about whether a bed needs water, is too wet, or is
fine — and for answering questions about a specific bed's moisture.

## Load these resources
- `references/knowledge.md` — the bed→sensor map, the moisture thresholds, and the
  sensor quirks you must not be fooled by.
- `references/decide.md` — the step-by-step procedure for a watering decision.

(`references/learn.md` is for the offline preference-extraction job, not this loop —
do not load it to answer a question.)

## Working rules
- Never advise watering from soil moisture alone; always check what rain is coming first.
- A reading you don't trust (a known-bad channel) is not data — say so rather than guess.
- Prefer "it's fine, leave it" over a needless watering call near the thresholds.

## Reading vs. recording (make the right call)
- A **question** about the garden ("what's in Bed 1?", "is the tomato bed dry?") is answered
  from the GARDEN block and the live soil reading. Do NOT call `records` to answer a question —
  the planting list is already in front of you.
- An **observation or change** the user reports ("I thinned the hot peppers", "transplanted the
  artichokes", "lost two tomato plants to frost", "harvested the snow peas today") is a fact to
  keep. Log it once with `records.log`: `kind` = `note` (an observation/state) or `chore` (an
  action you did), `subject` = the bed or plant they named (it attaches to the existing bed),
  `detail` = what happened. Then confirm in one line. Don't log a question, and don't log the
  same thing twice.
