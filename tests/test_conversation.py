"""Tests for Home Assistant chat-log request mapping."""

from unittest.mock import AsyncMock

from homeassistant.components.conversation.chat_log import (
    AssistantContent,
    ChatLog,
    UserContent,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openwebui_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_SERVER_SIDE_TOOLS_ENABLED,
    CONF_STREAMING_ENABLED,
    CONF_TOOL_IDS,
    DOMAIN,
)
from custom_components.openwebui_conversation.conversation import OpenWebUIAgent


def _entry(options=None):
    return MockConfigEntry(
        domain=DOMAIN,
        title="OpenWebUI - Test",
        data={
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options=options or {},
    )


def _add_turn(chat_log: ChatLog, user: str, assistant: str) -> None:
    chat_log.async_add_user_content(UserContent(content=user))
    chat_log.async_add_assistant_content_without_tools(
        AssistantContent(agent_id="conversation.openwebui_test", content=assistant)
    )


async def test_chat_log_is_bounded_and_current_turn_is_not_duplicated(hass) -> None:
    """HA ChatLog is authoritative and only the configured recent turns are sent."""
    agent = OpenWebUIAgent(hass, _entry({CONF_MAX_HISTORY: 1}))
    chat_log = ChatLog(hass, "conversation-id")
    _add_turn(chat_log, "first user", "first assistant")
    _add_turn(chat_log, "second user", "second assistant")
    chat_log.async_add_user_content(UserContent(content="search the web for weather"))

    messages = agent._messages_from_chat_log(chat_log, "weather")

    assert messages == [
        {"role": "user", "content": "second user"},
        {"role": "assistant", "content": "second assistant"},
        {"role": "user", "content": "weather"},
    ]


async def test_query_maps_options_to_one_request(hass) -> None:
    """Tools, search, streaming, history, and model are composed once."""
    agent = OpenWebUIAgent(
        hass,
        _entry(
            {
                CONF_MODEL: "test-model",
                CONF_SERVER_SIDE_TOOLS_ENABLED: True,
                CONF_TOOL_IDS: "server:mcp:home-assistant\nweather",
                CONF_STREAMING_ENABLED: True,
            }
        ),
    )
    agent.client.async_generate = AsyncMock(
        return_value={"choices": [{"message": {"content": "done"}}]}
    )
    chat_log = ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(UserContent(content="current request"))

    await agent.query("current request", chat_log, search=True)

    payload = agent.client.async_generate.await_args.args[0]
    assert payload == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "current request"}],
        "stream": True,
        "features": {"web_search": True},
        "tool_ids": ["server:mcp:home-assistant", "weather"],
    }
    assert "tools" not in payload
