"""MakerWorld error types.

Subclasses of the generic provider hierarchy so route layers can map errors
with the provider-agnostic classes (:class:`ProviderError` and friends) while
callers that know they're talking to MakerWorld get the specific types.
"""

from __future__ import annotations

from backend.app.services.model_providers.base import (
    ProviderAuthError,
    ProviderError,
    ProviderForbiddenError,
    ProviderNotFoundError,
    ProviderUnavailableError,
    ProviderUrlError,
)


class MakerWorldError(ProviderError):
    """Base exception for MakerWorld API errors."""


class MakerWorldAuthError(ProviderAuthError, MakerWorldError):
    """Raised when MakerWorld requires a Bambu Cloud token and we don't have
    one (or the one we sent was rejected). True auth failure."""


class MakerWorldForbiddenError(ProviderForbiddenError, MakerWorldError):
    """Raised when MakerWorld refuses access despite valid authentication —
    content-gated (points required, purchase required, region restricted,
    early-access, etc.)."""


class MakerWorldNotFoundError(ProviderNotFoundError, MakerWorldError):
    """Raised when a design / profile / instance doesn't exist."""


class MakerWorldUnavailableError(ProviderUnavailableError, MakerWorldError):
    """Raised on 5xx, network errors, or malformed payloads."""


class MakerWorldUrlError(ProviderUrlError, MakerWorldError):
    """Raised when a URL isn't a makerworld.com model page."""
