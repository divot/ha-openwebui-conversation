"""Tests for the OpenWebUI service device."""

from unittest.mock import patch

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openwebui_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_MODEL,
    DEFAULT_MODEL,
    DOMAIN,
)


async def test_service_device_and_conversation_agent(
    hass, enable_custom_integrations, setup_homeassistant_component
) -> None:
    """The conversation entity creates a service device and remains addressable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OpenWebUI - Test",
        data={
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "secret-key",
        },
        options={},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.openwebui_conversation.api."
            "OpenWebUIApiClient.async_get_heartbeat",
            return_value=True,
        ),
        patch(
            "custom_components.openwebui_conversation.api."
            "OpenWebUIApiClient.async_get_models",
            return_value={"data": [{"id": "selected-model"}]},
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, entry.entry_id)}
        )
        assert device is not None
        assert device.entry_type is dr.DeviceEntryType.SERVICE
        assert device.name == entry.title
        assert device.manufacturer == "Open WebUI"
        assert device.model == DEFAULT_MODEL
        assert str(device.configuration_url) == entry.data[CONF_BASE_URL]

        entity_registry = er.async_get(hass)
        entity_id = entity_registry.async_get_entity_id(
            "conversation", DOMAIN, entry.entry_id
        )
        assert entity_id is not None
        entity = entity_registry.async_get(entity_id)
        assert entity is not None
        assert entity.device_id == device.id
        assert entity.unique_id == entry.entry_id
        assert entity.original_name is None

        assert conversation.async_get_agent(hass, entity_id) is not None
        assert (
            conversation.async_get_conversation_languages(hass, entity_id) == MATCH_ALL
        )

        assert hass.config_entries.async_update_entry(
            entry, options={CONF_MODEL: "selected-model"}
        )
        await hass.async_block_till_done()

        updated_device = device_registry.async_get_device(
            identifiers={(DOMAIN, entry.entry_id)}
        )
        assert updated_device is not None
        assert updated_device.id == device.id
        assert updated_device.model == "selected-model"
        assert (
            entity_registry.async_get_entity_id("conversation", DOMAIN, entry.entry_id)
            == entity_id
        )
