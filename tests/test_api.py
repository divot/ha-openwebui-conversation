"""Tests for the secret-safe Open WebUI HTTP client."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

from custom_components.openwebui_conversation.api import OpenWebUIApiClient
from custom_components.openwebui_conversation.exceptions import (
    ApiAuthError,
    ApiCommError,
    ApiJsonError,
    ApiTimeoutError,
)


class FakeContent:
    """Minimal aiohttp response content stream."""

    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error

    async def iter_any(self):
        """Yield configured chunks and optionally interrupt the stream."""
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error


class FakeResponse:
    """Minimal async response context manager."""

    def __init__(
        self,
        *,
        status: int = 200,
        json_data: Any = None,
        text: str = "",
        chunks: list[bytes] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.json_data = json_data
        self.text_data = text
        self.content = FakeContent(chunks or [], stream_error)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self.json_data

    async def text(self):
        return self.text_data


class FakeSession:
    """Capture request arguments and return a fake response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.request_kwargs: dict[str, Any] = {}

    def request(self, **kwargs):
        self.request_kwargs = kwargs
        return self.response


def _client(session, *, timeout: float = 1, verify_ssl: bool = True):
    return OpenWebUIApiClient(
        base_url="https://openwebui.example",
        api_key="top-secret-key",
        timeout=timeout,
        verify_ssl=verify_ssl,
        session=session,
    )


async def test_non_streaming_request_and_ssl_setting() -> None:
    """The standard response is decoded and aiohttp's ssl option is used."""
    response_data = {"choices": [{"message": {"content": "test"}}]}
    session = FakeSession(FakeResponse(json_data=response_data))
    result = await _client(session, verify_ssl=False).async_generate(
        {"model": "test", "messages": [], "stream": False}
    )
    assert result == response_data
    assert session.request_kwargs["ssl"] is False
    assert session.request_kwargs["url"].endswith("/api/chat/completions")


async def test_non_object_chat_response_is_rejected() -> None:
    """A valid JSON scalar is not a valid chat-completion response."""
    with pytest.raises(ApiJsonError, match="malformed chat response"):
        await _client(FakeSession(FakeResponse(json_data=[]))).async_generate(
            {"stream": False}
        )


async def test_create_persistent_chat() -> None:
    """The native chat endpoint returns the server-generated chat ID."""
    session = FakeSession(FakeResponse(json_data={"id": "chat-id", "chat": {}}))
    chat = {"title": "Home Assistant", "history": {"messages": {}}}

    result = await _client(session).async_create_chat(chat)

    assert result == "chat-id"
    assert session.request_kwargs["url"].endswith("/api/v1/chats/new")
    assert session.request_kwargs["json"] == {"chat": chat}


async def test_malformed_persistent_chat_response_is_rejected() -> None:
    """A chat create response must contain a non-empty string ID."""
    with pytest.raises(ApiJsonError, match="malformed new chat response"):
        await _client(
            FakeSession(FakeResponse(json_data={"id": None}))
        ).async_create_chat({})


async def test_update_persistent_chat_escapes_id() -> None:
    """The chat ID cannot alter the update endpoint path."""
    session = FakeSession(FakeResponse(json_data={"id": "chat-id", "chat": {}}))
    chat = {"title": "Updated"}

    await _client(session).async_update_chat("chat/id", chat)

    assert session.request_kwargs["url"].endswith("/api/v1/chats/chat%2Fid")
    assert session.request_kwargs["json"] == {"chat": chat}


async def test_auth_error_redacts_credentials() -> None:
    """Upstream bodies cannot leak API keys or bearer tokens through errors."""
    body = '{"authorization":"Bearer top-secret-key","api_key":"top-secret-key"}'
    client = _client(FakeSession(FakeResponse(status=401, text=body)))
    with pytest.raises(ApiAuthError) as err:
        await client.async_generate({"stream": False})
    assert "top-secret-key" not in str(err.value)
    assert "/api/chat/completions" in str(err.value)
    assert "https://openwebui.example" not in str(err.value)


async def test_stream_is_buffered_to_final_tool_assisted_text() -> None:
    """Intermediate deltas and tool events are not returned as spoken output."""
    chunks = [
        b'data: {"choices":[{"delta":{"content":"I will check."}}]}\n\n',
        b'data: {"type":"status","data":{"action":"tool"}}\n\n',
        (
            b'data: {"done":true,"output":['
            b'{"type":"message","role":"assistant","content":'
            b'[{"type":"output_text","text":"I will check."}]},'
            b'{"type":"function_call","name":"get_state"},'
            b'{"type":"message","role":"assistant","content":'
            b'[{"type":"output_text","text":"The light is off."}]}]}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]
    result = await _client(FakeSession(FakeResponse(chunks=chunks))).async_generate(
        {"stream": True}
    )
    assert result["choices"][0]["message"]["content"] == "The light is off."


async def test_stream_accepts_ndjson_and_unterminated_done_marker() -> None:
    """Provider NDJSON and a final marker without a newline are accepted."""
    chunks = [
        b'{"choices":[{"delta":{"content":"Hello"}}]}\n',
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
        b"data: [DONE]",
    ]
    result = await _client(FakeSession(FakeResponse(chunks=chunks))).async_generate(
        {"stream": True}
    )
    assert result["choices"][0]["message"]["content"] == "Hello world"


async def test_malformed_stream_event() -> None:
    """Malformed SSE data is reported clearly."""
    client = _client(FakeSession(FakeResponse(chunks=[b"data: not-json\n\n"])))
    with pytest.raises(ApiJsonError, match="malformed streaming event"):
        await client.async_generate({"stream": True})


async def test_interrupted_stream() -> None:
    """An interrupted stream becomes a communication error."""
    response = FakeResponse(
        chunks=[b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'],
        stream_error=aiohttp.ClientPayloadError("connection lost"),
    )
    with pytest.raises(ApiCommError, match="connection lost"):
        await _client(FakeSession(response)).async_generate({"stream": True})


async def test_timeout() -> None:
    """A request exceeding the configured timeout is translated."""

    class SlowResponse(FakeResponse):
        async def __aenter__(self):
            await asyncio.sleep(0.05)
            return self

    with pytest.raises(ApiTimeoutError):
        await _client(FakeSession(SlowResponse()), timeout=0.001).async_generate(
            {"stream": False}
        )
