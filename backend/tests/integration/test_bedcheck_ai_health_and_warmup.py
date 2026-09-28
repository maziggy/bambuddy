"""Integration tests for:

- GET /api/v1/bedcheck-ai/health (item 1's health-registry route).
- The settings-save warmup hook (item 3's warmup(), fired from
  routes/settings.py's update_settings() when bedcheck_ai connection settings
  change -- see the review-response notes there for why this hook was chosen
  over firing warmup() off a successful POST /bedcheck-ai/test-connection).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient


class TestBedcheckAiHealthRoute:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_health_route_empty_before_any_check(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/bedcheck-ai/health")
        assert response.status_code == 200
        assert response.json() == {"printers": {}}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_health_route_reflects_recorded_outcome(self, async_client: AsyncClient):
        """The route is a thin read of bedcheck_ai.get_health() -- populate the
        module-level registry directly (check_bed_ai()'s own recording is
        covered at the unit level in test_ai_bed_check.py) and confirm the
        route serializes it as-is."""
        from backend.app.services import bedcheck_ai

        bedcheck_ai._record_outcome(42, "degraded", None, "json_object")
        try:
            response = await async_client.get("/api/v1/bedcheck-ai/health")
            assert response.status_code == 200
            body = response.json()
            assert body["printers"]["42"]["outcome"] == "degraded"
            assert body["printers"]["42"]["request_mode"] == "json_object"
        finally:
            bedcheck_ai._last_outcome.pop(42, None)


class TestBedcheckAiSettingsWarmupHook:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_saving_bedcheck_ai_api_key_triggers_warmup(self, async_client: AsyncClient, monkeypatch):
        from backend.app.services import bedcheck_ai

        warmup_mock = AsyncMock()
        monkeypatch.setattr(bedcheck_ai, "warmup", warmup_mock)

        response = await async_client.put("/api/v1/settings/", json={"bedcheck_ai_api_key": "new-key"})
        assert response.status_code == 200

        # The hook is fire-and-forget (asyncio.create_task) -- yield control
        # back to the loop so the scheduled task actually runs before we
        # assert on it.
        for _ in range(5):
            await asyncio.sleep(0)
        warmup_mock.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_saving_bedcheck_backend_ai_triggers_warmup(self, async_client: AsyncClient, monkeypatch):
        from backend.app.services import bedcheck_ai

        warmup_mock = AsyncMock()
        monkeypatch.setattr(bedcheck_ai, "warmup", warmup_mock)

        response = await async_client.put("/api/v1/settings/", json={"bedcheck_backend": "ai"})
        assert response.status_code == 200
        for _ in range(5):
            await asyncio.sleep(0)
        warmup_mock.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_saving_bedcheck_backend_opencv_does_not_trigger_warmup(self, async_client: AsyncClient, monkeypatch):
        """Switching TO opencv is not an AI-connection change -- must not fire."""
        from backend.app.services import bedcheck_ai

        warmup_mock = AsyncMock()
        monkeypatch.setattr(bedcheck_ai, "warmup", warmup_mock)

        response = await async_client.put("/api/v1/settings/", json={"bedcheck_backend": "opencv"})
        assert response.status_code == 200
        for _ in range(5):
            await asyncio.sleep(0)
        warmup_mock.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_saving_unrelated_setting_does_not_trigger_warmup(self, async_client: AsyncClient, monkeypatch):
        from backend.app.services import bedcheck_ai

        warmup_mock = AsyncMock()
        monkeypatch.setattr(bedcheck_ai, "warmup", warmup_mock)

        response = await async_client.put("/api/v1/settings/", json={"auto_archive": True})
        assert response.status_code == 200
        for _ in range(5):
            await asyncio.sleep(0)
        warmup_mock.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resaving_identical_values_does_not_trigger_warmup(self, async_client: AsyncClient, monkeypatch):
        """The settings UI auto-saves the whole bedcheck block on a debounce,
        so a key being *present* in the payload says nothing about whether it
        changed. Warming on presence alone fires a full vision-model request
        on every debounced save -- including the ones that change nothing."""
        from backend.app.services import bedcheck_ai

        payload = {
            "bedcheck_ai_base_url": "http://192.168.1.20:11434/v1",
            "bedcheck_ai_model": "qwen2.5vl:7b",
            "bedcheck_ai_api_key": "",
        }
        first = await async_client.put("/api/v1/settings/", json=payload)
        assert first.status_code == 200

        warmup_mock = AsyncMock()
        monkeypatch.setattr(bedcheck_ai, "warmup", warmup_mock)

        second = await async_client.put("/api/v1/settings/", json=payload)
        assert second.status_code == 200
        for _ in range(5):
            await asyncio.sleep(0)
        warmup_mock.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_changing_the_model_still_triggers_warmup(self, async_client: AsyncClient, monkeypatch):
        """The other half of the same rule: a real change must still warm, or
        the debounce optimisation would have disabled the hook outright."""
        from backend.app.services import bedcheck_ai

        await async_client.put("/api/v1/settings/", json={"bedcheck_ai_model": "qwen2.5vl:7b"})

        warmup_mock = AsyncMock()
        monkeypatch.setattr(bedcheck_ai, "warmup", warmup_mock)

        response = await async_client.put("/api/v1/settings/", json={"bedcheck_ai_model": "llava:13b"})
        assert response.status_code == 200
        for _ in range(5):
            await asyncio.sleep(0)
        warmup_mock.assert_awaited_once()


class TestBedcheckAiSettingsHealthReset:
    """Changing the connection settings points the check at a different
    backend, so the recorded outcomes describe something nobody is calling any
    more -- and the armed notification cooldown would swallow the NEW
    backend's first genuine failure for up to an hour."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_changing_base_url_clears_health_and_cooldown(self, async_client: AsyncClient, monkeypatch):
        from backend.app.services import bedcheck_ai

        monkeypatch.setattr(bedcheck_ai, "warmup", AsyncMock())
        bedcheck_ai._record_outcome(77, "unavailable", "connection failed", "json_schema")
        bedcheck_ai._last_unavailable_notified_at[77] = 1.0
        try:
            response = await async_client.put(
                "/api/v1/settings/", json={"bedcheck_ai_base_url": "http://192.168.1.99:11434/v1"}
            )
            assert response.status_code == 200
            assert bedcheck_ai.get_health() == {}
            assert bedcheck_ai._last_unavailable_notified_at == {}

            health = await async_client.get("/api/v1/bedcheck-ai/health")
            assert health.json() == {"printers": {}}
        finally:
            bedcheck_ai._last_outcome.pop(77, None)
            bedcheck_ai._last_unavailable_notified_at.pop(77, None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unrelated_setting_leaves_health_alone(self, async_client: AsyncClient):
        """Scoped to the bedcheck keys only -- every other setting in the app
        goes through this same endpoint."""
        from backend.app.services import bedcheck_ai

        bedcheck_ai._record_outcome(78, "ok", None, "json_schema")
        try:
            response = await async_client.put("/api/v1/settings/", json={"auto_archive": True})
            assert response.status_code == 200
            assert bedcheck_ai.get_health()[78]["outcome"] == "ok"
        finally:
            bedcheck_ai._last_outcome.pop(78, None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_flipping_backend_from_ai_to_opencv_clears_health_without_warming(
        self, async_client: AsyncClient, monkeypatch
    ):
        """Item 5b: the reset above is gated on `bedcheck_ai_changed` (ANY of
        base_url/model/api_key/backend actually changing), not on
        `bedcheck_ai_warmup_needed` -- so switching the backend AWAY from
        'ai' must still drop the stale health entry even though there is
        correctly no reason to warm a vision model nobody is about to call."""
        from backend.app.services import bedcheck_ai

        warmup_mock = AsyncMock()
        monkeypatch.setattr(bedcheck_ai, "warmup", warmup_mock)

        # Establish backend='ai' as the stored value so the follow-up PUT to
        # 'opencv' below is a real transition, not a no-op re-save.
        first = await async_client.put("/api/v1/settings/", json={"bedcheck_backend": "ai"})
        assert first.status_code == 200
        for _ in range(5):
            await asyncio.sleep(0)
        warmup_mock.reset_mock()

        bedcheck_ai._record_outcome(81, "unavailable", "connection failed", "json_schema")
        bedcheck_ai._last_unavailable_notified_at[81] = 1.0
        try:
            response = await async_client.put("/api/v1/settings/", json={"bedcheck_backend": "opencv"})
            assert response.status_code == 200

            assert bedcheck_ai.get_health() == {}
            assert bedcheck_ai._last_unavailable_notified_at == {}

            for _ in range(5):
                await asyncio.sleep(0)
            warmup_mock.assert_not_awaited()
        finally:
            bedcheck_ai._last_outcome.pop(81, None)
            bedcheck_ai._last_unavailable_notified_at.pop(81, None)
