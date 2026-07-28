"""Response parsing for Open WebUI chat completions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .exceptions import ApiJsonError


def _text_from_content(content: Any) -> str:
    """Extract user-facing text without exposing reasoning or tool structures."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") not in ("text", "output_text"):
            continue
        if isinstance(text := part.get("text"), str):
            parts.append(text)
    return "".join(parts)


def _text_from_output(output: Any) -> str:
    """Extract completed assistant message text from Open Responses-style output."""
    if not isinstance(output, list):
        return ""

    messages: list[tuple[int, str]] = []
    last_tool_index = -1
    for index, item in enumerate(output):
        if isinstance(item, dict) and item.get("type") in (
            "function_call",
            "function_call_output",
        ):
            last_tool_index = index
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("role", "assistant") != "assistant":
            continue
        if text := _text_from_content(item.get("content")):
            messages.append((index, text))
    # Open WebUI output can retain pre-tool assistant items. The last message
    # is the post-tool, user-facing answer and is the only safe TTS result.
    if not messages or messages[-1][0] < last_tool_index:
        return ""
    return messages[-1][1]


def summarize_response_shape(response: Any) -> dict[str, Any]:
    """Return content-free response structure suitable for debug logging."""
    if not isinstance(response, dict):
        return {"type": type(response).__name__}

    choices = response.get("choices")
    first_choice = (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )
    message = (
        first_choice.get("message")
        if isinstance(first_choice.get("message"), dict)
        else {}
    )
    output = response.get("output")
    return {
        "keys": sorted(response),
        "choice_count": len(choices) if isinstance(choices, list) else 0,
        "choice_keys": sorted(first_choice),
        "message_keys": sorted(message),
        "finish_reason": first_choice.get("finish_reason"),
        "tool_call_count": (
            len(message.get("tool_calls"))
            if isinstance(message.get("tool_calls"), list)
            else 0
        ),
        "output_types": (
            [
                item.get("type")
                for item in output
                if isinstance(item, dict) and isinstance(item.get("type"), str)
            ]
            if isinstance(output, list)
            else []
        ),
        "has_error": bool(response.get("error")),
    }


def summarize_stream_event_shape(event: Any) -> dict[str, Any]:
    """Return content-free stream-event structure suitable for debug logging."""
    if not isinstance(event, dict):
        return {"type": type(event).__name__}

    data = event.get("data")
    choices = event.get("choices")
    first_choice = (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )
    delta = (
        first_choice.get("delta") if isinstance(first_choice.get("delta"), dict) else {}
    )
    return {
        "event_type": event.get("type"),
        "keys": sorted(event),
        "data_keys": sorted(data) if isinstance(data, dict) else [],
        "choice_keys": sorted(first_choice),
        "delta_keys": sorted(delta),
    }


def stream_event_has_tool_call(event: dict[str, Any]) -> bool:
    """Return whether an event contains an unfinished or completed tool call."""
    if not isinstance(event, dict):
        return False

    if event.get("type") == "chat:completion" and isinstance(event.get("data"), dict):
        event = event["data"]

    event_type = event.get("type")
    if isinstance(event_type, str) and (
        event_type.startswith("response.function_call")
        or (
            event_type in ("response.output_item.added", "response.output_item.done")
            and isinstance(event.get("item"), dict)
            and event["item"].get("type") == "function_call"
        )
    ):
        return True

    response = event.get("response")
    for output in (
        event.get("output"),
        response.get("output") if isinstance(response, dict) else None,
    ):
        if isinstance(output, list) and any(
            isinstance(item, dict) and item.get("type") == "function_call"
            for item in output
        ):
            return True

    choices = event.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        for field in ("delta", "message"):
            value = choice.get(field)
            if isinstance(value, dict) and value.get("tool_calls"):
                return True
    return False


def extract_assistant_text(response: dict[str, Any]) -> str:
    """Extract final assistant text from an Open WebUI response."""
    if not isinstance(response, dict):
        raise ApiJsonError("Open WebUI returned a malformed response")
    if response.get("error"):
        raise ApiJsonError("Open WebUI returned an error response")

    # Prefer the post-tool Open Responses output when both compatibility
    # choices and an authoritative output trace are present.
    text = _text_from_output(response.get("output"))
    if text.strip():
        return text

    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        message = choice.get("message")
        if isinstance(message, dict):
            if message.get("tool_calls") or choice.get("finish_reason") == "tool_calls":
                raise ApiJsonError("Open WebUI returned an unfinished native tool call")
            text = _text_from_content(message.get("content"))
            if text.strip():
                return text

    output = response.get("output")
    if isinstance(output, list) and any(
        isinstance(item, dict) and item.get("type") == "function_call"
        for item in output
    ):
        raise ApiJsonError("Open WebUI returned an unfinished native tool call")

    raise ApiJsonError("Open WebUI returned an empty final response")


def extract_stream_event_text(event: dict[str, Any]) -> tuple[str, str | None]:
    """Return a text delta and, when present, an authoritative final response."""
    if not isinstance(event, dict):
        raise ApiJsonError("Open WebUI returned a malformed streaming event")

    event_type = event.get("type")
    completion_data = event.get("data")
    if event_type == "chat:completion" and isinstance(completion_data, dict):
        event = completion_data

    error = event.get("error")
    if not error and event_type == "chat:message:error":
        data = event.get("data")
        if data is None:
            data = completion_data
        error = data.get("error") if isinstance(data, dict) else data
    if not error and event_type in ("response.failed", "response.incomplete", "error"):
        error = True
    if (
        not error
        and isinstance(event.get("response"), dict)
        and event["response"].get("error")
    ):
        error = True
    if error:
        # Error payloads can echo prompts, MCP arguments/results, or household
        # state. Event type and structural diagnostics are sufficient for logs.
        raise ApiJsonError(
            f"Open WebUI stream reported an error event ({event_type or 'unknown'})"
        )

    response_data = event.get("response")
    response_output = (
        response_data.get("output") if isinstance(response_data, dict) else None
    )
    final_text = (
        _text_from_output(event.get("output"))
        or _text_from_output(response_output)
        or None
    )
    delta_text = ""

    if event_type == "response.output_text.delta" and isinstance(
        event.get("delta"), str
    ):
        delta_text = event["delta"]
    elif event_type == "response.output_text.done" and isinstance(
        event.get("text"), str
    ):
        final_text = event["text"]
    elif event_type == "response.output_item.done" and isinstance(
        event.get("item"), dict
    ):
        item = event["item"]
        if item.get("type") == "message":
            final_text = _text_from_content(item.get("content")) or final_text

    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict):
            delta_text = _text_from_content(delta.get("content"))
        message = choice.get("message")
        if isinstance(message, dict):
            final_text = _text_from_content(message.get("content")) or final_text

    return delta_text, final_text


def build_buffered_stream_response(
    deltas: Iterable[str],
    final_text: str | None,
    *,
    saw_tool_call: bool = False,
) -> dict[str, Any]:
    """Return a normal chat-completion shape after buffering an SSE response."""
    if saw_tool_call and final_text is None:
        raise ApiJsonError(
            "Open WebUI stream ended with an unfinished native tool call"
        )
    content = final_text if final_text is not None else "".join(deltas)
    if not content.strip():
        raise ApiJsonError("Open WebUI returned an empty final response")
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}
