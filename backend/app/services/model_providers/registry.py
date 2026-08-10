"""Provider registry — maps ``source_type`` / URLs to model providers.

The registry is the routing layer a future *shared* import API uses: a pasted
URL goes through :meth:`ModelProviderRegistry.find_for_url`, which asks each
registered provider ``supports_url`` and returns the one that owns it. Today
the route layer still calls the MakerWorld provider directly (endpoints stay
at ``/makerworld/*``), but registering providers here keeps the seam ready.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.services.model_providers.base import ModelProvider


class ModelProviderRegistry:
    """Holds the registered :class:`ModelProvider` instances.

    Registering is idempotent per provider instance; registering a *different*
    provider under an already-taken ``source_type`` is an error.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        existing = self._providers.get(provider.source_type)
        if existing is not None and existing is not provider:
            raise ValueError(f"A model provider for source_type {provider.source_type!r} is already registered")
        self._providers[provider.source_type] = provider

    def get(self, source_type: str) -> ModelProvider:
        try:
            return self._providers[source_type]
        except KeyError as exc:
            raise KeyError(f"No model provider registered for source_type {source_type!r}") from exc

    def all(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers.values())

    def find_for_url(self, url: str) -> ModelProvider | None:
        """Return the provider that claims ``url``, or ``None`` if none do."""
        for provider in self._providers.values():
            if provider.supports_url(url):
                return provider
        return None


# App-wide registry. Providers register themselves on package import (see
# ``backend/app/services/model_providers/__init__.py``).
registry = ModelProviderRegistry()
