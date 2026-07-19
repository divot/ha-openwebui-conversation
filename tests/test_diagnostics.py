"""Tests for secret-safe diagnostics."""

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openwebui_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_SERVICE_NAME,
    DOMAIN,
)
from custom_components.openwebui_conversation.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_api_key_is_redacted(hass) -> None:
    """Diagnostics retain useful structure without exposing the API key."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERVICE_NAME: "Test",
            CONF_BASE_URL: "https://openwebui.example",
            CONF_API_KEY: "do-not-leak-this-key",
        },
        options={},
    )
    result = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(result, default=str)

    assert "do-not-leak-this-key" not in serialized
    assert "https://openwebui.example" in serialized
