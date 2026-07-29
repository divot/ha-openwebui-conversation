"""Tests for Open WebUI request construction."""

from custom_components.openwebui_conversation.request import (
    build_chat_completion_payload,
    normalize_tool_ids,
)

MESSAGES = [{"role": "user", "content": "Hello"}]


def _payload(**overrides):
    values = {
        "model": "test-model",
        "messages": MESSAGES,
        "stream": False,
        "web_search": False,
        "server_side_tools_enabled": False,
        "tool_ids": None,
    }
    values.update(overrides)
    return build_chat_completion_payload(**values)


def test_legacy_payload_without_tools() -> None:
    """No tool configuration preserves the existing request shape."""
    assert _payload() == {
        "model": "test-model",
        "messages": MESSAGES,
        "stream": False,
        "features": {"web_search": False},
    }


def test_one_tool_id() -> None:
    """One explicitly enabled tool ID is sent."""
    payload = _payload(
        server_side_tools_enabled=True,
        tool_ids="server:mcp:home-assistant",
    )
    assert payload["tool_ids"] == ["server:mcp:home-assistant"]
    assert "tools" not in payload


def test_multiple_tool_ids_are_normalized() -> None:
    """Whitespace, blanks, and duplicate IDs are normalized."""
    tool_ids = "  server:mcp:home-assistant\n\nweather\nweather\n  workspace "
    assert normalize_tool_ids(tool_ids) == [
        "server:mcp:home-assistant",
        "weather",
        "workspace",
    ]
    assert _payload(server_side_tools_enabled=True, tool_ids=tool_ids)["tool_ids"] == [
        "server:mcp:home-assistant",
        "weather",
        "workspace",
    ]


def test_empty_or_disabled_tools_are_omitted() -> None:
    """Empty IDs and disabled server-side tools do not alter the request."""
    assert "tool_ids" not in _payload(server_side_tools_enabled=True, tool_ids=" \n ")
    assert "tool_ids" not in _payload(
        server_side_tools_enabled=False, tool_ids="server:mcp:home-assistant"
    )


def test_tools_and_web_search_coexist_without_caller_tools() -> None:
    """Web search remains a feature while selected tools use tool_ids."""
    payload = _payload(
        server_side_tools_enabled=True,
        tool_ids=["server:mcp:home-assistant", "weather"],
        web_search=True,
    )
    assert payload["features"] == {"web_search": True}
    assert payload["tool_ids"] == ["server:mcp:home-assistant", "weather"]
    assert "tools" not in payload


def test_function_calling_mode_is_explicit_only_when_requested() -> None:
    """Search modes can select native or legacy handling without a tools field."""
    assert "params" not in _payload()
    assert _payload(function_calling="native")["params"] == {
        "function_calling": "native"
    }
    assert _payload(function_calling="legacy")["params"] == {
        "function_calling": "legacy"
    }


def test_web_search_mode_is_scoped_to_enabled_search() -> None:
    """The server can distinguish trigger and agentic search without changing tools."""
    assert _payload(web_search_mode="native")["features"] == {"web_search": False}
    assert _payload(
        web_search=True,
        web_search_mode="native",
    )["features"] == {
        "web_search": True,
        "web_search_mode": "native",
    }


def test_streaming_selection() -> None:
    """Streaming selection maps directly to the Open WebUI request."""
    assert _payload(stream=True)["stream"] is True


def test_persistent_chat_metadata() -> None:
    """Native message metadata is included only when a chat is selected."""
    user_message = {
        "id": "user-message-id",
        "parentId": "previous-assistant-id",
        "childrenIds": ["assistant-message-id"],
        "role": "user",
        "content": "Hello",
    }
    payload = _payload(
        chat_id="chat-id",
        message_id="assistant-message-id",
        parent_id="previous-assistant-id",
        user_message=user_message,
    )
    assert payload["chat_id"] == "chat-id"
    assert payload["id"] == "assistant-message-id"
    assert payload["parent_id"] == "previous-assistant-id"
    assert payload["user_message"] == user_message

    assert "id" not in _payload(message_id="orphan-message-id")
    assert "user_message" not in _payload(user_message=user_message)
