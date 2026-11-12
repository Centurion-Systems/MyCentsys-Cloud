"""Errors raised by the cloud package.

Every error carries a short, user-presentable message and never a credential,
a phone number or a response body.
"""

from __future__ import annotations

__all__ = [
    "CloudError",
    "TransportError",
    "ApiError",
    "AuthError",
    "OtpRejected",
    "OtpThrottled",
    "AccessDenied",
    "DeviceOffline",
    "TimeBarred",
    "ConfigurationRequired",
    "ActionRefused",
    "ModelError",
]


class CloudError(Exception):
    """Base of everything the cloud package raises."""


class TransportError(CloudError):
    """The backend could not be reached (network, TLS, timeout)."""


class ApiError(CloudError):
    """The backend answered with an unexpected status or an unreadable body."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(CloudError):
    """The session is no longer accepted; the user has to sign in again."""


class OtpRejected(AuthError):
    """The one-time PIN was wrong."""


class OtpThrottled(CloudError):
    """Too many PIN requests or attempts; wait ``retry_after`` seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many one-time PIN requests; try again later")
        self.retry_after = retry_after


class AccessDenied(CloudError):
    """This number has no access to the requested device."""


class DeviceOffline(CloudError):
    """The module is not connected to the backend right now."""


class TimeBarred(CloudError):
    """The number is time barred on this module (too many requests)."""


class ConfigurationRequired(CloudError):
    """The module's configuration changed; it must be re-read before acting."""


class ActionRefused(CloudError):
    """The backend refused an action for a reason given in the message."""


class ModelError(ValueError, CloudError):
    """A response did not have the shape the backend contract promises."""
