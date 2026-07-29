"""Web-search mode compatibility helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_SEARCH_ENABLED,
    CONF_SEARCH_MODE,
    DEFAULT_SEARCH_ENABLED,
    DEFAULT_SEARCH_MODE,
    SEARCH_MODE_TRIGGER,
    SEARCH_MODES,
)


def search_mode_from_options(options: Mapping[str, Any]) -> str:
    """Return the configured search mode, including legacy option migration."""
    mode = options.get(CONF_SEARCH_MODE)
    if mode in SEARCH_MODES:
        return mode
    if CONF_SEARCH_MODE in options:
        return DEFAULT_SEARCH_MODE
    if options.get(CONF_SEARCH_ENABLED, DEFAULT_SEARCH_ENABLED):
        return SEARCH_MODE_TRIGGER
    return DEFAULT_SEARCH_MODE
