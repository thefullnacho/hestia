---
name: wildlife
description: Use when the user is recalling or logging a wildlife sighting — "when did I last see a deer", "any fox sightings lately", or reporting "saw a turkey by the pond". Scopes the request to the records tool so the model fires it instead of drowning in the full tool list or reaching for search.
triggers: sighting, sightings, spotted, sighted, wildlife, deer, fox, foxes, coyote, coyotes, bear, bears, turkey, turkeys, hawk, owl, owls, rabbit, rabbits, raccoon, raccoons, opossum, possum, snake, snakes, eagle, heron, bobcat, critter, tracks
tools: records
metadata:
  domain: wildlife
  version: 0.1.0
---

# Wildlife Sightings

The user is recalling or logging a wildlife sighting. Wildlife sightings live in the
`records` event log (kind `sighting`) — NOT in memory, and never use search for the
homestead's own sightings. Call the `records` tool:

- **Recall** ("when did I last see a deer", "any fox sightings this week"):
  `action='recent'`, `kind='sighting'`, `subject='<species>'` (singular, lowercase, e.g.
  `deer`, `fox`). Add `since` for a window ("this week"/"lately" → an ISO date). Then answer
  from the rows — give the date of the most recent one.
- **Log** ("saw a turkey by the pond", "two coyotes in the back field this morning"):
  `action='log'`, `kind='sighting'`, `subject='<species>'`, `did='observed'`, and when given
  `location`, a `count` in `attrs`, or a `ts` if it wasn't just now.

If a recall comes back empty, say there's no sighting on record for that species — don't
invent one. Fire the tool; don't deliberate about which tool to use (this request is yours).
