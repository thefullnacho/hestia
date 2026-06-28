"""Config flow for the Hestia conversation integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow

from .const import CONF_URL, DEFAULT_URL, DOMAIN


class HestiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-step flow: where is the Hestia brain?"""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Hestia", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str}),
        )
