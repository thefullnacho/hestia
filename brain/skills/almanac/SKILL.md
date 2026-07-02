---
name: almanac
description: Use when the user asks about the season as a whole — frost dates, growing degree-days, how this season compares to a prior year, when a pest window opened, or what the almanac says. The almanac page(s) are injected into context; answer from them.
triggers: almanac, growing season, this season, last season, the season, season so far, degree days, degree-days, gdd, growing degree, frost, frosts, frost-free, year over year, last year compared, how late, how early
tools: weather
metadata:
  domain: almanac
  version: 0.1.0
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
about a year with no page in context, say there's no almanac on file for that year (the
almanac started in 2026). The `weather` tool is ONLY for a forecast question ("will we
get frost tonight/this week") — everything historical is already on the page; do not
call a tool for it.
