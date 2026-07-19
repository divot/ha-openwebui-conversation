"""Shared test fixtures for OpenWebUI Conversation."""

import pytest

from homeassistant.setup import async_setup_component

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
async def setup_homeassistant_component(hass):
    """Initialize exposed-entity data required by the conversation dependency."""
    assert await async_setup_component(hass, "homeassistant", {})
