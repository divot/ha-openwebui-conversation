"""Tests for final response extraction."""

import pytest

from custom_components.openwebui_conversation.exceptions import ApiJsonError
from custom_components.openwebui_conversation.response import (
    extract_assistant_text,
    extract_stream_event_text,
)


def test_plain_text_response() -> None:
    """OpenAI-compatible plain text is returned."""
    assert (
        extract_assistant_text(
            {"choices": [{"message": {"role": "assistant", "content": "Hello"}}]}
        )
        == "Hello"
    )


def test_structured_content_ignores_reasoning_and_citations() -> None:
    """Only user-facing text parts are returned."""
    response = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "reasoning", "text": "private reasoning"},
                        {"type": "text", "text": "The answer"},
                    ]
                }
            }
        ],
        "sources": [{"url": "https://example.invalid"}],
    }
    assert extract_assistant_text(response) == "The answer"


def test_tool_assisted_output_returns_last_assistant_message() -> None:
    """Pre-tool text and tool structures are not exposed as the final answer."""
    response = {
        "choices": [{"message": {"content": "Speculative pre-tool answer"}}],
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I'll check."}],
            },
            {"type": "function_call", "name": "get_state", "arguments": "{}"},
            {"type": "function_call_output", "output": "off"},
            {"type": "reasoning", "content": "private"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "The light is off."}],
            },
        ],
    }
    assert extract_assistant_text(response) == "The light is off."


def test_openwebui_completion_event_uses_nested_final_output() -> None:
    """Open WebUI's event-emitter envelope is parsed without exposing tool data."""
    event = {
        "type": "chat:completion",
        "data": {
            "done": True,
            "output": [
                {"type": "function_call_output", "output": "secret tool data"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Final answer"}],
                },
            ],
        },
    }
    assert extract_stream_event_text(event) == ("", "Final answer")


def test_openwebui_stream_error_envelope() -> None:
    """Open WebUI event-emitter failures stop the response."""
    event = {
        "type": "chat:message:error",
        "data": {"error": {"content": "tool failed"}},
    }
    with pytest.raises(ApiJsonError, match="tool failed"):
        extract_stream_event_text(event)


def test_openwebui_completion_error_envelope() -> None:
    """A failure nested in a completion envelope is not treated as empty text."""
    event = {
        "type": "chat:completion",
        "data": {"error": {"detail": "provider failed"}},
    }
    with pytest.raises(ApiJsonError, match="provider failed"):
        extract_stream_event_text(event)


@pytest.mark.parametrize("response", [{}, {"choices": []}, {"choices": [{}]}])
def test_empty_or_malformed_response(response) -> None:
    """An absent final answer is surfaced instead of being spoken as JSON."""
    with pytest.raises(ApiJsonError, match="empty final response"):
        extract_assistant_text(response)
