# Pest watch — spec

A proactive, deterministic garden alert: warn (phone push, via HA) when a crop's pest enters its
**emergence window**, with the evidence-rated companion interplantings to act on. Sibling to
[`garden_watch.py`](garden_watch.py); same posture — runs on the daily 7am timer, notifies *only*
when actionable, no LLM in the loop (this is a threshold job, hestia's "determinism over
intelligence" north star).

## Source of truth (cross-repo)

Pest data lives on the **Homesteader Labs site**: `content/crops/pest-companions.json` — a
phenology-aware emergence table. Per crop, a list of `pests`, each with:
- `soilTempThreshold` (°F) — always present.
- `gddThreshold` (growing-degree-days, base 50°F) — present for *most* pests, **not all**
  (e.g. `nematode` is soil-temp only).
- `companions[]` — `{companion, companionId?, reason, placement (interplant|border), evidenceLevel}`.

Crops covered (10): tomato, potato, cabbage, broccoli, kale, squash-summer, squash-winter,
cucumber, beans-bush, beans-pole.

**Decoupling:** vendor a snapshot to `data/pest-companions.json` rather than reading the sibling
repo's working tree. The site is the source of truth; refresh with a documented one-liner. (See
the Forager wiki `ligaments.md` / `model-registry.md` for why we don't hard-link repos.)

## The hard constraint (drives the design)

Hestia has **6 Ecowitt soil-*moisture* sensors and zero soil-*temperature* sensors**, and no
air-temp entity in HA. So the thresholds can't be read directly. What we *can* get:

- **GDD** — computable from `weather.forecast_days()` daily `hi`/`lo` (Open-Meteo), accumulated
  day-over-day in a state file. **This is the spine.**
- **Soil temp** — *estimated*, not measured: a trailing N-day mean of air temp tracks bare-soil
  temperature at seed depth well enough for an emergence heads-up. Documented as an estimate.

### GDD math
`gdd_day = max(0, (hi + lo) / 2 - 50)` (°F, base 50). Accumulate from a **biofix** = season start.
Default biofix = last spring frost (reuse the site's NOAA-normals frost date via `frostNormals` /
the weather zone), falling back to Jan 1. Cumulative GDD is persisted; each daily run adds the new
day(s).

### First-run back-fill
`forecast_days()` is forecast-only (7 days), so on first run (or a fresh season) back-fill GDD from
biofix→today with one **Open-Meteo archive** call (`archive-api.open-meteo.com`, `start_date`/
`end_date`, same `temperature_2m_max/min`). Thereafter, accumulate incrementally — no repeat archive
hit. (Small extension to `tools/weather.py`: a `history_days(start, end)` helper.)

## Algorithm (per daily run)

1. Load `data/pest-companions.json` and the watched-crop list (config; default = all 10).
2. Load state `{biofix, cumulative_gdd, last_run_date, est_soil_temp, alerted: {"crop:pest": season}}`.
3. Advance GDD: if `last_run_date < yesterday`, back-fill the gap (archive); else add yesterday's
   `gdd_day`. Update `est_soil_temp` = trailing 10-day mean air temp. Stamp `last_run_date` (advance
   once per calendar day — idempotent, mirroring `garden_watch.saturated_alert`).
4. For each watched crop × pest: it's **in window** when
   `cumulative_gdd >= gddThreshold` (if the pest has one) **and** `est_soil_temp >= soilTempThreshold`.
   Pests with no `gddThreshold` fall back to the soil-temp gate alone (lower confidence — flag it).
5. Emit an alert for each newly-in-window pest **not already in `alerted` this season**; mark it.
   Fire once per pest per season (no nagging).
6. Push via HA notify (reuse `garden_watch`'s `_HDRS` / notify service), and log a `pest_alert`
   event through `records_store` for history. No actionable pests → no notification.

### Alert copy (example)
> 🐛 **Hornworm window open** (tomato): 150 GDD reached, est. soil 62°F. Interplant **Basil** or
> **Borage** now (mask scent / draw predatory wasps); **Marigold** as a border. [evidence: moderate]

Top 1–2 companions by `evidenceLevel`, with `placement`. Keep it one push per crop (group its pests).

## Config (env, mirroring garden_watch)
- `PEST_CROPS` — CSV of cropIds to watch (default: all in the JSON).
- `PEST_BIOFIX` — ISO date override; else last-spring-frost; else Jan 1.
- `PEST_SOILTEMP_WINDOW` — trailing days for the soil-temp estimate (default 10).
- `PEST_GDD_BASE` — default 50.
- Reuses `HA_URL`/`HA_TOKEN`/`GARDEN_NOTIFY` from secrets.

## Delivery
New `brain/pest_watch.py` + `deploy/systemd/hestia-pest-watch.{service,timer}` (daily, `Persistent=true`),
OR fold into the existing 7am garden-watch run as a second alert source (one push, fewer timers —
**recommended**, since both are "morning actionable garden" notices). State file `data/pest_state.json`
via a new `config.PEST_STATE`.

## Known limitations / future accuracy
- **Soil temp is estimated from air temp.** The real upgrade is an **Ecowitt WN34 soil-temp probe**
  per bed (~$15) → read it like `soilmoisture` and replace the estimate. Until then, soil-temp-only
  pests (e.g. nematode) are best-effort.
- GDD base is a flat 50°F; some pests use different bases. The JSON carries one `gddThreshold`, so
  we treat it as base-50. Revisit if the site adds per-pest bases.
- Garden-wide, not per-bed (air temp/GDD are site-wide; only moisture is per-bed). Per-bed crop
  mapping + a real soil-temp probe would make it per-bed later.

## Task checklist
- [ ] Vendor `data/pest-companions.json` (+ a `make`/script refresh line from the site path).
- [ ] `tools/weather.py`: add `history_days(start, end)` (Open-Meteo archive) for the back-fill.
- [ ] `config.PEST_STATE` path.
- [ ] `pest_watch.py`: GDD accumulation + soil-temp estimate + window test + once-per-season dedupe.
- [ ] Wire delivery (fold into garden-watch run, or new timer) + `records_store` pest_alert log.
- [ ] Tests: GDD accumulation/back-fill, idempotent daily advance, once-per-season dedupe, alert copy.
- [ ] (later) Ecowitt WN34 soil-temp probe → swap estimate for measurement.
