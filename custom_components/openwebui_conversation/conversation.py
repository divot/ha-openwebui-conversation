"""OpenWebUI conversation agent."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from hassil import recognize
from hassil.intents import Intents

from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import AssistantContent, ChatLog
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store

from markdown_it import MarkdownIt
from mdit_plain.renderer import RendererPlain

from .api import OpenWebUIApiClient
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_LANGUAGE_CODE,
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_PERSISTENT_CHAT_ENABLED,
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
    DEFAULT_PERSISTENT_CHAT_ENABLED,
    DEFAULT_SEARCH_RESULT_PREFIX,
    DEFAULT_SEARCH_SENTENCES,
    DEFAULT_SERVER_SIDE_TOOLS_ENABLED,
    DEFAULT_STREAMING_ENABLED,
    DEFAULT_STRIP_MARKDOWN,
    DEFAULT_TIMEOUT,
    DEFAULT_TOOL_IDS,
    DEFAULT_VERIFY_SSL,
    DO_SEARCH_INTENT,
    DOMAIN,
    LOGGER,
    SEARCH_MODE_NATIVE,
    SEARCH_MODE_TRIGGER,
)
from .exceptions import ApiClientError
from .request import build_chat_completion_payload, normalize_tool_ids
from .response import extract_assistant_text
from .search import search_mode_from_options

_CHAT_ID_STORAGE_VERSION = 1


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
    _attr_name = None

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
        self.search_mode = search_mode_from_options(entry.options)
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
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Open WebUI",
            model=entry.options.get(CONF_MODEL, DEFAULT_MODEL),
            configuration_url=entry.data[CONF_BASE_URL],
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        self.strip_markdown = entry.options.get(
            CONF_STRIP_MARKDOWN, DEFAULT_STRIP_MARKDOWN
        )
        self.markdown_parser = MarkdownIt(renderer_cls=RendererPlain)
        self._chat_id_store = Store[dict[str, str]](
            hass,
            _CHAT_ID_STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.persistent_chat_ids",
        )
        self._persistent_chat_ids: dict[str, str] | None = None
        self._persistent_chat_lock = asyncio.Lock()

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
        if self.search_mode != SEARCH_MODE_TRIGGER or not self.search_sentences:
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
        messages = self._all_messages_from_chat_log(chat_log, current_prompt)

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

    @staticmethod
    def _all_messages_from_chat_log(
        chat_log: ChatLog, current_prompt: str
    ) -> list[dict[str, str]]:
        """Convert all displayable Home Assistant chat content to messages."""
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

        return messages

    def _stable_message_id(self, conversation_id: str, role: str, index: int) -> str:
        """Return a stable UUID for an Open WebUI message."""
        return str(
            uuid5(
                NAMESPACE_URL,
                f"{DOMAIN}:{self.entry.entry_id}:{conversation_id}:{role}:{index}",
            )
        )

    @staticmethod
    def _persistent_chat_title(messages: list[dict[str, str]]) -> str:
        """Build a concise, recognizable Open WebUI sidebar title."""
        first_user_message = next(
            (message["content"] for message in messages if message["role"] == "user"),
            "",
        )
        title = " ".join(first_user_message.split())
        if len(title) > 80:
            title = f"{title[:77].rstrip()}..."
        return f"Home Assistant: {title}" if title else "Home Assistant"

    def _build_persistent_chat(
        self,
        *,
        chat_log: ChatLog,
        conversation_id: str,
        current_prompt: str,
        model: str,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """Build Open WebUI's persistent, tree-shaped chat representation."""
        messages = self._all_messages_from_chat_log(chat_log, current_prompt)
        now = int(time.time())
        history_messages: dict[str, dict[str, Any]] = {}
        previous_id: str | None = None

        for index, message in enumerate(messages):
            message_id = self._stable_message_id(
                conversation_id, message["role"], index
            )
            persistent_message: dict[str, Any] = {
                "id": message_id,
                "parentId": previous_id,
                "childrenIds": [],
                "role": message["role"],
                "content": message["content"],
                "timestamp": now - (len(messages) - index),
            }
            if message["role"] == "assistant":
                persistent_message.update({"model": model, "done": True})
            history_messages[message_id] = persistent_message
            if previous_id is not None:
                history_messages[previous_id]["childrenIds"].append(message_id)
            previous_id = message_id

        response_id = self._stable_message_id(
            conversation_id, "assistant", len(messages)
        )
        response_message = {
            "id": response_id,
            "parentId": previous_id,
            "childrenIds": [],
            "role": "assistant",
            "content": "",
            "model": model,
            "modelName": model,
            "modelIdx": 0,
            "timestamp": now,
            "done": False,
        }
        history_messages[response_id] = response_message
        if previous_id is not None:
            history_messages[previous_id]["childrenIds"].append(response_id)
        assert previous_id is not None
        current_user_message = history_messages[previous_id]

        return (
            {
                "title": self._persistent_chat_title(messages),
                "models": [model],
                "params": {},
                "history": {
                    "messages": history_messages,
                    "currentId": response_id,
                },
                "messages": list(history_messages.values()),
                "tags": [],
                "timestamp": int(time.time() * 1000),
            },
            response_id,
            current_user_message,
        )

    @staticmethod
    def _build_persistent_follow_up(
        chat: dict[str, Any], current_prompt: str
    ) -> tuple[str, dict[str, Any]]:
        """Build one new turn using the server-owned chat as its parent."""
        history = chat.get("history")
        history = history if isinstance(history, dict) else {}
        history_messages = history.get("messages")
        history_messages = (
            history_messages if isinstance(history_messages, dict) else {}
        )
        current_id = history.get("currentId")
        parent_id = (
            current_id
            if isinstance(current_id, str) and current_id in history_messages
            else None
        )
        response_id = str(uuid4())
        user_message = {
            "id": str(uuid4()),
            "parentId": parent_id,
            "childrenIds": [response_id],
            "role": "user",
            "content": current_prompt,
            "timestamp": int(time.time()),
        }
        return response_id, user_message

    async def _async_load_persistent_chat_ids(self) -> None:
        """Load the Home Assistant to Open WebUI chat ID mapping once."""
        if self._persistent_chat_ids is not None:
            return
        try:
            stored = await self._chat_id_store.async_load()
        except HomeAssistantError as err:
            LOGGER.warning(
                "Unable to load the persistent Open WebUI chat mapping: %s", err
            )
            stored = None
        self._persistent_chat_ids = (
            {
                conversation_id: chat_id
                for conversation_id, chat_id in stored.items()
                if isinstance(conversation_id, str)
                and isinstance(chat_id, str)
                and chat_id
            }
            if isinstance(stored, dict)
            else {}
        )

    async def _async_prepare_persistent_chat(
        self,
        *,
        conversation_id: str,
        chat_log: ChatLog,
        current_prompt: str,
        model: str,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """Prepare native Open WebUI message metadata for one completion."""
        async with self._persistent_chat_lock:
            await self._async_load_persistent_chat_ids()
            assert self._persistent_chat_ids is not None

            if chat_id := self._persistent_chat_ids.get(conversation_id):
                try:
                    chat = await self.client.async_get_chat(chat_id)
                    response_id, user_message = self._build_persistent_follow_up(
                        chat, current_prompt
                    )
                    return chat_id, response_id, user_message
                except ApiClientError as err:
                    LOGGER.warning(
                        "Unable to load persistent Open WebUI chat; "
                        "attempting to create a replacement: %s",
                        err,
                    )

            chat, response_id, user_message = self._build_persistent_chat(
                chat_log=chat_log,
                conversation_id=conversation_id,
                current_prompt=current_prompt,
                model=model,
            )
            try:
                chat_id = await self.client.async_create_chat(chat)
            except ApiClientError as err:
                LOGGER.warning(
                    "Unable to create a persistent Open WebUI chat; "
                    "continuing with a stateless completion: %s",
                    err,
                )
                return None, None, None

            self._persistent_chat_ids[conversation_id] = chat_id
            try:
                await self._chat_id_store.async_save(self._persistent_chat_ids)
            except HomeAssistantError as err:
                LOGGER.warning(
                    "Unable to save the persistent Open WebUI chat mapping: %s", err
                )
            return chat_id, response_id, user_message

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
        configured_stream = self.entry.options.get(
            CONF_STREAMING_ENABLED, DEFAULT_STREAMING_ENABLED
        )
        tools_active = tools_enabled and bool(tool_ids)
        native_search_active = self.search_mode == SEARCH_MODE_NATIVE
        # Open WebUI's native server-side tool loop is implemented by its
        # streaming response handler. Keep the user option for ordinary
        # conversations, but never send a native tool request in the
        # non-streaming mode that returns only the first tool-call turn.
        tool_loop_active = tools_active or native_search_active
        stream = configured_stream or tool_loop_active
        web_search = search or native_search_active
        web_search_mode: str | None = None
        if native_search_active:
            web_search_mode = SEARCH_MODE_NATIVE
        elif search:
            web_search_mode = SEARCH_MODE_TRIGGER
        messages = self._messages_from_chat_log(chat_log, prompt)
        persistent_chat_enabled = self.entry.options.get(
            CONF_PERSISTENT_CHAT_ENABLED, DEFAULT_PERSISTENT_CHAT_ENABLED
        )
        conversation_id = chat_log.conversation_id
        chat_id: str | None = None
        message_id: str | None = None
        persistent_chat_id: str | None = None
        persistent_user_message: dict[str, Any] | None = None

        if persistent_chat_enabled and conversation_id:
            (
                persistent_chat_id,
                message_id,
                persistent_user_message,
            ) = await self._async_prepare_persistent_chat(
                conversation_id=conversation_id,
                chat_log=chat_log,
                current_prompt=prompt,
                model=model,
            )
            chat_id = persistent_chat_id
        elif persistent_chat_enabled:
            LOGGER.warning(
                "Cannot create a persistent Open WebUI chat without a "
                "Home Assistant conversation ID"
            )

        if tool_loop_active and not chat_id:
            # Open WebUI only enters its native server-tool loop when both
            # chat_id and message id are present. A local chat provides the
            # required event-emitter context without creating a sidebar chat
            # or requiring a WebSocket session.
            chat_id = f"local:{uuid4()}"
            message_id = str(uuid4())

        payload = build_chat_completion_payload(
            model=model,
            messages=messages,
            stream=stream,
            web_search=web_search,
            server_side_tools_enabled=tools_enabled,
            tool_ids=tool_ids,
            function_calling="native" if native_search_active else None,
            web_search_mode=web_search_mode,
            chat_id=chat_id,
            message_id=message_id,
            parent_id=(
                persistent_user_message.get("parentId")
                if persistent_user_message is not None
                else None
            ),
            user_message=persistent_user_message,
        )

        LOGGER.debug(
            "Sending Open WebUI request (model=%s, messages=%d, tool_ids=%s, "
            "web_search=%s, web_search_mode=%s, stream=%s, persistent_chat=%s)",
            model,
            len(messages),
            tool_ids if tools_enabled else [],
            web_search,
            web_search_mode,
            stream,
            bool(persistent_chat_id),
        )
        response = await self.client.async_generate(payload)

        return response

    async def _async_entry_update_listener(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Reload the entry after options change."""
        await hass.config_entries.async_reload(entry.entry_id)
