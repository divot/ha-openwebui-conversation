"""OpenWebUI conversation agent."""

from __future__ import annotations

from typing import Any, Literal

from hassil import recognize
from hassil.intents import Intents

from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import AssistantContent, ChatLog
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from markdown_it import MarkdownIt
from mdit_plain.renderer import RendererPlain

from .api import OpenWebUIApiClient
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_LANGUAGE_CODE,
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_SEARCH_ENABLED,
    CONF_SEARCH_RESULT_PREFIX,
    CONF_SEARCH_SENTENCES,
    CONF_SERVER_SIDE_TOOLS_ENABLED,
    CONF_STREAMING_ENABLED,
    CONF_STRIP_MARKDOWN,
    CONF_TIMEOUT,
    CONF_TOOL_IDS,
    CONF_VERIFY_SSL,
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MODEL,
    DEFAULT_SEARCH_ENABLED,
    DEFAULT_SEARCH_RESULT_PREFIX,
    DEFAULT_SEARCH_SENTENCES,
    DEFAULT_SERVER_SIDE_TOOLS_ENABLED,
    DEFAULT_STREAMING_ENABLED,
    DEFAULT_STRIP_MARKDOWN,
    DEFAULT_TIMEOUT,
    DEFAULT_TOOL_IDS,
    DEFAULT_VERIFY_SSL,
    DO_SEARCH_INTENT,
    LOGGER,
)
from .exceptions import ApiClientError
from .request import build_chat_completion_payload, normalize_tool_ids
from .response import extract_assistant_text


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OpenWebUI conversation agent from a config entry."""
    async_add_entities([OpenWebUIAgent(hass, entry)])


class OpenWebUIAgent(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """OpenWebUI conversation agent."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self.client = OpenWebUIApiClient(
            base_url=entry.data[CONF_BASE_URL],
            api_key=entry.data[CONF_API_KEY],
            timeout=entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            session=async_get_clientsession(hass),
            verify_ssl=entry.options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
        self.search_enabled = entry.options.get(
            CONF_SEARCH_ENABLED, DEFAULT_SEARCH_ENABLED
        )
        self.search_sentences = [
            sentence.strip()
            for sentence in entry.options.get(
                CONF_SEARCH_SENTENCES, DEFAULT_SEARCH_SENTENCES
            ).splitlines()
            if sentence.strip()
        ]
        self.search_result_prefix = entry.options.get(
            CONF_SEARCH_RESULT_PREFIX, DEFAULT_SEARCH_RESULT_PREFIX
        )
        self.lang = entry.options.get(CONF_LANGUAGE_CODE, DEFAULT_LANGUAGE_CODE).strip()
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self.strip_markdown = entry.options.get(
            CONF_STRIP_MARKDOWN, DEFAULT_STRIP_MARKDOWN
        )
        self.markdown_parser = MarkdownIt(renderer_cls=RendererPlain)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register the agent and its update listener."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)
        self.entry.async_on_unload(
            self.entry.add_update_listener(self._async_entry_update_listener)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the conversation agent."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    def _match_search_trigger(self, prompt: str) -> tuple[str, bool]:
        """Return a rewritten search query when a configured sentence matches."""
        if not self.search_enabled or not self.search_sentences:
            return prompt, False

        intents = Intents.from_dict(
            {
                "language": self.lang,
                "settings": {"ignore_whitespace": True},
                "intents": {
                    DO_SEARCH_INTENT: {"data": [{"sentences": self.search_sentences}]}
                },
                "lists": {"query": {"wildcard": True}},
            }
        )
        result = recognize(prompt, intents)
        if (
            result is not None
            and result.intent.name == DO_SEARCH_INTENT
            and (query := result.entities.get("query")) is not None
        ):
            return query.value, True
        return prompt, False

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: ChatLog,
    ) -> conversation.ConversationResult:
        """Pass a Home Assistant conversation turn to Open WebUI."""
        prompt, should_search = self._match_search_trigger(user_input.text)
        intent_response = intent.IntentResponse(language=user_input.language)

        try:
            response = await self.query(prompt, chat_log, should_search)
            response_data = extract_assistant_text(response)
        except ApiClientError as err:
            LOGGER.error("Open WebUI request failed: %s", err)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "Sorry, I couldn't get a response from Open WebUI.",
            )
            return conversation.ConversationResult(
                response=intent_response,
                conversation_id=chat_log.conversation_id,
            )

        if self.strip_markdown:
            response_data = self.markdown_parser.render(response_data).strip()
        if should_search:
            response_data = f"{self.search_result_prefix} {response_data}".strip()

        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(
                agent_id=self.entity_id or user_input.agent_id,
                content=response_data,
            )
        )
        intent_response.async_set_speech(response_data)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=chat_log.continue_conversation,
        )

    def _messages_from_chat_log(
        self, chat_log: ChatLog, current_prompt: str
    ) -> list[dict[str, str]]:
        """Convert Home Assistant's bounded chat history to Open WebUI messages."""
        messages: list[dict[str, str]] = []
        for content in chat_log.content:
            if content.role not in ("system", "user", "assistant"):
                continue
            if not isinstance(content.content, str) or not content.content.strip():
                continue
            messages.append({"role": content.role, "content": content.content})

        # ChatLog already contains the current user turn. Rewrite it in place
        # when a search trigger extracted a query, avoiding a duplicate message.
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] = current_prompt
        else:
            messages.append({"role": "user", "content": current_prompt})

        max_history = self.entry.options.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY)
        system_messages = [
            message for message in messages if message["role"] == "system"
        ]
        conversation_messages = [
            message for message in messages if message["role"] != "system"
        ]
        # Retain the current user turn plus at most N prior user/assistant turns.
        max_messages = (int(max_history) * 2) + 1
        return [*system_messages, *conversation_messages[-max_messages:]]

    async def query(
        self, prompt: str, chat_log: ChatLog, search: bool
    ) -> dict[str, Any]:
        """Build and send one Open WebUI chat-completions request."""
        model = self.entry.options.get(CONF_MODEL, DEFAULT_MODEL)
        tool_ids = normalize_tool_ids(
            self.entry.options.get(CONF_TOOL_IDS, DEFAULT_TOOL_IDS)
        )
        tools_enabled = self.entry.options.get(
            CONF_SERVER_SIDE_TOOLS_ENABLED, DEFAULT_SERVER_SIDE_TOOLS_ENABLED
        )
        stream = self.entry.options.get(
            CONF_STREAMING_ENABLED, DEFAULT_STREAMING_ENABLED
        )
        messages = self._messages_from_chat_log(chat_log, prompt)
        payload = build_chat_completion_payload(
            model=model,
            messages=messages,
            stream=stream,
            web_search=search,
            server_side_tools_enabled=tools_enabled,
            tool_ids=tool_ids,
        )

        LOGGER.debug(
            "Sending Open WebUI request (model=%s, messages=%d, tool_ids=%s, web_search=%s, stream=%s)",
            model,
            len(messages),
            tool_ids if tools_enabled else [],
            search,
            stream,
        )
        return await self.client.async_generate(payload)

    async def _async_entry_update_listener(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Reload the entry after options change."""
        await hass.config_entries.async_reload(entry.entry_id)
