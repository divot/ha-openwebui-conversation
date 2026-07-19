"""Exceptions used by OpenWebUI Conversation."""

from homeassistant.exceptions import HomeAssistantError


class ApiClientError(HomeAssistantError):
    """Exception to indicate a general API error."""


class ApiCommError(ApiClientError):
    """Exception to indicate a communication error."""


class ApiAuthError(ApiCommError):
    """Exception to indicate rejected Open WebUI credentials or access."""


class ApiJsonError(ApiClientError):
    """Exception to indicate an error with json response."""


class ApiTimeoutError(ApiClientError):
    """Exception to indicate a timeout error."""
