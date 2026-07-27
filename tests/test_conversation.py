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
    CONF_PERSISTENT_CHAT_ENABLED,
    CONF_SERVER_SIDE_TOOLS_ENABLED,
    CONF_STREAMING_ENABLED,
    CONF_TOOL_IDS,
    DOMAIN,
)
from custom_components.openwebui_conversation.conversation import OpenWebUIAgent
from custom_components.openwebui_conversation.exceptions import ApiCommError


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


async def test_persistent_chat_is_created_reused_and_completed(hass) -> None:
    """One Open WebUI chat follows the Home Assistant conversation."""
    agent = OpenWebUIAgent(
        hass,
        _entry(
            {
                CONF_MODEL: "test-model",
                CONF_PERSISTENT_CHAT_ENABLED: True,
            }
        ),
    )
    agent.client.async_create_chat = AsyncMock(return_value="openwebui-chat-id")
    agent.client.async_update_chat = AsyncMock(return_value={"id": "openwebui-chat-id"})
    agent.client.async_generate = AsyncMock(
        side_effect=[
            {"choices": [{"message": {"content": "first answer"}}]},
            {"choices": [{"message": {"content": "second answer"}}]},
        ]
    )
    chat_log = ChatLog(hass, "ha-conversation-id")
    chat_log.async_add_user_content(UserContent(content="first request"))

    await agent.query("first request", chat_log, search=False)

    agent.client.async_create_chat.assert_awaited_once()
    created_chat = agent.client.async_create_chat.await_args.args[0]
    created_history = created_chat["history"]
    created_messages = created_history["messages"]
    response_message_id = created_history["currentId"]
    assert created_chat["title"] == "Home Assistant: first request"
    assert created_messages[response_message_id]["content"] == ""
    assert created_messages[response_message_id]["done"] is False

    first_payload = agent.client.async_generate.await_args_list[0].args[0]
    assert first_payload["chat_id"] == "openwebui-chat-id"
    assert first_payload["id"] == response_message_id
    assert "session_id" not in first_payload

    first_completed_chat = agent.client.async_update_chat.await_args_list[0].args[1]
    assert (
        first_completed_chat["history"]["messages"][response_message_id]["content"]
        == "first answer"
    )
    assert (
        first_completed_chat["history"]["messages"][response_message_id]["done"] is True
    )

    chat_log.async_add_assistant_content_without_tools(
        AssistantContent(agent_id="conversation.openwebui_test", content="first answer")
    )
    chat_log.async_add_user_content(UserContent(content="second request"))
    await agent.query("second request", chat_log, search=False)

    agent.client.async_create_chat.assert_awaited_once()
    assert agent.client.async_update_chat.await_count == 3
    second_payload = agent.client.async_generate.await_args_list[1].args[0]
    assert second_payload["chat_id"] == "openwebui-chat-id"
    second_completed_chat = agent.client.async_update_chat.await_args_list[2].args[1]
    second_current_id = second_completed_chat["history"]["currentId"]
    assert (
        second_completed_chat["history"]["messages"][second_current_id]["content"]
        == "second answer"
    )


async def test_persistent_chat_mapping_survives_agent_reload(hass) -> None:
    """The Home Assistant conversation reuses its native chat after reload."""
    entry = _entry({CONF_PERSISTENT_CHAT_ENABLED: True})
    first_agent = OpenWebUIAgent(hass, entry)
    first_agent.client.async_create_chat = AsyncMock(return_value="stored-chat-id")
    first_agent.client.async_update_chat = AsyncMock(
        return_value={"id": "stored-chat-id"}
    )
    first_agent.client.async_generate = AsyncMock(
        return_value={"choices": [{"message": {"content": "first answer"}}]}
    )
    chat_log = ChatLog(hass, "stored-ha-conversation")
    chat_log.async_add_user_content(UserContent(content="first request"))

    await first_agent.query("first request", chat_log, search=False)

    reloaded_agent = OpenWebUIAgent(hass, entry)
    reloaded_agent.client.async_create_chat = AsyncMock(return_value="new-chat-id")
    reloaded_agent.client.async_update_chat = AsyncMock(
        return_value={"id": "stored-chat-id"}
    )
    reloaded_agent.client.async_generate = AsyncMock(
        return_value={"choices": [{"message": {"content": "second answer"}}]}
    )
    reloaded_log = ChatLog(hass, "stored-ha-conversation")
    reloaded_log.async_add_user_content(UserContent(content="second request"))

    await reloaded_agent.query("second request", reloaded_log, search=False)

    reloaded_agent.client.async_create_chat.assert_not_awaited()
    assert reloaded_agent.client.async_update_chat.await_args_list[0].args[0] == (
        "stored-chat-id"
    )
    assert (
        reloaded_agent.client.async_generate.await_args.args[0]["chat_id"]
        == "stored-chat-id"
    )


async def test_persistent_chat_failure_falls_back_to_stateless(hass) -> None:
    """A sidebar sync failure does not suppress the assistant response."""
    agent = OpenWebUIAgent(
        hass,
        _entry({CONF_PERSISTENT_CHAT_ENABLED: True}),
    )
    agent.client.async_create_chat = AsyncMock(
        side_effect=ApiCommError("chat endpoint unavailable")
    )
    agent.client.async_update_chat = AsyncMock()
    agent.client.async_generate = AsyncMock(
        return_value={"choices": [{"message": {"content": "answer"}}]}
    )
    chat_log = ChatLog(hass, "fallback-conversation")
    chat_log.async_add_user_content(UserContent(content="request"))

    response = await agent.query("request", chat_log, search=False)

    assert response["choices"][0]["message"]["content"] == "answer"
    payload = agent.client.async_generate.await_args.args[0]
    assert "chat_id" not in payload
    assert "id" not in payload
    agent.client.async_update_chat.assert_not_awaited()
