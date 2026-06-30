---
name: recipe
description: Use when the user wants to cook or bake — getting a recipe, walking its steps, asking about ingredients, quantities, temperatures or times, substitutions, or saving a recipe. Covers looking the recipe up, grounding every quantity in it, and keeping a hands-free kitchen conversation going.
triggers: recipe, recipes, recipe for, cook, cooking, bake, baking, baked, how do i make, how to make, how do you make, what should i make, for dinner, for breakfast, ingredient, ingredients, oven, preheat, knead, dough, batter, simmer, roast, broil, saute, marinade, casserole, tablespoon, teaspoon, substitute, substitution
tools: recipe, search, reminder
metadata:
  domain: kitchen
  version: 0.1.0
---

# Recipe

For cooking and baking: fetching a recipe, walking its steps hands-free, and answering
questions about it while someone's hands are busy in the kitchen.

## Get the recipe in front of you first
- For any "how do I make / cook / bake X", call `recipe` with action='lookup' FIRST. If it
  returns a saved recipe, that is the household's canonical version — use it and nothing else.
- If lookup finds nothing, use `search` to find one on the web, read the best result, and
  answer from it. Then OFFER to save it ("want me to save this one for next time?"). Only on
  a yes, call `recipe` action='save' — and pass the recipe CLEANED into a short Ingredients
  list and numbered Steps, with the blog story, ads, and chatter stripped out.

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
