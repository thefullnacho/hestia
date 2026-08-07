---
name: almanac
description: Use when the user asks about the season as a whole — frost dates, growing degree-days, how this season compares to a prior year, when a pest window opened, or what the almanac says. The almanac page(s) are injected into context; answer from them.
triggers: almanac, growing season, this season, last season, the season, season so far, degree days, degree-days, gdd, growing degree, frost, frosts, frost-free, year over year, last year compared, how late, how early
tools: weather
metadata:
  domain: almanac
  version: 0.2.0
---

# Household Almanac

The user is asking about the season record. The almanac page for this year (and prior
years when they exist) is already in context under `--- ALMANAC ---`. Answer strictly
from it:

- **Frost & season** ("when was our last frost", "how late was spring", "when should I
  expect the first fall frost", "how long is our growing season"): read the "Season &
  frost" section — it has the observed dates, the zone normals, and the delta.
- **Degree-days** ("how many GDD are we at", "how warm has the season run"): the GDD
  line is the accumulated total, with its as-of date.
- **Season story** ("what's happened in the garden this season", "when did the pest
  windows open", "what wildlife have we seen this year"): the timeline, pest, and
  wildlife sections are the complete record — relay them, don't summarize from memory.
- **Year over year** ("how does this season compare to last year"): use the "Year over
  year" section, or compare this year's page against a prior year's page directly.

Never invent a date, event, or species that is not written on a page. If the user asks
about a year with no page in context, say there's no almanac on file for that year.
Pages exist from 2024; 2024 and 2025 were backfilled in July 2026 and are thin, holding
only what could be dated confidently after the fact. The `weather` tool is ONLY for a
forecast question ("will we get frost tonight/this week") — everything historical is
already on the page; do not call a tool for it.

## Recording an observation

The page renders two halves differently, and filing an observation in the wrong one
loses it. This is not visible from the page itself.

- **`sighting`** (subject = a species) feeds the **Wildlife** section, which renders
  ONLY the species name, its first-logged date, and a repeat count. **The detail text is
  never displayed.** Use it for the species record.
- **`note`** (subject = a place: Backyard, Garden Zone, Pond Zone) feeds the **Garden
  timeline**, which renders the detail in full. Use it for the story.

A wildlife event worth remembering needs **both**: a sighting so the species is counted
and shows up in year-over-year, and a note against a place so the account survives on
the page. The canonical example is already in the record:

    Jul 2 — Backyard: observed — Earthworm population explosion this year, so many
            they attracted a mole that burrowed all over the lot.       (note)
    Mole (Jul 2)                                                        (sighting)

Filing the 2024 blue dasher irruption as a sighting alone reduced the most striking
wildlife event on the property to "Dragonfly (Jul 20)". Corrected 2026-07-26 by
adding the note.

Three conventions the record already uses, worth keeping:

- **Retroactive entries say so**, with the filing date and a note that the exact date is
  unrecorded. Both backfilled years and several July 2026 entries are marked this way.
- **Mark a hypothesis as a hypothesis**, with "Working theory:". Observations are facts,
  attributions are not. A plausible mechanism written down as fact is how a small
  dataset quietly stops being trustworthy.
- **Dated changes to the property are anchors.** Burying a line, felling a tree, cutting
  a new bed. They bracket uncertain memories: the 2024 irruption was dated by the
  powerline the dragonflies perched on, which was buried in spring 2025.

Keep entries to a sentence or three. The timeline is read at a glance; depth belongs in
the source docs, not in every line.
