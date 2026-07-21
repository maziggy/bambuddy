"""Unit tests for the gram-based Spoolman cost helper (_spool_cost_for_grams)."""

import pytest

from backend.app.services.spoolman_tracking import _spool_cost_for_grams


class _FakeClient:
    """Stub Spoolman client exposing only the async get_spool used by the helper."""

    def __init__(self, spool: dict | None = None, raise_exc: Exception | None = None):
        self._spool = spool
        self._raise_exc = raise_exc

    async def get_spool(self, spool_id: int) -> dict:
        if self._raise_exc:
            raise self._raise_exc
        return self._spool


class TestSpoolCostForGrams:
    """Tests for _spool_cost_for_grams()."""

    @pytest.mark.asyncio
    async def test_price_via_filament(self):
        client = _FakeClient({"filament": {"price": 61.25, "weight": 250}})
        cost = await _spool_cost_for_grams(client, spool_id=1, grams=100)
        assert cost == 24.5

    @pytest.mark.asyncio
    async def test_spool_price_override_takes_precedence(self):
        client = _FakeClient({"price": 100, "filament": {"price": 61.25, "weight": 250}})
        cost = await _spool_cost_for_grams(client, spool_id=1, grams=100)
        assert cost == 40.0

    @pytest.mark.asyncio
    async def test_none_when_price_missing(self):
        client = _FakeClient({"filament": {"weight": 250}})
        cost = await _spool_cost_for_grams(client, spool_id=1, grams=100)
        assert cost is None

    @pytest.mark.asyncio
    async def test_none_when_weight_missing(self):
        client = _FakeClient({"filament": {"price": 61.25}})
        cost = await _spool_cost_for_grams(client, spool_id=1, grams=100)
        assert cost is None

    @pytest.mark.asyncio
    async def test_none_when_weight_zero(self):
        client = _FakeClient({"filament": {"price": 61.25, "weight": 0}})
        cost = await _spool_cost_for_grams(client, spool_id=1, grams=100)
        assert cost is None

    @pytest.mark.asyncio
    async def test_none_when_get_spool_raises(self):
        client = _FakeClient(raise_exc=RuntimeError("boom"))
        cost = await _spool_cost_for_grams(client, spool_id=1, grams=100)
        assert cost is None

    @pytest.mark.asyncio
    async def test_uses_prefetched_spool_without_calling_client(self):
        client = _FakeClient(raise_exc=RuntimeError("should not be called"))
        prefetched = {"filament": {"price": 61.25, "weight": 250}}
        cost = await _spool_cost_for_grams(client, spool_id=1, grams=100, prefetched=prefetched)
        assert cost == 24.5
