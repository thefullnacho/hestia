# Deciding whether a bed needs water

Follow this when asked "does bed X need water?", "should I water?", or anything that
turns on the state of a bed.

## Steps
1. **Get the named bed's moisture — by tool call, not by eye.** Call `home` `get_state`
   with the bed's name as `entity_id` (e.g. `entity_id='Carrots'` — the tool resolves the
   name to the right sensor). Use the single value it returns and confirm the name it
   echoes matches what was asked. Do not read the number off the catalog list and do not
   guess an entity_id: with several beds shown, the wrong row is easy to grab. If the
   user named no bed, ask which one.
2. **Get the rain outlook.** Call `weather` with `action='rain'` (or `'briefing'` if
   frost might also matter). What you need is whether meaningful rain (≥ ~0.1 in) lands
   in the next ~3 days.
3. **Decide** with both numbers in hand.

## Decision
- **Dry (≤40%) AND no meaningful rain in ~3 days** → recommend watering it.
- **Dry (≤40%) BUT rain is coming** → say it's dry but hold off; the rain has it. Name
  the day/amount so they can judge.
- **Saturated (≥95%), especially after it's been that high for a couple of days** →
  don't water; flag it as waterlogged and suggest checking drainage.
- **Comfortable (mid-range)** → it's fine, leave it.
- **Reads 0% or a known-bad channel** → don't call it a drought; say the reading looks
  off and the sensor may be down (see knowledge.md).

## Answering
Give the bed, its reading, the relevant rain fact, and the call — in one or two plain
sentences. Example: "Tomatoes are at 32% and there's no real rain through Thursday, so
I'd water them. Carrots are fine at 61%."
