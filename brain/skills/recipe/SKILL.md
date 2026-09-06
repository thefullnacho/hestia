---
name: recipe
description: Use when the user wants to cook or bake — getting a recipe, walking its steps, asking about ingredients, quantities, temperatures or times, substitutions, or saving a recipe. Covers looking the recipe up, grounding every quantity in it, and keeping a hands-free kitchen conversation going.
triggers: recipe, recipes, recipe for, cook, cooking, bake, baking, baked, how do i make, how to make, how do you make, what should i make, for dinner, for breakfast, ingredient, ingredients, oven, preheat, knead, dough, batter, simmer, roast, broil, saute, marinade, casserole, tablespoon, teaspoon, substitute, substitution
tools: recipe, search, reminder
metadata:
  domain: kitchen
  version: 0.2.0
---

# Recipe

For cooking and baking: fetching a recipe, walking its steps hands-free, and answering
questions about it while someone's hands are busy in the kitchen.

## Get the recipe in front of you first
- For any "how do I make / cook / bake X", call `recipe` with action='lookup' FIRST. If it
  returns a saved recipe, that is the household's canonical version — use it and nothing else.
- If lookup finds nothing, use `search` to find a source and offer to import it. A user-provided
  recipe or PDF URL goes to `recipe` action='import_url', never to generic page stripping.
- Pasted or dictated recipes use `recipe` action='save' to prepare a DRAFT with Ingredients
  and numbered Steps. Preserve all quantities, units, timing, temperature, yield and notes.
  Do not invent missing values, combine recipes, scale amounts, or silently substitute.
- Every import or save is pending review. Tell the user to open Recipes in the chat app,
  choose the draft, compare it with the original, and approve it there. Include the returned
  review path in text chat. Never claim the recipe is in the collection before approval.
- Only the human review page can approve or replace recipes. A conversational "yes" can
  authorize preparing a draft, not bypass review. Existing recipes require an explicit
  replacement choice; older versions and source snapshots are retained.

## Ground every quantity — never recall one
- Amounts, oven temperatures, and times come ONLY from the recipe text in front of you. Never
  state a quantity or temperature from memory: a wrong number in a kitchen ruins the dish. If
  it isn't in the recipe, say so and offer to look it up rather than guessing.
- Walk numbered steps ONE at a time — give the current step, then stop.

## Keep the kitchen conversation alive
- Their hands are busy, so don't make them say the wake word for every follow-up. End a step
  or an answer by inviting the next turn — "Ready for the next step?", "Want the next
  ingredient?". Ending on a question is what keeps the mic open without a re-wake.
- When they're clearly finished ("that's it", "thanks"), close warmly WITHOUT a trailing
  question, so the conversation ends cleanly.

## Substitutions and technique are judgment, not lookup
- "Can I use oil instead of butter?", "what can I swap for buttermilk?" — these are NOT in the
  recipe. Answer from cooking knowledge (or `search` if unsure), and say it's a suggestion.
  Don't present it as something the recipe specified.

## Timers belong to the timer, not you
- "Set a timer for 20 minutes", "remind me when the bread's done" → use the `reminder` tool.
  Never try to track elapsed time yourself.
