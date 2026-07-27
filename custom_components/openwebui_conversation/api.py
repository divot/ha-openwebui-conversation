"""OpenWebUI API Client."""

from __future__ import annotations

import asyncio
import json
import re
import socket
import time
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

from .const import LOGGER
from .exceptions import (
    ApiAuthError,
    ApiClientError,
    ApiCommError,
    ApiJsonError,
    ApiTimeoutError,
)
from .response import build_buffered_stream_response, extract_stream_event_text

_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;\"']+")
_SECRET_FIELD_PATTERN = re.compile(
    r"(?i)([\"\']?(?:api[_-]?key|token|authorization)[\"\']?\s*[:=]\s*[\"\']?)[^\s,;\"\'}]+"
)


def _decode_stream_line(raw_line: bytes) -> dict[str, Any] | None:
    """Decode one SSE or newline-delimited JSON line."""
    line = raw_line.decode("utf-8", "replace").strip()
    if not line or line.startswith(":") or line.startswith("event:"):
        return None
    if line.startswith("data:"):
        payload = line.removeprefix("data:").strip()
    elif line.startswith("{"):
        # Some OpenAI-compatible providers emit NDJSON through Open WebUI.
        payload = line
    else:
        return None
    if payload == "[DONE]":
        return None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as err:
        raise ApiJsonError("Open WebUI returned a malformed streaming event") from err
    if not isinstance(event, dict):
        raise ApiJsonError("Open WebUI returned a malformed streaming event")
    return event


class OpenWebUIApiClient:
    """OpenWebUI API Client."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int,
        verify_ssl: bool,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the API client."""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self._verify_ssl = verify_ssl
        self._session = session

    async def async_get_heartbeat(self) -> bool:
        """Get heartbeat from the API."""
        response = await self._api_wrapper(method="get", url=f"{self._base_url}/health")
        if not isinstance(response, dict):
            raise ApiJsonError("Open WebUI returned a malformed health response")
        return response.get("status") is True

    async def async_get_models(self) -> dict[str, Any]:
        """Get models from the API."""
        response = await self._api_wrapper(
            method="get",
            url=f"{self._base_url}/api/models",
            headers=self._auth_headers,
        )
        if not isinstance(response, dict):
            raise ApiJsonError("Open WebUI returned a malformed models response")
        return response

    async def async_get_tools(self) -> list[dict[str, Any]]:
        """Get tools available to the authenticated Open WebUI user."""
        response = await self._api_wrapper(
            method="get",
            url=f"{self._base_url}/api/v1/tools/",
            headers=self._auth_headers,
        )
        if not isinstance(response, list) or not all(
            isinstance(tool, dict) for tool in response
        ):
            raise ApiJsonError("Open WebUI returned a malformed tools response")
        return response

    async def async_create_chat(self, chat: dict[str, Any]) -> str:
        """Create a persistent chat and return its Open WebUI ID."""
        response = await self._api_wrapper(
            method="post",
            url=f"{self._base_url}/api/v1/chats/new",
            data={"chat": chat},
            headers=self._auth_headers,
        )
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("id"), str)
            or not response["id"]
        ):
            raise ApiJsonError("Open WebUI returned a malformed new chat response")
        return response["id"]

    async def async_update_chat(
        self, chat_id: str, chat: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace the client-owned state of a persistent Open WebUI chat."""
        response = await self._api_wrapper(
            method="post",
            url=f"{self._base_url}/api/v1/chats/{quote(chat_id, safe='')}",
            data={"chat": chat},
            headers=self._auth_headers,
        )
        if not isinstance(response, dict):
            raise ApiJsonError("Open WebUI returned a malformed updated chat response")
        return response

    async def async_generate(
        self,
        data: dict | None = None,
    ) -> dict[str, Any]:
        """Generate a completion from the API."""
        if data and data.get("stream"):
            return await self._api_stream(
                url=f"{self._base_url}/api/chat/completions",
                data=data,
                headers=self._auth_headers,
            )
        response = await self._api_wrapper(
            method="post",
            url=f"{self._base_url}/api/chat/completions",
            data=data,
            headers=self._auth_headers,
        )
        if not isinstance(response, dict):
            raise ApiJsonError("Open WebUI returned a malformed chat response")
        return response

    @property
    def _auth_headers(self) -> dict[str, str]:
        """Return request headers without ever logging their contents."""
        return {
            "Content-type": "application/json; charset=UTF-8",
            "Authorization": f"Bearer {self._api_key}",
        }

    def _sanitize_error(self, value: str) -> str:
        """Redact credentials from an upstream error body."""
        sanitized = (
            value.replace(self._api_key, "[redacted]") if self._api_key else value
        )
        sanitized = _BEARER_PATTERN.sub(r"\1[redacted]", sanitized)
        return _SECRET_FIELD_PATTERN.sub(r"\1[redacted]", sanitized)

    async def _raise_for_status(
        self, response: aiohttp.ClientResponse, url: str
    ) -> None:
        """Raise a secret-safe exception for an HTTP error response."""
        if response.status < 400:
            return
        path = urlsplit(url).path
        # Error bodies can echo credentials, prompts, MCP results, or household
        # state. The status and endpoint path are sufficient for default logs.
        error = f"HTTP {response.status} from {path}"
        if response.status in (401, 403):
            raise ApiAuthError(error)
        raise ApiCommError(error)

    async def _api_stream(
        self, url: str, data: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """Buffer an Open WebUI SSE response and return only its final answer."""
        started = time.monotonic()
        try:
            async with (
                asyncio.timeout(self.timeout),
                self._session.request(
                    method="post",
                    url=url,
                    headers=headers,
                    json=data,
                    ssl=self._verify_ssl,
                ) as response,
            ):
                await self._raise_for_status(response, url)
                deltas: list[str] = []
                final_text: str | None = None
                buffer = b""
                async for chunk in response.content.iter_any():
                    buffer += chunk
                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        event = _decode_stream_line(raw_line)
                        if event is None:
                            continue
                        delta, event_final = extract_stream_event_text(event)
                        if delta:
                            deltas.append(delta)
                        if event_final is not None:
                            final_text = event_final
                if buffer.strip():
                    event = _decode_stream_line(buffer)
                    if event is not None:
                        delta, event_final = extract_stream_event_text(event)
                        if delta:
                            deltas.append(delta)
                        if event_final is not None:
                            final_text = event_final

                result = build_buffered_stream_response(deltas, final_text)
                LOGGER.debug(
                    "Open WebUI request completed (path=%s, status=%s, elapsed=%.3fs, stream=true)",
                    urlsplit(url).path,
                    response.status,
                    time.monotonic() - started,
                )
                return result
        except ApiJsonError as err:
            raise ApiJsonError(self._sanitize_error(str(err))) from err
        except ApiClientError:
            raise
        except TimeoutError as err:
            raise ApiTimeoutError("timeout while talking to the server") from err
        except (aiohttp.ClientError, socket.gaierror) as err:
            raise ApiCommError(
                f"communication error: {self._sanitize_error(str(err))}"
            ) from err
        except Exception as err:
            raise ApiClientError(
                f"unexpected error: {self._sanitize_error(str(err))}"
            ) from err

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
        decode_json: bool = True,
    ) -> Any:
        """Get information from the API."""
        started = time.monotonic()
        try:
            async with (
                asyncio.timeout(self.timeout),
                self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                    ssl=self._verify_ssl,
                ) as response,
            ):
                await self._raise_for_status(response, url)
                try:
                    result = (
                        await response.json() if decode_json else await response.text()
                    )
                except (json.JSONDecodeError, aiohttp.ContentTypeError) as err:
                    raise ApiJsonError("Open WebUI returned invalid JSON") from err
                LOGGER.debug(
                    "Open WebUI request completed (path=%s, status=%s, elapsed=%.3fs)",
                    urlsplit(url).path,
                    response.status,
                    time.monotonic() - started,
                )
                return result
        except ApiClientError:
            raise
        except TimeoutError as err:
            raise ApiTimeoutError("timeout while talking to the server") from err
        except (aiohttp.ClientError, socket.gaierror) as err:
            raise ApiCommError(
                f"communication error: {self._sanitize_error(str(err))}"
            ) from err
        except Exception as err:  # pylint: disable=broad-except
            raise ApiClientError(
                f"unexpected error: {self._sanitize_error(str(err))}"
            ) from err
