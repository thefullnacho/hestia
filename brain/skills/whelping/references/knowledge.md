# Whelping & kennel knowledge

## Our program (Lhasa Apso, a small/toy breed)
The full, authoritative roster is in the WHO & WHAT block of the system prompt — read
names, AKC numbers, DOBs, and litter counts from there, not from here. In brief:
- **Lily** and **Fiona** are the **dams** (Fiona has been bred to Bodhi for her first
  litter; her breeding and due dates are in `records`, not here). **Bodhi** is the **sire**. **Momo** is a retired/neutered male — not
  breeding. Operating belief: one sire can cover up to ~4 dams.
- Litters and individual puppies are tracked in `records` (the `birth` action creates a
  pup and links its dam/sire; progeny totals are computed from the actual pups/litters).

## Gestation & due dates
- Canine gestation averages **~63 days** from the breeding/ovulation date, normal range
  **58–68 days**. Small breeds often whelp at the earlier end.
- Date math across months is error-prone — **prefer a stored due date** over computing
  one. When a breeding is recorded, store the expected due date as an attribute so it's
  later a lookup, not a calculation. If you must estimate, say "around" and give the
  58–68 day window, not a false-precise single day.

## Approaching whelp — the signs (last ~24–48h)
- **Temperature drop** is the classic signal: a dam's normal temp is ~**100–102.5°F**;
  a sustained drop **below ~99°F** usually means labor within ~12–24 hours. Twice-daily
  rectal temps in the last week catch it.
- Nesting/digging, restlessness, panting, refusing food, clear vaginal discharge.
- Once hard straining/contractions begin, pups normally arrive within the windows below.

## Neonatal basics (first weeks)
- Small-breed pups are tiny — roughly **4–8 oz** at birth is typical; **weigh every pup
  daily** at the same time. Healthy pups **gain a little every day**; flat or dropping
  weight is the earliest warning of a fading pup.
- Keep the whelping area warm: chilling is the #1 neonatal killer. A pup that's cold,
  limp, not nursing, or constantly crying needs intervention.
- Eyes open ~10–14 days; nothing should be forced before that.

## Box temperature (our house practice)
- Heating pad under ~2/3 of the floor on its own thermostat at **85°F**; the rest unheated
  so pups can crawl off it. Lily runs hot and needs that cool side too.
- Heat lamp supplements from outside the box, hung where it can't fall in.
- Air at pup height on the heated side: **~90°F week 1** (from the last litter, matches the
  usual 85–90), then about **80°F weeks 2–3** and **75°F by week 4** (general guidance, not
  yet confirmed here).
- The kennel box shows this; `box_watch` alerts outside 70–90°F in week 1, 75–88 weeks
  2–3, 68–83 weeks 4–5. **Week 1 was recalibrated 2026-09-24 from 82–93 to 77–90, then
  again 2026-09-29 13:52 EDT from 77–90 to 70–90** when the heat lamp went OFF, the pad
  stayed at 85, and ambient settled at ~72°F as the new normal (pups self-regulating, no
  distress). The band is tied to where the Govee actually sits, which is not pup height on
  the heated side. Read the pups and the box camera for the truth. Move the sensor and the
  band has to be re-derived.
- The pups are the real thermometer: piled on each other = cold, spread away from the
  heat = too hot.

## Red flags — say to call the vet NOW (don't coach through these)
- **Hard straining for more than ~30–60 minutes with no puppy**, or **more than ~2–4
  hours between puppies** when more are expected.
- More than ~24 hours past the temperature drop with no labor started.
- Heavy fresh bleeding, foul or black/green discharge **before** the first pup, a pup
  visibly stuck in the canal, or a dam in obvious distress/collapse.
- A neonate that is cold, limp, gasping, or losing weight day over day.
