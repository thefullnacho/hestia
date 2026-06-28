"""The Hestia conversation agent — a thin forwarder to Hestia's /v1 endpoint."""
from __future__ import annotations

import aiohttp
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_URL


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([HestiaConversationEntity(entry)])


class HestiaConversationEntity(conversation.ConversationEntity):
    """Forwards each utterance to Hestia and speaks the reply. Hestia does the thinking."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._url = entry.data[CONF_URL]
        self._attr_unique_id = entry.entry_id
        self._attr_name = entry.title or "Hestia"

    @property
    def supported_languages(self):
        return "*"

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        session = async_get_clientsession(self.hass)
        payload = {"messages": [{"role": "user", "content": user_input.text}], "stream": False}
        text = "Sorry, I couldn't reach the Hestia brain."
        try:
            async with session.post(self._url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                data = await resp.json()
                text = (data["choices"][0]["message"]["content"] or text).strip()
        except Exception as err:  # noqa: BLE001
            text = f"Sorry, I couldn't reach the Hestia brain: {err}"
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
