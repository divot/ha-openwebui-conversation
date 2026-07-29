"""Build requests for Open WebUI's agentic chat endpoint."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


def normalize_tool_ids(value: str | Iterable[str] | None) -> list[str]:
    """Return unique, non-empty tool IDs while retaining their order."""
    if value is None:
        return []

    values = value.splitlines() if isinstance(value, str) else value
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        tool_id = item.strip()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)
        normalized.append(tool_id)
    return normalized


def build_chat_completion_payload(
    *,
    model: str,
    messages: Sequence[dict[str, Any]],
    stream: bool,
    web_search: bool,
    server_side_tools_enabled: bool,
    tool_ids: str | Iterable[str] | None,
    function_calling: str | None = None,
    web_search_mode: str | None = None,
    chat_id: str | None = None,
    message_id: str | None = None,
    parent_id: str | None = None,
    user_message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a payload for ``POST /api/chat/completions``.

    Open WebUI resolves and executes ``tool_ids``. Deliberately do not add an
    OpenAI-compatible ``tools`` field: Open WebUI treats its presence, including
    ``tools: []``, as caller-owned tool execution and skips server-side
    resolution.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": stream,
        # Preserve the integration's pre-existing web-search request contract.
        "features": {"web_search": web_search},
    }

    if web_search and web_search_mode:
        payload["features"]["web_search_mode"] = web_search_mode

    if function_calling:
        payload["params"] = {"function_calling": function_calling}

    if server_side_tools_enabled and (normalized_ids := normalize_tool_ids(tool_ids)):
        payload["tool_ids"] = normalized_ids

    if chat_id:
        payload["chat_id"] = chat_id
        if message_id:
            payload["id"] = message_id
        if user_message is not None:
            # Match Open WebUI's browser request contract. This lets the
            # completion endpoint own message persistence, including structured
            # reasoning and tool output on the assistant message.
            payload["parent_id"] = parent_id
            payload["user_message"] = dict(user_message)

    return payload
