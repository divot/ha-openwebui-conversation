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


def test_streaming_selection() -> None:
    """Streaming selection maps directly to the Open WebUI request."""
    assert _payload(stream=True)["stream"] is True
