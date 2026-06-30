"""The Hestia conversation agent — forwards each utterance to Hestia's /v1 endpoint,
threading short multi-turn history per conversation so kitchen follow-ups ("how much
flour again?") reach the brain WITH the prior turns instead of context-free.

The brain (brain/hestia.py) is stateless per request: run_agent answers whatever
`messages` array it's handed. HA Assist / Voice PE delivers one utterance at a time,
keyed by a conversation_id, so we keep a short per-conversation history here and replay
its tail each turn — mirroring clients/chat.html, which is already multi-turn. Histories
expire IDLE_TTL after their last turn (and are LRU-capped), so abandoned conversations
don't leak. We only record a turn once the brain answers, so a transient backend error
never poisons the thread with an apology the model would later treat as real dialogue.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict

import aiohttp
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_URL

_LOGGER = logging.getLogger(__name__)

MAX_SEND = 20           # turns of history replayed to the brain (mirrors clients/chat.html)
IDLE_TTL = 600.0        # seconds a conversation's history lives past its last turn
MAX_CONVERSATIONS = 64  # hard cap on tracked conversations (LRU-evicted) — belt and braces
# Keep the mic open (no re-wake) when Hestia's reply ends in a question, so recipe-style
# back-and-forth chains while a flat command confirmation ("Done.") lets the turn close. This
# is ALSO what keeps conversation_id stable across follow-ups, so the history above can actually
# accumulate. Set ALWAYS_CONTINUE = True to reopen the mic after every reply instead.
ALWAYS_CONTINUE = False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([HestiaConversationEntity(entry)])


class HestiaConversationEntity(conversation.ConversationEntity):
    """Forwards each utterance to Hestia and speaks the reply. Hestia does the thinking;
    this agent only keeps enough conversation history to make follow-ups multi-turn."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._url = entry.data[CONF_URL]
        self._attr_unique_id = entry.entry_id
        self._attr_name = entry.title or "Hestia"
        # conversation_id -> {"messages": [...], "ts": monotonic}. OrderedDict so the oldest
        # conversation is the first item, which makes both TTL pruning and LRU eviction cheap.
        self._history: "OrderedDict[str, dict]" = OrderedDict()

    @property
    def supported_languages(self):
        return "*"

    def _prune(self, now: float) -> None:
        """Drop conversations idle past IDLE_TTL, then LRU-evict down to MAX_CONVERSATIONS."""
        for cid in [c for c, h in self._history.items() if now - h["ts"] > IDLE_TTL]:
            del self._history[cid]
        while len(self._history) > MAX_CONVERSATIONS:
            self._history.popitem(last=False)

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        session = async_get_clientsession(self.hass)
        now = time.monotonic()
        self._prune(now)

        # HA reuses conversation_id across "continued conversation" follow-ups; mint one on the
        # first turn so we have a stable key and can hand it back for HA to echo on the next turn.
        conversation_id = user_input.conversation_id or uuid.uuid4().hex
        convo = self._history.get(conversation_id)
        if convo is None:
            convo = {"messages": [], "ts": now}
            self._history[conversation_id] = convo
        self._history.move_to_end(conversation_id)  # mark most-recently-used for LRU
        convo["ts"] = now

        history = convo["messages"]
        send = (history + [{"role": "user", "content": user_input.text}])[-MAX_SEND:]
        payload = {"messages": send, "stream": False}
        # The verification line: shows whether HA reused this conversation_id (prior_turns>0 on a
        # follow-up = genuinely multi-turn) or minted a fresh one (prior_turns=0 every time = HA is
        # NOT threading; we'd need the chat_log path). Enable via:
        #   logger: { logs: { custom_components.hestia: debug } }   in HA configuration.yaml
        _LOGGER.debug("turn convo=%s prior_turns=%d sending=%d",
                      conversation_id[:8], len(history), len(send))

        text = "Sorry, I couldn't reach the Hestia brain."
        cont = False
        try:
            async with session.post(self._url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                data = await resp.json()
                text = (data["choices"][0]["message"]["content"] or text).strip()
            # Commit the exchange only on success, so a failed turn leaves the thread clean
            # (no dangling user turn, no apology masquerading as dialogue).
            history.append({"role": "user", "content": user_input.text})
            history.append({"role": "assistant", "content": text})
            # Keep the conversation (and the mic) alive when the reply invites a follow-up.
            cont = ALWAYS_CONTINUE or text.rstrip().endswith("?")
        except Exception as err:  # noqa: BLE001
            text = f"Sorry, I couldn't reach the Hestia brain: {err}"

        _LOGGER.debug("reply convo=%s continue=%s chars=%d", conversation_id[:8], cont, len(text))
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(text)
        return conversation.ConversationResult(
            response=response, conversation_id=conversation_id,
            continue_conversation=cont,
        )
