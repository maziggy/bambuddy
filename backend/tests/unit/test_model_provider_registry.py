"""Tests for the model-provider registry.

Pins the routing seam that a future *shared* import API will use: pasted URLs
go through ``find_for_url`` and land on the provider that owns them.
"""

from __future__ import annotations

import pytest

from backend.app.services.model_providers import makerworld_provider, registry
from backend.app.services.model_providers.base import ModelProvider
from backend.app.services.model_providers.registry import ModelProviderRegistry


class _DummyProvider(ModelProvider):
    source_type = "dummy"
    display_name = "Dummy"

    async def build_service(self, *, db, user, api_key_owner=None, client=None):
        raise NotImplementedError

    def parse_url(self, url):
        raise NotImplementedError

    def canonical_url(self, ref):
        raise NotImplementedError


class TestAppRegistry:
    """The app-wide singleton auto-registers MakerWorld on import."""

    def test_makerworld_is_registered(self):
        assert registry.get("makerworld") is makerworld_provider
        assert registry.get("makerworld").display_name == "MakerWorld"

    def test_unknown_source_type_raises_keyerror(self):
        with pytest.raises(KeyError):
            registry.get("thingiverse")

    def test_find_for_url_routes_makerworld_urls(self):
        provider = registry.find_for_url("https://makerworld.com/en/models/1400373#profileId-1452154")
        assert provider is makerworld_provider

    def test_find_for_url_returns_none_for_foreign_hosts(self):
        assert registry.find_for_url("https://thingiverse.com/thing/123") is None
        assert registry.find_for_url("") is None
        assert registry.find_for_url(None) is None  # type: ignore[arg-type]


class TestModelProviderRegistry:
    def test_register_is_idempotent_per_instance(self):
        reg = ModelProviderRegistry()
        provider = _DummyProvider()
        reg.register(provider)
        reg.register(provider)
        assert reg.all() == (provider,)

    def test_register_duplicate_source_type_rejected(self):
        reg = ModelProviderRegistry()
        reg.register(_DummyProvider())
        with pytest.raises(ValueError):
            reg.register(_DummyProvider())

    def test_all_returns_registered_providers(self):
        reg = ModelProviderRegistry()
        provider = _DummyProvider()
        reg.register(provider)
        assert provider in reg.all()
        assert len(reg.all()) == 1
