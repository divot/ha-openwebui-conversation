"""Tests for final response extraction."""

import pytest

from custom_components.openwebui_conversation.exceptions import ApiJsonError
from custom_components.openwebui_conversation.response import (
    extract_assistant_text,
    extract_stream_event_text,
    stream_event_has_tool_call,
    summarize_response_shape,
    summarize_stream_event_shape,
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


def test_pre_tool_message_is_not_mistaken_for_final_output() -> None:
    """Text before an unfinished function call is not safe spoken output."""
    response = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I'll check."}],
            },
            {"type": "function_call", "name": "get_state", "arguments": "{}"},
        ]
    }
    with pytest.raises(ApiJsonError, match="unfinished native tool call"):
        extract_assistant_text(response)


def test_chat_completion_tool_call_only_is_actionable() -> None:
    """A native first-turn tool call is distinguished from malformed output."""
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "get_state", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    with pytest.raises(ApiJsonError, match="unfinished native tool call"):
        extract_assistant_text(response)


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


def test_responses_api_text_events() -> None:
    """Native Responses API delta and final-text events are supported."""
    assert extract_stream_event_text(
        {"type": "response.output_text.delta", "delta": "Hello"}
    ) == ("Hello", None)
    assert extract_stream_event_text(
        {"type": "response.output_text.done", "text": "Hello world"}
    ) == ("", "Hello world")


def test_responses_api_completed_event_uses_response_output() -> None:
    """The authoritative output nested under response.completed is extracted."""
    event = {
        "type": "response.completed",
        "response": {
            "output": [
                {"type": "function_call", "name": "get_state", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "output": "private result",
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Final answer"}],
                },
            ]
        },
    }
    assert extract_stream_event_text(event) == ("", "Final answer")
    assert stream_event_has_tool_call(event)


def test_stream_tool_call_detection_covers_both_protocols() -> None:
    """Chat Completions and Responses function calls are recognized."""
    assert stream_event_has_tool_call(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"function": {"name": "get_state", "arguments": ""}}
                        ]
                    }
                }
            ]
        }
    )
    assert stream_event_has_tool_call(
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "name": "get_state"},
        }
    )


def test_shape_diagnostics_never_include_content_or_arguments() -> None:
    """Structural summaries omit response text, tool arguments, and results."""
    secret = "sensitive-household-data"
    response = {
        "choices": [
            {
                "message": {
                    "content": secret,
                    "tool_calls": [
                        {"function": {"name": "get_state", "arguments": secret}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "output": [{"type": "function_call_output", "output": secret}],
    }
    event = {
        "type": "response.output_text.delta",
        "delta": secret,
        "data": {"content": secret},
    }

    assert secret not in repr(summarize_response_shape(response))
    assert secret not in repr(summarize_stream_event_shape(event))


def test_openwebui_stream_error_envelope() -> None:
    """Open WebUI event-emitter failures stop the response."""
    event = {
        "type": "chat:message:error",
        "data": {"error": {"content": "tool failed"}},
    }
    with pytest.raises(ApiJsonError, match="chat:message:error"):
        extract_stream_event_text(event)


def test_openwebui_completion_error_envelope() -> None:
    """A failure nested in a completion envelope is not treated as empty text."""
    event = {
        "type": "chat:completion",
        "data": {"error": {"detail": "provider failed"}},
    }
    with pytest.raises(ApiJsonError, match="chat:completion"):
        extract_stream_event_text(event)


def test_responses_api_error_does_not_expose_error_body() -> None:
    """Responses API failures retain their type without logging private detail."""
    secret = "sensitive-household-data"
    event = {
        "type": "response.failed",
        "response": {"error": {"message": secret}},
    }
    with pytest.raises(ApiJsonError, match="response.failed") as err:
        extract_stream_event_text(event)
    assert secret not in str(err.value)


@pytest.mark.parametrize("response", [{}, {"choices": []}, {"choices": [{}]}])
def test_empty_or_malformed_response(response) -> None:
    """An absent final answer is surfaced instead of being spoken as JSON."""
    with pytest.raises(ApiJsonError, match="empty final response"):
        extract_assistant_text(response)
