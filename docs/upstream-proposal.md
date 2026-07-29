# Upstream proposal

No GitHub issue, comment, branch, commit, or pull request was created by this
work. The following text is ready for maintainer review.

## Open WebUI v0.10.2 direct native-tool fix

Live testing found a separate Open WebUI defect that blocks the integration's
native tool path. For a synchronous authenticated caller to
`POST /api/chat/completions`:

1. `stream: false` resolves `tool_ids` but returns the provider's first
   `finish_reason: tool_calls` response without executing it.
2. `stream: true` without chat/message context passes through the same
   first-turn tool-call SSE.
3. `stream: true` with `chat_id` and assistant message ID enters Open WebUI's
   event-emitter handler, executes MCP, reinjects its result, and builds a final
   `data` object containing the authoritative `output`.
4. `response_handler` emits and persists that `data` object but has no return
   statement. FastAPI therefore returns JSON `null` to the synchronous direct
   caller.

The minimal fix in
`backend/open_webui/utils/middleware.py::streaming_chat_response_handler` is:

```diff
                 await outlet_filter_handler(ctx)
                 await background_tasks_handler(ctx)
+                return data
             except asyncio.CancelledError:
```

Browser requests that provide `session_id` still use the existing
background-task/WebSocket branch, so their response contract is unchanged. The
fixed synchronous path returns the same final object that Open WebUI already
emits and stores. A regression test should assert that a streaming direct
request with local chat/message context returns a final `output` object rather
than JSON `null`.

The companion integration change forces streaming for native server tools and
supplies an ephemeral `local:` chat/message context when persistent sidebar
chats are disabled. This activates the server-side loop without creating a
stored chat or requiring a WebSocket session.

## Open WebUI sessionless native-search support

The integration now distinguishes deterministic trigger search from
model-decided search inside the existing `features` object:

```json
{
  "features": {
    "web_search": true,
    "web_search_mode": "native"
  },
  "params": {
    "function_calling": "native"
  },
  "stream": true,
  "chat_id": "local:<uuid>",
  "id": "<assistant-message-uuid>"
}
```

The current direct-search patch is not sufficient for this mode.
`should_force_web_search` forces all requests without `session_id` through RAG,
while `use_builtin_tools` requires `session_id`. A fake session is not a safe
workaround: it selects the background-task API response and can create an event
caller for a browser session that does not exist.

The narrow server change should:

1. treat `features.web_search_mode: trigger` and omitted mode as the existing
   synchronous-RAG path;
2. recognize native mode only when `features.web_search` is true,
   `params.function_calling` is native, `stream` is true, and chat plus assistant
   message IDs are present;
3. allow only `search_web` and `fetch_url` through the existing global-config,
   user-permission, model-capability, and `builtinTools.web_search` gates for
   that sessionless request; and
4. reuse the direct streaming response fix so the completed tool-loop `data`
   object is returned over HTTP.

This preserves the browser path, the deterministic trigger path, and the
least-privilege rule that direct API callers do not receive unrelated hidden
builtins.

This is the historical proposal for the server-side tools work. Persistent
Open WebUI chats were subsequently added as a separate, disabled-by-default
General Settings option, and the manual tool-ID field was subsequently replaced
with permission-aware discovery and a multiselect. See the README and API
contract for the current behavior.

## Draft comment for issue #37

> I investigated adding Open WebUI-owned tools without moving the tool loop into
> Home Assistant. Current Open WebUI supports explicit server-side `tool_ids` on
> `POST /api/chat/completions`, including native MCP IDs such as
> `server:mcp:<server-id>`. One selected tool can therefore be Home Assistant's
> `/api/mcp/assist` endpoint, while other Workspace, OpenAPI, and MCP tools remain
> configured and executed in Open WebUI.
>
> I propose a backwards-compatible first PR that adds an opt-in tools setting
> and one tool ID per line, normalizes the IDs, and sends them only when enabled.
> It will deliberately never add an OpenAI-style `tools` array, because Open
> WebUI treats that field as caller-owned tool execution and skips its
> server-side ID resolution. Web search remains the existing
> `features.web_search` capability rather than being modeled as a tool ID.
>
> The PR would also update the conversation entity to use Home Assistant's
> current `ChatLog` lifecycle, add secret-safe response/error handling and
> diagnostics, add focused tests, and document Home Assistant MCP least
> privilege. Buffered streaming would remain disabled by default until verified
> against a live Open WebUI/model combination. Would this scope fit the project?

Issue: <https://github.com/TheRealPSV/ha-openwebui-conversation/issues/37>

## Draft pull-request description

### Add support for Open WebUI server-side tools

The integration can optionally include configured Open WebUI tool IDs in
`/api/chat/completions` requests. This allows Open WebUI to orchestrate
Workspace, OpenAPI, and MCP tools while Home Assistant remains the conversation
frontend.

This can be used with Home Assistant's MCP server to enable entity control,
without requiring this integration to implement or execute Home Assistant LLM
tools itself.

#### What changed

- Add opt-in **Enable server-side tools** and newline-separated **Tool IDs**
  options.
- Normalize whitespace, blank entries, and duplicate IDs while preserving
  arbitrary non-MCP IDs.
- Centralize request construction and send explicit `tool_ids` without an
  OpenAI-compatible `tools` field.
- Preserve the existing `features.web_search` contract and allow search and
  tools in one request. For sessionless direct API calls, route an explicit
  search request through the synchronous RAG handler because browser-only
  native builtin injection requires a WebSocket `session_id`.
- Use Home Assistant's supported `_async_handle_message`/`ChatLog` lifecycle,
  and bound prior history. Persistent Open WebUI chats remain outside this
  tools-only proposal.
- Parse plain and structured final responses without speaking reasoning, tool
  structures, or citation metadata.
- Add optional buffered SSE/NDJSON support, disabled by default.
- Validate API credentials on new setup, translate network/auth/timeout errors,
  redact secrets, and add diagnostics.
- Add unit tests, CI, architecture/setup/security/troubleshooting documentation,
  and English translations.

#### Backwards compatibility

Existing entries need no migration. New options default to tools off, streaming
off, and the prior web-search payload shape. Model and search configuration
remain in Open WebUI where practical.

#### Security

Tool IDs are explicit and opt-in; the integration never enables every tool for
the API user. Documentation recommends a dedicated Open WebUI user, Home
Assistant's non-admin `/api/mcp/assist` endpoint, Assist entity exposure,
OAuth or a dedicated long-lived token, HTTPS, narrow MCP function filters, and
Open WebUI access grants. No credentials or household transcripts are logged or
included in tests.

#### Test plan

- `python -m ruff check .`
- `python -m pytest`
- Home Assistant hassfest workflow
- HACS validation workflow
- Optional `scripts/probe_openwebui_contract.py` against a disposable Open
  WebUI instance
- Manual options-flow screenshots
- Manual acceptance: no-tool chat, read-only HA MCP query, exposed-entity
  control, web search, combined search + HA read, multi-turn follow-up, MCP
  unavailable, and invalid/deleted tool ID

Live MCP actions and the deployed Open WebUI tool loop are not claimed by the
unit suite. The probe and manual cases must be completed with the target
Open WebUI version/model before enabling tools in a real home.

## Suggested commit structure

1. `test: add Home Assistant component test harness and baseline request tests`
2. `refactor: adopt ConversationEntity ChatLog lifecycle and safe API errors`
3. `refactor: centralize Open WebUI request and final-response parsing`
4. `feat: add explicit Open WebUI server-side tool options`
5. `feat: add opt-in buffered streaming transport`
6. `docs: document MCP, web search, security, and API findings`

If the maintainer wants the smallest possible diff, split commits 2 and 5 into
follow-up PRs. The tools feature should still retain the request builder,
normalization tests, no-`tools` invariant, and final-text safety.

## Known limitations for release notes

- Tool discovery reflects the configured API user's current permissions; saved
  custom or unavailable IDs remain editable until removed.
- Persistent Open WebUI chat records are a separate opt-in feature; the default
  direct-completion path remains stateless.
- Streaming is buffered rather than progressively spoken and is disabled by
  default.
- Tool-loop behavior depends on Open WebUI version, model/provider support, user
  permissions, MCP health, and function-calling configuration.
- The integration cannot prove a real-world side effect from the model's text;
  operators should verify Home Assistant state/logs during acceptance testing.
- Reauthentication/reconfiguration flows are not yet included.

## Follow-up roadmap

1. Reauthentication and reconfiguration for API keys and base URLs.
2. Live versioned contract fixtures for Open WebUI native/legacy function
   calling and representative providers.
3. Progressive Home Assistant streaming only when speculative pre-tool text and
   final-message replacement can be handled safely.
4. Additional translations, brand assets, and release UI screenshots.
5. More granular error categories when Open WebUI exposes stable structured
   error codes for unknown models/tools, MCP failures, search failures, and loop
   limits.
