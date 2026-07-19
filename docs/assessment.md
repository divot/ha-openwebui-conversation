# Repository and architecture assessment

Assessment date: 2026-07-18 (America/Los_Angeles).

## Decision

Use **Path B: modernize and extend `TheRealPSV/ha-openwebui-conversation`**.
It is the correct conceptual home, is released and maintained, already owns the
Open WebUI-specific configuration, and can remain a thin client. Its previous
conversation implementation was behind Home Assistant's supported entity
lifecycle, but the necessary modernization is focused rather than a replacement
of the integration.

`skye-harris/hass_local_openai_llm` is a useful Apache-2.0 reference for current
Home Assistant patterns, but it intentionally implements the tool loop in Home
Assistant. Making it defer tool execution to Open WebUI would fight its core
architecture. No code was copied between the projects. This repository remains
AGPL-3.0 under its existing license.

## Source snapshot

| Project | Revision reviewed | Purpose |
| --- | --- | --- |
| `ha-openwebui-conversation` | `93024a1`, 2026-06-14, v1.3.2 baseline | Preferred repository |
| `home-assistant/core` | `02fef3de`, retrieved 2026-07-18 PT | Current conversation and MCP patterns |
| `home-assistant/core` | `0171b527`, Home Assistant 2025.6.3 | Minimum-version compatibility check |
| `open-webui/open-webui` | `ecd48e2f`, Open WebUI v0.10.2 | Direct API, tool, search, and streaming source contract |
| `hass_local_openai_llm` | repository HEAD retrieved 2026-07-18 | Maintained custom-integration reference |

The Open WebUI and Home Assistant source revisions were inspected locally. The
test suite added here uses mocked HTTP responses. No live Open WebUI or Home
Assistant instance was accessed, so deployment-specific behavior remains an
acceptance-test item.

## Repository health

| Area | Baseline finding | Change or recommendation |
| --- | --- | --- |
| Recent work | Latest baseline commit was a search README update on 2026-06-14; v1.3.2 was released the next day. | Active enough to propose a focused feature. |
| Releases | Eight releases from November 2024 through June 2026, with long quiet periods and clustered maintenance releases. | Plan for a small, reviewable PR rather than a broad redesign. |
| Issues and PRs | GitHub reported eight open issue/PR records: two issues and six dependency-update PRs. Tool request #37 has been open since May 2025 and received an initial maintainer response. Some user reports waited months; small PRs have also been handled quickly. | Discuss on #37, then open a scoped PR. |
| Tests | No automated tests in the baseline. | Added request, config-flow, conversation, response, API failure, and diagnostics tests plus a CI workflow. |
| CI | Hassfest, HACS validation, Ruff, and release packaging existed. Ruff selected Python 3.11 while requirements pinned Home Assistant 2026.6, which requires a newer Python. | Lint now uses Python 3.14; pytest CI was added. Keep hassfest and HACS jobs. |
| HACS | `hacs.json` declares a zip release and Home Assistant 2025.6 minimum; HACS validation runs in CI. Brand validation is explicitly ignored. | Packaging remains compatible. Add brand assets upstream when practical. |
| HA/Python support | Baseline metadata said HA 2025.6, but the dev requirements and CI interpreter were inconsistent. | Implementation is checked against both HA 2025.6.3 source APIs and HA 2026.6 tests; CI uses Python 3.14. |
| Dependencies | `markdown-it-py` 4.2.0 and `mdit-plain` 1.0.1 are current baseline runtime pins. | No new runtime dependency was added. |
| HA APIs | Baseline overrode `async_process`, called `async_get_chat_log` directly, and kept its own history. This bypassed the current `ConversationEntity` session lifecycle and was already incompatible with the declared 2025.6 minimum signature. It also used older callback/result type aliases. | Implement `_async_handle_message(user_input, chat_log)`, make `ChatLog` authoritative, and use current public types. |
| HTTP client | Baseline used aiohttp's obsolete/invalid `verify_ssl=` request argument, validated setup only through public `/health`, and logged complete prompts/payloads. | Use `ssl=`, validate the key with authenticated `/api/models`, bound timeouts, redact errors, and log only safe metadata. |
| Streaming | Not supported. | Added optional buffered SSE/NDJSON handling. It is off by default pending a live version-specific probe. |
| Config migration | None existed. | No entry migration is needed: all new options have backwards-compatible defaults. Reauth and reconfigure remain follow-ups. |
| Diagnostics | None existed. | Added secret-redacted diagnostics. |
| Localization | English setup/options strings existed, including an English translation file. | Added English strings for new options/errors. Additional locales remain community follow-up work. |
| Contribution process | AGPL-3.0 license, codeowner, issues, and release automation exist; no `CONTRIBUTING.md` was present. | Follow existing style, provide screenshots if requested, and keep commits separable. |

Repository activity was assessed from releases, issues, PRs, commits, and CI—not
stars.

## Baseline request path

Before this work, a turn followed this path:

1. `conversation.py:OpenWebUIAgent.async_process` acquired a Home Assistant
   `ChatLog` itself and appended the current request to a private `self.history`.
2. `OpenWebUIAgent.query` constructed `model`, `messages`, `stream: false`, and
   `features.web_search` inline.
3. `api.py:OpenWebUIApiClient.async_generate` called
   `POST /api/chat/completions`.
4. It assumed `choices[0].message.content` was a string.
5. It produced an `IntentResponse` and appended its own assistant history.

Models were loaded from authenticated `GET /api/models`. Search used local
Hassil sentence matching to extract `{query}`, then sent
`features: {"web_search": true}`. The integration had both Home Assistant
`ChatLog` state and an unbounded per-entity history, so the two could diverge.

Home Assistant conversation IDs were returned locally but never mapped to Open
WebUI. The client did not create persistent Open WebUI chats and sent no
`chat_id`, `session_id`, user-message ID, or assistant-message ID. It did not
call `/api/chat/completed`, did not consume SSE, did not understand structured
content/reasoning/citations/tool results, and did not send an OpenAI `tools`
field.

## Implemented request path

1. Home Assistant's `ConversationEntity.async_process` now owns session and
   `ChatLog` creation and invokes
   `OpenWebUIAgent._async_handle_message(user_input, chat_log)`.
2. `_messages_from_chat_log` maps Home Assistant's authoritative content and
   retains the current user message plus a bounded number of prior turns. A
   search-trigger rewrite replaces the current message in place.
3. `request.build_chat_completion_payload` constructs the only request shape.
   Explicit Open WebUI `tool_ids` and `features.web_search` can coexist; an
   OpenAI-style `tools` field is never injected.
4. `OpenWebUIApiClient` calls `POST /api/chat/completions`, either decoding JSON
   or buffering SSE/NDJSON to a final response.
5. `response.extract_assistant_text` returns only user-facing text. It ignores
   reasoning, tool structures, and citation metadata, prefers the final
   post-tool assistant message, and rejects empty output.
6. The final `AssistantContent` is added to `ChatLog`, and an `IntentResponse`
   suitable for TTS is returned with Home Assistant's local conversation ID.

## Modern Home Assistant comparison

The selected lifecycle matches Home Assistant's current and 2025.6
`ConversationEntity` contracts: the base entity manages `ChatSession` and
`ChatLog`; the integration implements `_async_handle_message`. This avoids a
second history and lets Home Assistant retain conversation isolation.

Buffered Open WebUI streaming is deliberately not presented as progressive
Home Assistant streaming. Server-side tool execution may emit speculative text,
status events, tool arguments, or reasoning before the authoritative final
message. Buffering is safer for voice output until live response shapes are
confirmed.

## Remaining modernization work

These are useful follow-ups, not blockers for the smallest feature:

- reauthentication and base-URL/API-key reconfiguration flows;
- permission-aware tool discovery with stable IDs and a manual fallback;
- progressive Home Assistant streaming only after Open WebUI tool-loop behavior
  is stable and the final spoken response can be guaranteed;
- translation contributions beyond English;
- richer structured, typed upstream error mapping without exposing response
  bodies;
- release screenshots and brand assets if requested by the maintainer.
