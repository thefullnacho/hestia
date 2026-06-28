---
name: home_control
description: Use when the user wants to control or check a light or device in the house — turn lights on/off, dim/brighten, check a light's state. Scopes the request to the home tool so the model fires it instead of drowning in the full tool list.
triggers: light, lights, lamp, lamps, lamppost, dim, brighten, lighting, thermostat, blinds, plug, outlet, light strip
tools: home
metadata:
  domain: home
  version: 0.1.0
---

# Home Control

The user is controlling or querying a light/device. Call the `home` tool:
- on/off: `action='turn_on'` / `action='turn_off'` with the exact `entity_id` from the
  light catalog. For a whole room use its group (`light.light_<room>_lights`).
- state: `action='get_state'`, or just answer from the light catalog already in context.

Pick the entity_id from the catalog — do not invent one. Fire the tool; don't deliberate
about which tool to use (this request is yours).
