# Open WebUI and Home Assistant API contract

This document separates source-verified behavior from live behavior. Open WebUI
v0.10.2 source (`ecd48e2f`) and Home Assistant current/2025.6.3 source were
reviewed. Unit tests exercise recorded shapes through mocked HTTP. No credentials
or disposable instances were supplied, so native tool-loop execution, real MCP
effects, and provider-specific streaming have **not** been live-verified.

## Endpoint and request shapes

Use the Open WebUI agentic route:

```http
POST /api/chat/completions
Authorization: Bearer <OPEN_WEBUI_API_KEY>
Content-Type: application/json
```

Open WebUI v0.10.2 also routes `/api/v1/chat/completions`, but the documented
direct-integration example and frontend-oriented middleware use
`/api/chat/completions`. This integration uses that route.

Plain request, preserving the integration's legacy fields:

```json
{
  "model": "<model-id>",
  "messages": [{"role": "user", "content": "Reply with the word test."}],
  "stream": false,
  "features": {"web_search": false}
}
```

Server-side Workspace tool plus Home Assistant MCP:

```json
{
  "model": "<model-id>",
  "messages": [{"role": "user", "content": "Use the configured tools."}],
  "stream": false,
  "features": {"web_search": false},
  "tool_ids": [
    "server:mcp:home-assistant",
    "<workspace-tool-id>"
  ]
}
```

OpenAPI server IDs use `server:<server-id>`; native MCP server IDs use
`server:mcp:<server-id>`. Ordinary Workspace tool IDs are stored exactly as
returned by Open WebUI. The integration deliberately validates only that IDs
are nonblank because not all valid IDs have an MCP prefix.

Web search plus MCP in one turn:

```json
{
  "model": "<model-id>",
  "messages": [{"role": "user", "content": "Use search and the home tool."}],
  "stream": false,
  "features": {"web_search": true},
  "tool_ids": ["server:mcp:home-assistant"]
}
```

Built-in web search is a feature/capability, not a generic `tool_id`. In current
source, `features.web_search: true` enters either native agentic search or the
legacy RAG preprocessing path. It still requires global search configuration,
the API user's web-search permission, selected-model support/configuration, and
the relevant function-calling mode. A true request flag cannot overcome a
disabled provider, permission, or model capability.

Do **not** send this shape:

```json
{
  "tools": [],
  "tool_ids": ["server:mcp:home-assistant"]
}
```

Open WebUI snapshots the incoming `tools` value before server-side resolution.
When caller-provided `tools` is present—even an empty list—the request takes the
caller-owned tool path and skips `tool_ids` resolution. This integration never
adds `tools`.

## Optional persistent chat lifecycle

The General Settings option **Show Conversations in Open WebUI** mirrors each
Home Assistant conversation into a native Open WebUI chat:

1. `POST /api/v1/chats/new` with Open WebUI's tree-shaped `history` creates the
   record and returns its server-generated ID.
2. Home Assistant stores only its conversation-ID to Open WebUI chat-ID mapping
   in local integration storage.
3. `POST /api/chat/completions` includes `chat_id` and the assistant message
   `id`.
4. `POST /api/v1/chats/{id}` stores the completed assistant content and the
   current mirrored history.

The completion does not include `session_id`. In current Open WebUI source,
combining `session_id` and `chat_id` selects the WebSocket/background-task path
and returns task metadata instead of the final assistant response. Omitting it
keeps the direct HTTP completion contract used by Home Assistant.

Home Assistant's `ChatLog` remains authoritative. The bounded message list is
sent for model context, while the persistent Open WebUI record mirrors all
displayable messages in that Home Assistant conversation. Chat create/update
failures are logged and fall back to the existing stateless completion path so
sidebar persistence cannot suppress a voice response. API-key endpoint
restrictions must allow `/api/v1/chats` for this option.

## Tool activation and discovery

`GET /api/v1/tools/` returns tool metadata filtered for the authenticated user's
access and also includes native MCP server entries. It is usable for manual
lookup and is a candidate for future discovery. The first implementation keeps
manual IDs because discovery UI needs deletion, refresh, permission, and
fallback semantics and the endpoint is not necessary for request execution.

Tools visually attached to a model should not be assumed to activate on a
plain direct API call. Open WebUI's automation backend explicitly resolves
model-bound IDs because the frontend normally does that work; the ordinary
direct route consumes request `tool_ids`. The integration therefore sends its
explicit least-privilege list.

## Response contract

Common non-streaming response:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "The kitchen light is off."
      }
    }
  ]
}
```

Open Responses-style tool-assisted data may contain reasoning, calls, outputs,
and more than one assistant item:

```json
{
  "output": [
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I'll check."}]},
    {"type": "function_call", "name": "get_state", "arguments": "{}"},
    {"type": "function_call_output", "output": "off"},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "The kitchen light is off."}]}
  ]
}
```

The response parser speaks only `text`/`output_text` from the final assistant
message. It ignores reasoning, function arguments/results, sources, and
citations. Empty user-facing output is an error.

For `stream: true`, the HTTP client accepts SSE `data:` lines and newline-
delimited JSON. It handles OpenAI-style `choices[].delta.content`, `[DONE]`,
Open WebUI `chat:completion` envelopes, `chat:message:error` envelopes, and a
final `output` array. It buffers everything and returns one ordinary completion
shape. Tool calls, status events, sources, and reasoning are not spoken.

This is transport support, not proof that every Open WebUI/provider combination
finishes a native server-side tool loop through a direct streaming request.
Streaming remains off by default until the live probe passes.

## Tool-loop findings and limitations

Open WebUI's source supports resolving `tool_ids`, MCP/OpenAPI function specs,
tool invocation, result reinjection, and iterative final-response handling.
Open WebUI's public direct-API documentation describes `tool_ids` as server-side
tools. Source review also shows a distinction between legacy preprocessing and
the richer native streaming response handler. Consequently:

- request construction and server-side resolution are source-verified;
- the documented intent is that Open WebUI owns execution;
- non-streaming response parsing waits for the HTTP response and expects final
  text, but real native multi-step behavior is version/model dependent;
- the exact direct-API stream sequence and fully iterated final response must be
  confirmed against the deployed Open WebUI/model/provider;
- `/api/chat/completed` runs `outlet()` filters after a direct chat. It is not
  required to complete the tool loop or this direct request contract, but an
  integration that promises outlet-filtered output must make that second call
  on stable Open WebUI releases.

Home Assistant supplies bounded full message history on every turn. Persistent
Open WebUI chats are disabled by default. When enabled, the integration creates
and updates the native chat, sends `chat_id` and an assistant message `id`, and
persists the Home Assistant-to-Open WebUI ID mapping locally. It does not send
`session_id`.

## Home Assistant MCP contract

Prefer:

```text
https://HOME_ASSISTANT/api/mcp/assist
```

Current Home Assistant source makes the API-specific Assist endpoint available
to authenticated non-admin users. It exposes the Assist LLM API: registered
Assist intents and context limited by entity exposure. The general
`/api/mcp` endpoint exposes the APIs selected in the MCP Server integration and
is appropriate only when that broader selection is intentional. API-specific
endpoints other than `assist` require an administrator in current source.

Prefer OAuth if the deployments can complete Open WebUI's user authorization
flow. Static `Authorization: Bearer <long-lived-access-token>` is supported as a
fallback; use a dedicated non-admin Home Assistant user for `/api/mcp/assist`.
Use HTTPS and trust a private CA instead of broadly disabling TLS checks. Open
WebUI admins configure MCP servers, and Open WebUI access grants determine which
users can select them. A stable `WEBUI_SECRET_KEY` is required for encrypted
credential persistence across Open WebUI restarts.

The Assist API deliberately is not an unrestricted service/administration API.
Only exposed entities and registered Assist capabilities are available. For
example, current source omits calendar and script entities from the general
live-context overview because their domains use dedicated tools. Do not promote
the Home Assistant user to admin or expose additional entities merely to make a
model request succeed.

## Safe live probe

The optional script prints response structure and counts, never response text,
error bodies, tokens, or headers:

```bash
OPENWEBUI_API_KEY='<temporary-disposable-key>' \
  .venv/bin/python scripts/probe_openwebui_contract.py \
  --base-url 'https://openwebui.example' \
  --model '<model-id>'
```

To probe tools, add one or more `--tool-id` values and an operator-reviewed,
read-only `--tool-prompt`. Add `--web-search` to probe search and combined
capabilities. Run only against a disposable/test setup. A Home Assistant control
prompt can have real-world side effects.

Record the deployed Open WebUI version, provider/model, function-calling mode,
enabled model capabilities, probe structural output, and server logs. Specifically
confirm that non-streaming blocks until the final post-tool message, that
streaming terminates, and that both search and MCP can run in one request.

## Explicit answers

1. **Correct base?** Yes, after focused modernization (Path B). It is active and
   is the natural Open WebUI-specific integration.
2. **Outdated Home Assistant APIs?** The baseline overrode `async_process`,
   acquired `ChatLog` itself, maintained duplicate history, and used older
   callback/result type aliases. It now implements `_async_handle_message`.
3. **Endpoint?** `POST /api/chat/completions`.
4. **Tool payload?** An explicit top-level `tool_ids` list; MCP IDs use
   `server:mcp:<server-id>`, OpenAPI servers use `server:<server-id>`, and
   Workspace IDs are passed as returned.
5. **Does incoming `tools` supersede IDs?** Yes. Its presence selects the
   caller-owned tool path and suppresses server-side `tool_ids` resolution.
6. **Web search?** Send `features.web_search: true`; also configure global
   search, user permission, model capability/settings, and compatible function
   calling. It is not an MCP/Workspace tool ID.
7. **Are model-attached tools automatic?** Not reliably for direct API calls;
   send explicit IDs.
8. **Discovery?** Permission-filtered metadata is available from
   `GET /api/v1/tools/`; manual ID entry is the stable first UI.
9. **Does Open WebUI own the full loop?** Its current source and documented
   server-tool contract do. Exact direct non-streaming native behavior still
   requires the supplied live probe on the target version/provider.
10. **Streaming?** It can include deltas, status/tool/source events, errors, a
    final output, and `[DONE]`. The client buffers and speaks only the final
    assistant text. Version/provider behavior is live-unverified.
11. **Secondary finalization?** Not for the server-side tool loop.
    `/api/chat/completed` is separately required if the caller needs `outlet()`
    filters to run and return their transformed output on stable releases; that
    optional filter contract is outside this first feature.
12. **History owner?** Home Assistant `ChatLog`, sent as bounded model context
    and optionally mirrored in full to Open WebUI.
13. **Persistent Open WebUI chats?** Opt-in under General Settings. Create and
    update native `/api/v1/chats` records while preserving the stateless default.
14. **Conversation-ID mapping?** Store only the Home Assistant-to-Open WebUI
    chat-ID mapping locally so an Open WebUI chat is reused after reload.
15. **HA MCP endpoint?** Prefer `/api/mcp/assist`; use `/api/mcp` only for an
    intentional MCP Server API selection.
16. **HA MCP authentication/permission?** Authenticated OAuth or bearer token;
    `/api/mcp/assist` supports non-admin users and remains exposure/intent
    constrained.
17. **Search plus MCP in one turn?** The fields coexist and current source has
    both processing paths. Real combined execution is an acceptance test.
18. **Model lacks native tool calling?** It can ignore the tools, fail parsing,
    or return an error/fallback depending on Open WebUI mode. Choose a capable
    model or supported legacy mode; do not claim a side effect succeeded.
19. **Smallest upstreamable feature?** Keep both tools and persistent chats
    independently opt-in, preserve the legacy request when disabled, and cover
    request shapes, final-text safety, options, translations, and documentation.
20. **Intentional follow-ups?** Tool discovery, reauth/reconfigure, progressive
    streaming, broad HA API exposure, automatic tool enabling, and live
    deployment mutation.

## Primary references

- [Open WebUI API endpoints](https://docs.openwebui.com/reference/api-endpoints/)
- [Open WebUI native MCP](https://docs.openwebui.com/features/extensibility/mcp/)
- [Open WebUI agentic web search](https://docs.openwebui.com/features/chat-conversations/web-search/agentic-search/)
- [Open WebUI tools](https://docs.openwebui.com/features/extensibility/plugin/tools/)
- [Home Assistant MCP Server](https://www.home-assistant.io/integrations/mcp_server/)
- [Home Assistant conversation entities](https://developers.home-assistant.io/docs/core/entity/conversation/)
