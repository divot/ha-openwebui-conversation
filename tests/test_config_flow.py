"""Tests for the OpenWebUI config and options flows."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openwebui_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_SERVER_SIDE_TOOLS_ENABLED,
    CONF_SERVICE_NAME,
    CONF_TIMEOUT,
    CONF_TOOL_IDS,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.openwebui_conversation.exceptions import ApiAuthError


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
    """An entry without new options can add normalized tool IDs in-place."""
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
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tools_config"}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SERVER_SIDE_TOOLS_ENABLED: True,
            CONF_TOOL_IDS: " server:mcp:home-assistant\nweather\nweather\n",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOOL_IDS] == "server:mcp:home-assistant\nweather"


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

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tools_config"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVER_SIDE_TOOLS_ENABLED: True, CONF_TOOL_IDS: "\n  "},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "tool_ids_required"}
