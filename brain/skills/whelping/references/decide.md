# Whelping & kennel procedures

Pick the procedure that matches the request. Names (Lily, Bodhi, Fiona) resolve from
the roster in the system prompt.

## "When is X due?" / gestation
1. Look for a stored due/whelp date first: call `records` `entity` on the dam and read
   her attributes/relations. If a due date is recorded, answer from it.
2. If only a breeding date is known, estimate ~63 days out but say "around" and give the
   58–68 day window — don't invent a precise day, and don't trust your own date math.
3. If nothing is recorded, say so and offer to log the breeding (with the date) so the
   due date is stored going forward.

## "Is she close / what do I watch for?" — whelp-watch
1. Load the signs from knowledge.md: the temperature drop below ~99°F is the headline.
2. Give the concrete watch list (temp twice daily, nesting, refusing food) and the
   window the drop implies (~12–24h).
3. State the red flags plainly so they know the line where it stops being a watch and
   becomes a vet call.

## Logging a birth — the records discipline
When a puppy is born ("Lily had a pup, 6 oz, male, by Bodhi"):
1. Call `records` with `action='birth'`: `name` = the pup's name, `dam`, `sire`, and
   whatever of `sex` / `weight` / `color` is known. Pass `ts` if it wasn't just now.
   This creates the pup, links lineage, and groups it into the litter automatically.
2. For a pup not yet named, still record it (use a placeholder like "Lily pup 1") so the
   litter count stays right; rename later via `remember` on the pup.
3. Don't store a manual litter total — it's computed from the actual pups.
4. Ongoing weights are health events: log each with `records` `log` (kind='health',
   subject = the pup, detail = the weight) so a fading pup shows up in the timeline.

## "How many puppies / which litter?" — progeny questions
- Call `records` `entity` on the dam or sire and read the precomputed progeny total and
  per-litter breakdown. Answer from that — do not try to sum litters in your head.

## Answering
Be concrete and calm. For care/observation, give the watch points and the vet line. For
records, confirm what you logged in one sentence (the pup, the litter, the new count).
