#!/usr/bin/env python3
"""Probe an Open WebUI chat contract without printing credentials or content."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _emit(value: dict[str, Any]) -> None:
    """Write one machine-readable, content-free summary line."""
    sys.stdout.write(f"{json.dumps(value, sort_keys=True)}\n")


def _request(
    base_url: str, api_key: str, payload: dict[str, Any], *, stream: bool = False
) -> tuple[int, str, bytes]:
    """Send one request and return status, content type, and body."""
    request = Request(
        f"{base_url.rstrip('/')}/api/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=180) as response:  # noqa: S310
        return response.status, response.headers.get_content_type(), response.read()


def _response_summary(body: bytes) -> dict[str, Any]:
    """Summarize response structure without printing household or prompt content."""
    data = json.loads(body)
    choices = data.get("choices") if isinstance(data, dict) else None
    content = ""
    tool_calls = False
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict):
            value = message.get("content")
            content = value if isinstance(value, str) else ""
            tool_calls = bool(message.get("tool_calls"))
    return {
        "top_level_keys": sorted(data) if isinstance(data, dict) else [],
        "has_final_text": bool(content.strip()),
        "final_text_characters": len(content),
        "has_caller_visible_tool_calls": tool_calls,
        "has_sources": bool(data.get("sources")) if isinstance(data, dict) else False,
        "has_error": bool(data.get("error")) if isinstance(data, dict) else False,
    }


def _stream_summary(body: bytes) -> dict[str, Any]:
    """Summarize SSE/NDJSON event types and text counts without printing content."""
    summary = {
        "events": 0,
        "delta_characters": 0,
        "tool_call_events": 0,
        "status_events": 0,
        "source_events": 0,
        "done": False,
        "errors": 0,
    }
    for raw_line in body.decode("utf-8", "replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith((":", "event:")):
            continue
        payload = line.removeprefix("data:").strip()
        if payload == "[DONE]":
            summary["done"] = True
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            summary["errors"] += 1
            continue
        summary["events"] += 1
        if not isinstance(event, dict):
            continue
        if event.get("error") or event.get("type") == "chat:message:error":
            summary["errors"] += 1
        if event.get("type") == "status" or "status" in event:
            summary["status_events"] += 1
        if event.get("type") == "source" or event.get("sources"):
            summary["source_events"] += 1
        choices = event.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta") or {}
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    summary["delta_characters"] += len(content)
                if delta.get("tool_calls"):
                    summary["tool_call_events"] += 1
    return summary


def _run_probe(
    name: str,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
) -> bool:
    """Run one named probe and print only a structural result."""
    try:
        status, content_type, body = _request(
            base_url, api_key, payload, stream=bool(payload.get("stream"))
        )
        summary = (
            _stream_summary(body) if payload.get("stream") else _response_summary(body)
        )
        _emit(
            {
                "probe": name,
                "status": status,
                "content_type": content_type,
                "summary": summary,
            }
        )
        return status < 400 and not summary.get("has_error", False)
    except HTTPError as err:
        # Do not print response bodies: upstream errors can echo credentials or
        # private tool data.
        _emit({"probe": name, "status": err.code, "error": "http_error"})
    except (URLError, TimeoutError):
        _emit({"probe": name, "error": "connection_or_timeout"})
    except (json.JSONDecodeError, ValueError):
        _emit({"probe": name, "error": "malformed_response"})
    return False


def main() -> int:
    """Run selected contract probes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tool-id", action="append", default=[])
    parser.add_argument(
        "--tool-prompt",
        help="Operator-reviewed, read-only prompt required before tool probes run.",
    )
    parser.add_argument("--web-search", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("OPENWEBUI_API_KEY")
    if not api_key:
        parser.error("set OPENWEBUI_API_KEY; it is read from the environment only")
    if args.tool_id and not args.tool_prompt:
        parser.error(
            "--tool-prompt is required with --tool-id; use a read-only request"
        )

    probes: list[tuple[str, dict[str, Any]]] = [
        (
            "plain",
            {
                "model": args.model,
                "messages": [{"role": "user", "content": "Reply with the word test."}],
                "stream": False,
            },
        )
    ]
    if args.tool_id:
        tool_payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": args.tool_prompt}],
            "tool_ids": args.tool_id,
            "stream": False,
        }
        probes.extend(
            [
                ("tools", tool_payload),
                ("tools_with_caller_tools", {**tool_payload, "tools": []}),
                ("tools_streaming", {**tool_payload, "stream": True}),
            ]
        )
    if args.web_search:
        search_payload = {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": "Search the web for the official Home Assistant homepage.",
                }
            ],
            "features": {"web_search": True},
            "stream": False,
        }
        probes.append(("web_search", search_payload))
        if args.tool_id:
            probes.append(
                (
                    "multiple_capabilities",
                    {
                        **search_payload,
                        "messages": [{"role": "user", "content": args.tool_prompt}],
                        "tool_ids": args.tool_id,
                    },
                )
            )

    passed = 0
    for name, payload in probes:
        passed += _run_probe(name, args.base_url, api_key, payload)
    _emit({"probes": len(probes), "passed": passed})
    return 0 if passed == len(probes) else 1


if __name__ == "__main__":
    sys.exit(main())
