"""Diagnostics support for OpenWebUI Conversation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return config-entry diagnostics with credentials removed."""
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "home_assistant_version": HA_VERSION,
        },
        TO_REDACT,
    )
