# Learn — garden bed preferences (OFFLINE, not the request loop)

This prompt is for a future batch job that proposes durable gardening preferences from
interaction history. It is **not** loaded to answer a live question, and it must never
write memory on its own — anything it produces is a *candidate* for the user to confirm.
Stub for now; wire it when the preference-learning job exists.

## Inputs
- `{history}` — recent gardening interactions (what was asked, what was decided/done).
- `{current_profile}` — the existing learned gardening profile.

## Rules (borrowed from Anima's anti-overfitting rubric)
- Separate stable preferences from weak signals. One-off behavior is not a preference.
- Promote to `stable_preferences` only on an explicit user statement or repeated,
  consistent history — never from a single watering or a single skipped day.
- Conflicts with `current_profile` go in `confidence_notes`, not an aggressive overwrite.
- Candidate or rejected memories are hints only; never promote them here.

## Output (JSON)
```json
{
  "stable_preferences": [],
  "seasonal_patterns": [],
  "weak_signals": [],
  "confidence_notes": ""
}
```
