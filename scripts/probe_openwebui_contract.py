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
from uuid import uuid4


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
    tool_calls = 0
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict):
            value = message.get("content")
            content = value if isinstance(value, str) else ""
            value = message.get("tool_calls")
            tool_calls = len(value) if isinstance(value, list) else 0

    output = data.get("output") if isinstance(data, dict) else None
    output_types = (
        [
            item.get("type")
            for item in output
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        ]
        if isinstance(output, list)
        else []
    )
    output_text_characters = 0
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if (
                    isinstance(part, dict)
                    and part.get("type") in ("text", "output_text")
                    and isinstance(part.get("text"), str)
                ):
                    output_text_characters += len(part["text"])

    return {
        "json_type": type(data).__name__,
        "top_level_keys": sorted(data) if isinstance(data, dict) else [],
        "has_final_text": bool(content.strip()) or output_text_characters > 0,
        "final_text_characters": len(content) + output_text_characters,
        "has_tool_calls": tool_calls > 0 or "function_call" in output_types,
        "tool_call_count": tool_calls + output_types.count("function_call"),
        "output_types": output_types,
        "finish_reason": (
            choices[0].get("finish_reason")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        ),
        "has_sources": bool(data.get("sources")) if isinstance(data, dict) else False,
        "has_error": bool(data.get("error")) if isinstance(data, dict) else False,
    }


def _stream_summary(body: bytes) -> dict[str, Any]:
    """Summarize SSE/NDJSON event types and text counts without printing content."""
    summary = {
        "events": 0,
        "delta_characters": 0,
        "tool_call_events": 0,
        "has_final_text": False,
        "has_tool_calls": False,
        "finish_reasons": [],
        "event_types": {},
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
        event_type = event.get("type")
        event_type_key = (
            event_type if isinstance(event_type, str) else "chat.completion.chunk"
        )
        summary["event_types"][event_type_key] = (
            summary["event_types"].get(event_type_key, 0) + 1
        )
        if event.get("error") or event_type in (
            "chat:message:error",
            "response.failed",
            "error",
        ):
            summary["errors"] += 1
        if event_type == "status" or "status" in event:
            summary["status_events"] += 1
        if event_type == "source" or event.get("sources"):
            summary["source_events"] += 1
        if event_type == "response.output_text.delta" and isinstance(
            event.get("delta"), str
        ):
            summary["delta_characters"] += len(event["delta"])
        if event_type == "response.output_text.done" and isinstance(
            event.get("text"), str
        ):
            summary["has_final_text"] = bool(event["text"].strip())
        if event_type in ("response.output_item.added", "response.output_item.done"):
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                summary["tool_call_events"] += 1
                summary["has_tool_calls"] = True
        choices = event.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str):
                        summary["delta_characters"] += len(content)
                    if delta.get("tool_calls"):
                        summary["tool_call_events"] += 1
                        summary["has_tool_calls"] = True
                if choice.get("finish_reason"):
                    summary["finish_reasons"].append(choice["finish_reason"])
        if summary["delta_characters"] > 0:
            summary["has_final_text"] = True
    summary["finish_reasons"] = sorted(set(summary["finish_reasons"]))
    return summary


def _run_probe(
    name: str,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    expectation: str,
) -> bool:
    """Run one named probe and print only a structural result."""
    try:
        status, content_type, body = _request(
            base_url, api_key, payload, stream=bool(payload.get("stream"))
        )
        summary = (
            _response_summary(body)
            if content_type == "application/json"
            else _stream_summary(body)
        )
        if expectation == "final":
            matched_expectation = bool(summary.get("has_final_text"))
        elif expectation == "tool_call":
            matched_expectation = bool(summary.get("has_tool_calls")) and not bool(
                summary.get("has_final_text")
            )
        else:
            matched_expectation = True
        _emit(
            {
                "probe": name,
                "expectation": expectation,
                "status": status,
                "content_type": content_type,
                "summary": summary,
                "matched_expectation": matched_expectation,
            }
        )
        return (
            status < 400
            and not summary.get("has_error", False)
            and not summary.get("errors", 0)
            and matched_expectation
        )
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

    probes: list[tuple[str, dict[str, Any], str]] = [
        (
            "plain_nonstreaming",
            {
                "model": args.model,
                "messages": [{"role": "user", "content": "Reply with the word test."}],
                "stream": False,
            },
            "final",
        ),
        (
            "plain_streaming",
            {
                "model": args.model,
                "messages": [{"role": "user", "content": "Reply with the word test."}],
                "stream": True,
            },
            "final",
        ),
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
                ("tools_nonstreaming_first_turn", tool_payload, "tool_call"),
                (
                    "tools_streaming_first_turn",
                    {**tool_payload, "stream": True},
                    "tool_call",
                ),
                (
                    "tools_streaming_server_loop",
                    {
                        **tool_payload,
                        "stream": True,
                        "chat_id": f"local:contract-probe-{uuid4()}",
                        "id": str(uuid4()),
                    },
                    "final",
                ),
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
        probes.append(("web_search", search_payload, "final"))
        if args.tool_id:
            probes.append(
                (
                    "multiple_capabilities",
                    {
                        **search_payload,
                        "messages": [{"role": "user", "content": args.tool_prompt}],
                        "tool_ids": args.tool_id,
                        "stream": True,
                        "chat_id": f"local:contract-probe-{uuid4()}",
                        "id": str(uuid4()),
                    },
                    "final",
                )
            )

    passed = 0
    for name, payload, expectation in probes:
        passed += _run_probe(
            name,
            args.base_url,
            api_key,
            payload,
            expectation,
        )
    _emit({"probes": len(probes), "passed": passed})
    return 0 if passed == len(probes) else 1


if __name__ == "__main__":
    sys.exit(main())
