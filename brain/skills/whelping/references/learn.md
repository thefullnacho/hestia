# Learn — kennel preferences (OFFLINE, not the request loop)

For a future batch job that proposes durable breeding-program preferences from
interaction history (e.g. a preferred whelping-box setup, this dam's typical litter
size, a chosen vet). It is **not** loaded to answer a live question and must never
write memory on its own — its output is a *candidate* for the user to confirm. Stub
for now.

## Inputs
- `{history}` — recent kennel/breeding interactions.
- `{current_profile}` — the existing learned kennel profile.

## Rules (Anima's anti-overfitting rubric)
- Stable preferences need an explicit statement or repeated, consistent history — never
  a single litter or a one-off decision.
- Per-dam patterns (litter size, gestation length) are observations to surface with
  uncertainty when data is sparse, not hard rules.
- Conflicts with `current_profile` go in `confidence_notes`, not an overwrite.

## Output (JSON)
```json
{
  "stable_preferences": [],
  "per_dam_patterns": [],
  "weak_signals": [],
  "confidence_notes": ""
}
```
