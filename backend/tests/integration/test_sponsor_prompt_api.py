"""Integration tests for /sponsor-prompt routes."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestSponsorPromptAPI:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_check_returns_show_false_for_empty_install(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/sponsor-prompt/check")
        assert response.status_code == 200
        body = response.json()
        assert body["show"] is False
        assert body.get("milestone") is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_dismiss_requires_milestone(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/sponsor-prompt/dismiss", json={})
        # Pydantic missing-field → 422.
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_dismiss_returns_204(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/sponsor-prompt/dismiss",
            json={"milestone": "version-update"},
        )
        assert response.status_code == 204

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_check_then_dismiss_then_recheck_is_silent(self, async_client: AsyncClient):
        """End-to-end: even if no trigger is currently eligible, dismissing
        anchors the cooldown, so a subsequent check stays {show: false}."""
        first = await async_client.get("/api/v1/sponsor-prompt/check")
        assert first.json()["show"] is False
        dismiss = await async_client.post(
            "/api/v1/sponsor-prompt/dismiss",
            json={"milestone": "version-update"},
        )
        assert dismiss.status_code == 204
        second = await async_client.get("/api/v1/sponsor-prompt/check")
        assert second.json()["show"] is False
