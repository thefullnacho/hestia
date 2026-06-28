"""Hestia Brain — points Home Assistant's conversation agent at Hestia's /v1.

This is the "seam": HA Assist (text or voice) forwards each utterance to Hestia,
which owns the loop — recall memory, call tools (incl. controlling HA back), reply.
HA just speaks the answer. HA is an input device + a tool, not the brain.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
