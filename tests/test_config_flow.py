"""Tests for the OpenWebUI config and options flows."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openwebui_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_PERSISTENT_CHAT_ENABLED,
    CONF_SEARCH_ENABLED,
    CONF_SEARCH_MODE,
    CONF_SERVER_SIDE_TOOLS_ENABLED,
    CONF_SERVICE_NAME,
    CONF_TIMEOUT,
    CONF_TOOL_IDS,
    CONF_VERIFY_SSL,
    DOMAIN,
    SEARCH_MODE_NATIVE,
    SEARCH_MODE_TRIGGER,
)
from custom_components.openwebui_conversation.exceptions import (
    ApiAuthError,
    ApiCommError,
)


async def test_new_install_validates_credentials(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """A new install validates both health and API-key access."""
    with (
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_heartbeat",
            return_value=True,
        ) as heartbeat,
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_models",
            return_value={"data": [{"id": "test-model"}]},
        ) as models,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SERVICE_NAME: "Test",
                CONF_BASE_URL: "https://openwebui.example",
                CONF_API_KEY: "secret-key",
                CONF_TIMEOUT: 60,
                CONF_VERIFY_SSL: True,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "secret-key"
    assert result["options"] == {CONF_TIMEOUT: 60, CONF_VERIFY_SSL: True}
    # Setup may immediately recheck the public heartbeat and authenticated model
    # endpoint after the flow creates the entry.
    assert heartbeat.await_count >= 1
    assert models.await_count >= 1


async def test_new_install_rejects_invalid_api_key(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """The unauthenticated heartbeat cannot allow an invalid key through setup."""
    with (
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_heartbeat",
            return_value=True,
        ),
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_models",
            side_effect=ApiAuthError("HTTP 401"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SERVICE_NAME: "Test",
                CONF_BASE_URL: "https://openwebui.example",
                CONF_API_KEY: "invalid",
                CONF_TIMEOUT: 60,
                CONF_VERIFY_SSL: True,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_existing_entry_tools_options_are_normalized(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """Multiselect values are normalized into the existing stored format."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    with (
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_tools",
            return_value=[
                {"id": "weather", "name": "Weather"},
                {
                    "id": "server:mcp:home-assistant",
                    "name": "Home Assistant",
                },
            ],
        ),
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_models",
            return_value={"data": []},
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "tools_config"}
        )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SERVER_SIDE_TOOLS_ENABLED: True,
            CONF_TOOL_IDS: [
                "server:mcp:home-assistant",
                "weather",
                "weather",
            ],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOOL_IDS] == "server:mcp:home-assistant\nweather"


async def test_tools_options_are_multiselect_with_model_attached_defaults(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """Available tools are named and model-attached tools are initially selected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={CONF_MODEL: "test-model"},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_tools",
            return_value=[
                {"id": "weather", "name": "Weather"},
                {
                    "id": "server:mcp:home-assistant",
                    "name": "Home Assistant",
                },
                {"id": "notes", "name": "Notes"},
            ],
        ),
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_models",
            return_value={
                "data": [
                    {
                        "id": "test-model",
                        "info": {
                            "meta": {
                                "toolIds": [
                                    "server:mcp:home-assistant",
                                    "weather",
                                    "private-tool",
                                ]
                            }
                        },
                    }
                ]
            },
        ),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "tools_config"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({})[CONF_TOOL_IDS] == [
        "server:mcp:home-assistant",
        "weather",
    ]
    tool_selector = next(
        selector
        for marker, selector in result["data_schema"].schema.items()
        if marker.schema == CONF_TOOL_IDS
    )
    assert tool_selector.config["multiple"] is True
    assert tool_selector.config["custom_value"] is True
    assert tool_selector.config["options"] == [
        {"value": "weather", "label": "Weather"},
        {
            "value": "server:mcp:home-assistant",
            "label": "Home Assistant",
        },
        {"value": "notes", "label": "Notes"},
    ]


async def test_tools_options_preserve_saved_and_unavailable_ids(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """Saved choices take precedence and deleted tools remain editable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={
            CONF_MODEL: "test-model",
            CONF_TOOL_IDS: "retired-tool\nweather",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_tools",
            return_value=[{"id": "weather", "name": "Weather"}],
        ),
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_models",
        ) as models,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "tools_config"}
        )

    assert result["data_schema"]({})[CONF_TOOL_IDS] == [
        "retired-tool",
        "weather",
    ]
    tool_selector = next(
        selector
        for marker, selector in result["data_schema"].schema.items()
        if marker.schema == CONF_TOOL_IDS
    )
    assert tool_selector.config["options"] == [
        {"value": "weather", "label": "Weather"},
        {"value": "retired-tool", "label": "retired-tool"},
    ]
    models.assert_not_awaited()


async def test_tools_options_keep_saved_ids_when_discovery_fails(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """A discovery error is visible without discarding saved custom IDs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={CONF_TOOL_IDS: "server:mcp:home-assistant"},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.openwebui_conversation.config_flow."
        "OpenWebUIApiClient.async_get_tools",
        side_effect=ApiCommError("unavailable"),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "tools_config"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_load_tools"}
    assert result["data_schema"]({})[CONF_TOOL_IDS] == ["server:mcp:home-assistant"]
    tool_selector = next(
        selector
        for marker, selector in result["data_schema"].schema.items()
        if marker.schema == CONF_TOOL_IDS
    )
    assert tool_selector.config["options"] == [
        {
            "value": "server:mcp:home-assistant",
            "label": "server:mcp:home-assistant",
        }
    ]


async def test_general_options_include_persistent_chats(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """Persistent Open WebUI chats are an opt-in General Settings option."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general_config"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({})[CONF_PERSISTENT_CHAT_ENABLED] is False

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PERSISTENT_CHAT_ENABLED: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PERSISTENT_CHAT_ENABLED] is True


async def test_search_options_migrate_legacy_boolean_to_mode(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """The search form maps legacy entries to sentence-trigger mode."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={CONF_SEARCH_ENABLED: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "search_config"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({})[CONF_SEARCH_MODE] == SEARCH_MODE_TRIGGER


async def test_search_options_save_native_mode_and_remove_legacy_boolean(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """Saving native mode leaves one authoritative search setting."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={CONF_SEARCH_ENABLED: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "search_config"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SEARCH_MODE: SEARCH_MODE_NATIVE,
            "search_sentences": "search for {query}",
            "search_result_prefix": "Searched:",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SEARCH_MODE] == SEARCH_MODE_NATIVE
    assert CONF_SEARCH_ENABLED not in result["data"]


async def test_tools_options_require_an_explicit_id(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """Enabling server-side tools without selecting any is rejected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_tools",
            return_value=[],
        ),
        patch(
            "custom_components.openwebui_conversation.config_flow."
            "OpenWebUIApiClient.async_get_models",
            return_value={"data": []},
        ),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "tools_config"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SERVER_SIDE_TOOLS_ENABLED: True, CONF_TOOL_IDS: []},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "tool_ids_required"}
