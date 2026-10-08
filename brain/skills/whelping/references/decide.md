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

## Logging a breeding (a tie)
1. Call `records` `log`: `kind='breeding'`, `did='tied'`, `subject` = the dam, `detail` = the sire
   and anything the operator said about it. Pass `ts` for a tie that was not just now: both
   reminders count from it.
2. The tool files the day-28 pregnancy check and the day-56 whelp-watch itself and says so in
   its reply ("Reminders set: ..."). Read that back in a sentence. Do not use `reminder` for
   these. A milestone already in the past is not filed, so a reply with no "Reminders set"
   line means nothing was due to file, not that something failed.
3. If the pregnancy check comes back negative, or the tie turns out to be wrong, cancel that
   dam's pending reminders: `reminder` `list`, then `cancel` each by id.

## Logging a birth — the records discipline
When a puppy is born ("Lily had a pup, 6 oz, male, by Bodhi"):
1. Call `records` with `action='birth'`: `name` = the pup's name, `dam`, `sire`, and
   whatever of `sex` / `weight` / `color` is known. Pass `ts` if it wasn't just now.
   This creates the pup, links lineage, and groups it into the litter automatically.
2. For a pup not yet named, still record it (use a placeholder like "Lily pup 1") so the
   litter count stays right; rename later via `remember` on the pup.
3. Don't store a manual litter total — it's computed from the actual pups.
4. Ongoing weights go through `records` `action='weigh'` — `name` = the pup, `qty` and
   `unit` (oz/g/lb/kg). Use this and not a plain `log`: `weigh` stores the number in grams,
   which is what `puppy_watch` compares day over day. A weight written into free text is
   readable by a person and invisible to the watcher.
5. There is a form at `/whelp` that does all of this without you, and it is the preferred
   path when the operator has a pup in one hand. If a name will not resolve, or anything at
   all is uncertain, say so and point at the form. Never answer "logged" unless the tool
   returned a confirmation.

## "Is the pup gaining / is it fading?" — the weight curve
1. `records` `entity` on the pup shows its recent weighings. Answer from the numbers, and
   say the direction plainly: gaining, flat, or down.
2. Do not reassure past the data. Flat or dropping weight is the earliest sign of a fading
   pup, and "probably fine" is the answer that costs a puppy.
3. `puppy_watch` already alerts on this twice a day while a litter is under three weeks old
   (lost weight, no gain in two days, still under birth weight after day 3, or down 10% from
   its own peak). If the user is asking, the alert either has not fired yet or they want the
   detail — give them the readings, not a summary of the rules.

## "How many puppies / which litter?" — progeny questions
- Call `records` `entity` on the dam or sire and read the precomputed progeny total and
  per-litter breakdown. Answer from that — do not try to sum litters in your head.

## Answering
Be concrete and calm. For care/observation, give the watch points and the vet line. For
records, confirm what you logged in one sentence (the pup, the litter, the new count).
