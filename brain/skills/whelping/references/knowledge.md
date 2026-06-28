# Whelping & kennel knowledge

## Our program (Lhasa Apso, a small/toy breed)
The full, authoritative roster is in the WHO & WHAT block of the system prompt — read
names, AKC numbers, DOBs, and litter counts from there, not from here. In brief:
- **Lily** and **Fiona** are the **dams** (Fiona's first pairing with Bodhi is planned,
  no litters yet). **Bodhi** is the **sire**. **Momo** is a retired/neutered male — not
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

## Red flags — say to call the vet NOW (don't coach through these)
- **Hard straining for more than ~30–60 minutes with no puppy**, or **more than ~2–4
  hours between puppies** when more are expected.
- More than ~24 hours past the temperature drop with no labor started.
- Heavy fresh bleeding, foul or black/green discharge **before** the first pup, a pup
  visibly stuck in the canal, or a dam in obvious distress/collapse.
- A neonate that is cold, limp, gasping, or losing weight day over day.
