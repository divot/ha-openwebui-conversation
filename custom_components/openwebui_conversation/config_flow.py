"""Adds config flow for OpenWebUI."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import types
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    BooleanSelectorConfig,
    SelectOptionDict,
    TemplateSelector,
    TemplateSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import OpenWebUIApiClient
from .const import (
    DOMAIN,
    LOGGER,
    MENU_OPTIONS,
    CONF_SERVICE_NAME,
    CONF_BASE_URL,
    CONF_API_KEY,
    CONF_TIMEOUT,
    CONF_MODEL,
    CONF_LANGUAGE_CODE,
    CONF_SEARCH_ENABLED,
    CONF_SEARCH_SENTENCES,
    CONF_SEARCH_RESULT_PREFIX,
    CONF_STRIP_MARKDOWN,
    CONF_VERIFY_SSL,
    CONF_MAX_HISTORY,
    CONF_PERSISTENT_CHAT_ENABLED,
    CONF_STREAMING_ENABLED,
    CONF_SERVER_SIDE_TOOLS_ENABLED,
    CONF_TOOL_IDS,
    DEFAULT_SERVICE_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DEFAULT_MODEL,
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_SEARCH_ENABLED,
    DEFAULT_SEARCH_SENTENCES,
    DEFAULT_SEARCH_RESULT_PREFIX,
    DEFAULT_STRIP_MARKDOWN,
    DEFAULT_VERIFY_SSL,
    DEFAULT_MAX_HISTORY,
    DEFAULT_PERSISTENT_CHAT_ENABLED,
    DEFAULT_STREAMING_ENABLED,
    DEFAULT_SERVER_SIDE_TOOLS_ENABLED,
    DEFAULT_TOOL_IDS,
)
from .exceptions import ApiAuthError, ApiClientError, ApiCommError, ApiTimeoutError
from .request import normalize_tool_ids


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERVICE_NAME, default=DEFAULT_SERVICE_NAME): str,
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Required(CONF_API_KEY, default=""): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
            ),
        ),
        vol.Required(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): int,
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)

DEFAULT_OPTIONS = types.MappingProxyType(
    {
        CONF_TIMEOUT: DEFAULT_TIMEOUT,
        CONF_MODEL: DEFAULT_MODEL,
        CONF_SEARCH_ENABLED: DEFAULT_SEARCH_ENABLED,
        CONF_SEARCH_SENTENCES: DEFAULT_SEARCH_SENTENCES,
        CONF_SEARCH_RESULT_PREFIX: DEFAULT_SEARCH_RESULT_PREFIX,
        CONF_STRIP_MARKDOWN: DEFAULT_STRIP_MARKDOWN,
        CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
        CONF_MAX_HISTORY: DEFAULT_MAX_HISTORY,
        CONF_PERSISTENT_CHAT_ENABLED: DEFAULT_PERSISTENT_CHAT_ENABLED,
        CONF_STREAMING_ENABLED: DEFAULT_STREAMING_ENABLED,
        CONF_SERVER_SIDE_TOOLS_ENABLED: DEFAULT_SERVER_SIDE_TOOLS_ENABLED,
        CONF_TOOL_IDS: DEFAULT_TOOL_IDS,
    }
)


class OpenWebUIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OpenWebUI Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        # Search for duplicates with the same CONF_BASE_URL value.
        for existing_entry in self._async_current_entries(include_ignore=False):
            if (
                existing_entry.data.get(CONF_SERVICE_NAME)
                == user_input[CONF_SERVICE_NAME]
            ):
                return self.async_abort(reason="already_configured")

        errors = {}
        try:
            self.client = OpenWebUIApiClient(
                base_url=cv.url_no_path(user_input[CONF_BASE_URL]),
                api_key=user_input[CONF_API_KEY],
                timeout=user_input[CONF_TIMEOUT],
                session=async_create_clientsession(self.hass),
                verify_ssl=user_input[CONF_VERIFY_SSL],
            )
            response = await self.client.async_get_heartbeat()
            if not response:
                raise vol.Invalid("Invalid OpenWebUI server")
            # The public heartbeat cannot validate API credentials.
            await self.client.async_get_models()
        except vol.Invalid:
            errors["base"] = "invalid_url"
        except ApiAuthError:
            errors["base"] = "invalid_auth"
        except ApiTimeoutError:
            errors["base"] = "timeout_connect"
        except ApiCommError:
            errors["base"] = "cannot_connect"
        except ApiClientError as exception:
            LOGGER.exception("Unexpected exception: %s", exception)
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(
                title=f"OpenWebUI - {user_input[CONF_SERVICE_NAME]}",
                data={
                    CONF_SERVICE_NAME: user_input[CONF_SERVICE_NAME],
                    CONF_BASE_URL: user_input[CONF_BASE_URL],
                    CONF_API_KEY: user_input[CONF_API_KEY],
                },
                options={
                    CONF_TIMEOUT: user_input[CONF_TIMEOUT],
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                },
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OpenWebUIOptionsFlow(config_entry)


class OpenWebUIOptionsFlow(config_entries.OptionsFlow):
    """OpenWebUI config flow options handler."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.options = deepcopy(dict(config_entry.options))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        return self.async_show_menu(step_id="init", menu_options=MENU_OPTIONS)

    async def async_step_general_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage General Settings."""
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)

        schema = openwebui_schema_general_config(self.options)
        return self.async_show_form(
            step_id="general_config", data_schema=vol.Schema(schema)
        )

    async def async_step_model_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Model Settings."""
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)

        try:
            client = OpenWebUIApiClient(
                base_url=cv.url_no_path(self.config_entry.data[CONF_BASE_URL]),
                api_key=self.config_entry.data[CONF_API_KEY],
                timeout=self.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                session=async_create_clientsession(self.hass),
                verify_ssl=self.options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            response = await client.async_get_models()
            models = response.get("data")
            if not isinstance(models, list):
                raise ApiClientError("Open WebUI returned a malformed models list")
        except ApiClientError as exception:
            LOGGER.exception("Unexpected exception: %s", exception)
            models = []

        schema = openwebui_schema_model_config(
            self.options,
            [
                model["id"]
                for model in models
                if isinstance(model, dict) and isinstance(model.get("id"), str)
            ],
        )
        return self.async_show_form(
            step_id="model_config", data_schema=vol.Schema(schema)
        )

    async def async_step_search_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Search Settings."""
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)

        schema = openwebui_schema_search_config(self.options)
        return self.async_show_form(
            step_id="search_config", data_schema=vol.Schema(schema)
        )

    async def async_step_tools_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Open WebUI server-side tool settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            tool_ids = normalize_tool_ids(user_input.get(CONF_TOOL_IDS))
            if user_input.get(CONF_SERVER_SIDE_TOOLS_ENABLED) and not tool_ids:
                errors["base"] = "tool_ids_required"
            else:
                user_input[CONF_TOOL_IDS] = "\n".join(tool_ids)
                self.options.update(user_input)
                return self.async_create_entry(title="", data=self.options)

        client = OpenWebUIApiClient(
            base_url=cv.url_no_path(self.config_entry.data[CONF_BASE_URL]),
            api_key=self.config_entry.data[CONF_API_KEY],
            timeout=self.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            session=async_create_clientsession(self.hass),
            verify_ssl=self.options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
        try:
            tools = await client.async_get_tools()
        except ApiClientError as exception:
            LOGGER.exception("Unable to load Open WebUI tools: %s", exception)
            tools = []
            if "base" not in errors:
                errors["base"] = "cannot_load_tools"

        selected_tool_ids = normalize_tool_ids(self.options.get(CONF_TOOL_IDS))
        if CONF_TOOL_IDS not in self.options:
            try:
                response = await client.async_get_models()
                models = response.get("data")
                if not isinstance(models, list):
                    raise ApiClientError("Open WebUI returned a malformed models list")
                model_tool_ids = _model_tool_ids(
                    models,
                    self.options.get(CONF_MODEL, DEFAULT_MODEL),
                )
                available_tool_ids = {
                    tool["id"]
                    for tool in tools
                    if isinstance(tool.get("id"), str) and tool["id"]
                }
                selected_tool_ids = [
                    tool_id
                    for tool_id in model_tool_ids
                    if tool_id in available_tool_ids
                ]
            except ApiClientError as exception:
                LOGGER.exception(
                    "Unable to load model-attached Open WebUI tools: %s",
                    exception,
                )

        tool_options = _tool_select_options(tools, selected_tool_ids)
        schema = openwebui_schema_tools_config(
            self.options,
            tool_options,
            selected_tool_ids,
        )
        return self.async_show_form(
            step_id="tools_config",
            data_schema=vol.Schema(schema),
            errors=errors,
        )


def openwebui_schema_general_config(options: Mapping[str, Any]) -> dict:
    """Return a schema for general config."""
    if not options:
        options = DEFAULT_OPTIONS
    return {
        vol.Optional(
            CONF_TIMEOUT,
            description={"suggested_value": options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)},
            default=DEFAULT_TIMEOUT,
        ): int,
        vol.Optional(
            CONF_LANGUAGE_CODE,
            description={
                "suggested_value": options.get(
                    CONF_LANGUAGE_CODE, DEFAULT_LANGUAGE_CODE
                )
            },
            default=DEFAULT_LANGUAGE_CODE,
        ): TextSelector(TextSelectorConfig(multiline=False)),
        vol.Required(
            CONF_VERIFY_SSL,
            description={
                "suggested_value": options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            },
            default=DEFAULT_VERIFY_SSL,
        ): BooleanSelector(BooleanSelectorConfig()),
        vol.Optional(
            CONF_MAX_HISTORY,
            description={
                "suggested_value": options.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY)
            },
            default=DEFAULT_MAX_HISTORY,
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
        vol.Required(
            CONF_STREAMING_ENABLED,
            description={
                "suggested_value": options.get(
                    CONF_STREAMING_ENABLED, DEFAULT_STREAMING_ENABLED
                )
            },
            default=DEFAULT_STREAMING_ENABLED,
        ): BooleanSelector(BooleanSelectorConfig()),
        vol.Required(
            CONF_PERSISTENT_CHAT_ENABLED,
            description={
                "suggested_value": options.get(
                    CONF_PERSISTENT_CHAT_ENABLED, DEFAULT_PERSISTENT_CHAT_ENABLED
                )
            },
            default=DEFAULT_PERSISTENT_CHAT_ENABLED,
        ): BooleanSelector(BooleanSelectorConfig()),
    }


def openwebui_schema_model_config(
    options: Mapping[str, Any], models: list[str]
) -> dict:
    """Return a schema for model config."""
    if not options:
        options = DEFAULT_OPTIONS
    return {
        vol.Required(
            CONF_MODEL,
            description={"suggested_value": options.get(CONF_MODEL, DEFAULT_MODEL)},
            default=DEFAULT_MODEL,
        ): SelectSelector(
            SelectSelectorConfig(
                options=models,
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
                translation_key=CONF_MODEL,
                sort=True,
            )
        ),
        vol.Required(
            CONF_STRIP_MARKDOWN,
            description={
                "suggested_value": options.get(
                    CONF_STRIP_MARKDOWN, DEFAULT_STRIP_MARKDOWN
                )
            },
            default=DEFAULT_STRIP_MARKDOWN,
        ): BooleanSelector(BooleanSelectorConfig()),
    }


def openwebui_schema_search_config(options: Mapping[str, Any]) -> dict:
    """Return a schema for search config."""
    if not options:
        options = DEFAULT_OPTIONS
    return {
        vol.Required(
            CONF_SEARCH_ENABLED,
            description={
                "suggested_value": options.get(
                    CONF_SEARCH_ENABLED, DEFAULT_SEARCH_ENABLED
                )
            },
            default=DEFAULT_SEARCH_ENABLED,
        ): BooleanSelector(BooleanSelectorConfig()),
        vol.Required(
            CONF_SEARCH_SENTENCES,
            description={
                "suggested_value": options.get(
                    CONF_SEARCH_SENTENCES, DEFAULT_SEARCH_SENTENCES
                )
            },
            default=DEFAULT_SEARCH_SENTENCES,
        ): TemplateSelector(TemplateSelectorConfig()),
        vol.Optional(
            CONF_SEARCH_RESULT_PREFIX,
            description={
                "suggested_value": options.get(
                    CONF_SEARCH_RESULT_PREFIX, DEFAULT_SEARCH_RESULT_PREFIX
                )
            },
            default=DEFAULT_SEARCH_RESULT_PREFIX,
        ): TextSelector(TextSelectorConfig(multiline=False)),
    }


def _model_tool_ids(models: list[Any], model_id: str) -> list[str]:
    """Return tool IDs attached to the selected Open WebUI model."""
    for model in models:
        if not isinstance(model, dict) or model.get("id") != model_id:
            continue
        info = model.get("info")
        meta = info.get("meta") if isinstance(info, dict) else model.get("meta")
        if not isinstance(meta, dict):
            return []
        return normalize_tool_ids(meta.get("toolIds"))
    return []


def _tool_select_options(
    tools: list[dict[str, Any]], selected_tool_ids: list[str]
) -> list[SelectOptionDict]:
    """Return named tool choices, retaining unavailable configured IDs."""
    options: list[SelectOptionDict] = []
    seen: set[str] = set()
    for tool in tools:
        tool_id = tool.get("id")
        if not isinstance(tool_id, str) or not tool_id or tool_id in seen:
            continue
        name = tool.get("name")
        options.append(
            SelectOptionDict(
                value=tool_id,
                label=name if isinstance(name, str) and name else tool_id,
            )
        )
        seen.add(tool_id)

    for tool_id in selected_tool_ids:
        if tool_id not in seen:
            options.append(SelectOptionDict(value=tool_id, label=tool_id))

    return options


def openwebui_schema_tools_config(
    options: Mapping[str, Any],
    tool_options: list[SelectOptionDict] | None = None,
    selected_tool_ids: list[str] | None = None,
) -> dict:
    """Return a schema for explicit Open WebUI server-side tool IDs."""
    if not options:
        options = DEFAULT_OPTIONS
    if selected_tool_ids is None:
        selected_tool_ids = normalize_tool_ids(
            options.get(CONF_TOOL_IDS, DEFAULT_TOOL_IDS)
        )
    if tool_options is None:
        tool_options = _tool_select_options([], selected_tool_ids)
    return {
        vol.Required(
            CONF_SERVER_SIDE_TOOLS_ENABLED,
            description={
                "suggested_value": options.get(
                    CONF_SERVER_SIDE_TOOLS_ENABLED,
                    DEFAULT_SERVER_SIDE_TOOLS_ENABLED,
                )
            },
            default=DEFAULT_SERVER_SIDE_TOOLS_ENABLED,
        ): BooleanSelector(BooleanSelectorConfig()),
        vol.Optional(
            CONF_TOOL_IDS,
            description={"suggested_value": selected_tool_ids},
            default=selected_tool_ids,
        ): SelectSelector(
            SelectSelectorConfig(
                options=tool_options,
                multiple=True,
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
                sort=True,
            )
        ),
    }
