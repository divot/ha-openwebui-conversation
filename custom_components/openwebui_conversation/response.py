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

    messages: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("role", "assistant") != "assistant":
            continue
        if text := _text_from_content(item.get("content")):
            messages.append(text)
    # Open WebUI output can retain pre-tool assistant items. The last message
    # is the post-tool, user-facing answer and is the only safe TTS result.
    return messages[-1] if messages else ""


def extract_assistant_text(response: dict[str, Any]) -> str:
    """Extract final assistant text from an Open WebUI response."""
    if not isinstance(response, dict):
        raise ApiJsonError("Open WebUI returned a malformed response")

    # Prefer the post-tool Open Responses output when both compatibility
    # choices and an authoritative output trace are present.
    text = _text_from_output(response.get("output"))
    if text.strip():
        return text

    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            text = _text_from_content(message.get("content"))
            if text.strip():
                return text

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
    if error:
        if isinstance(error, dict):
            error = (
                error.get("message")
                or error.get("detail")
                or error.get("content")
                or "stream error"
            )
        raise ApiJsonError(f"Open WebUI stream failed: {error}")

    final_text = _text_from_output(event.get("output")) or None
    delta_text = ""
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
    deltas: Iterable[str], final_text: str | None
) -> dict[str, Any]:
    """Return a normal chat-completion shape after buffering an SSE response."""
    content = final_text if final_text is not None else "".join(deltas)
    if not content.strip():
        raise ApiJsonError("Open WebUI returned an empty final response")
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}
