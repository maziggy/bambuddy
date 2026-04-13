"""Unit tests for Obico detection service (#172)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.schemas.settings import AppSettingsUpdate
from backend.app.services.obico_detection import ObicoDetectionService
from backend.app.services.obico_smoothing import WARMUP_FRAMES


class TestSettingsSchemaValidators:
    """Guard rails on the new obico_* AppSettings fields."""

    def test_sensitivity_accepts_valid_values(self):
        for value in ("low", "medium", "high"):
            u = AppSettingsUpdate(obico_sensitivity=value)
            assert u.obico_sensitivity == value

    def test_sensitivity_rejects_garbage(self):
        with pytest.raises(ValueError, match="obico_sensitivity"):
            AppSettingsUpdate(obico_sensitivity="extreme")

    def test_action_accepts_valid_values(self):
        for value in ("notify", "pause", "pause_and_off"):
            assert AppSettingsUpdate(obico_action=value).obico_action == value

    def test_action_rejects_garbage(self):
        with pytest.raises(ValueError, match="obico_action"):
            AppSettingsUpdate(obico_action="explode")

    def test_enabled_printers_accepts_empty(self):
        assert AppSettingsUpdate(obico_enabled_printers="").obico_enabled_printers == ""
        assert AppSettingsUpdate(obico_enabled_printers=None).obico_enabled_printers is None

    def test_enabled_printers_accepts_int_array(self):
        u = AppSettingsUpdate(obico_enabled_printers="[1, 2, 3]")
        assert u.obico_enabled_printers == "[1, 2, 3]"

    def test_enabled_printers_rejects_non_json(self):
        with pytest.raises(ValueError, match="valid JSON"):
            AppSettingsUpdate(obico_enabled_printers="1,2,3")

    def test_enabled_printers_rejects_non_list(self):
        with pytest.raises(ValueError, match="JSON array"):
            AppSettingsUpdate(obico_enabled_printers='{"1": true}')

    def test_enabled_printers_rejects_non_int_elements(self):
        with pytest.raises(ValueError, match="JSON array"):
            AppSettingsUpdate(obico_enabled_printers='[1, "two"]')

    def test_poll_interval_bounds(self):
        with pytest.raises(ValueError):
            AppSettingsUpdate(obico_poll_interval=4)
        with pytest.raises(ValueError):
            AppSettingsUpdate(obico_poll_interval=121)
        assert AppSettingsUpdate(obico_poll_interval=10).obico_poll_interval == 10


class TestGetStatus:
    def test_empty_initial_status(self):
        svc = ObicoDetectionService()
        s = svc.get_status()
        assert s["is_running"] is False
        assert s["per_printer"] == {}
        assert s["history"] == []
        assert "low" in s["thresholds"] and "high" in s["thresholds"]


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_empty_url_via_route(self):
        """Service does not special-case empty URL — the route does."""
        svc = ObicoDetectionService()
        # This will fail DNS/connect, but should return ok=False
        result = await svc.test_connection("http://nonexistent-obico-host-xyz.invalid:3333")
        assert result["ok"] is False
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_healthy_response_is_ok(self):
        svc = ObicoDetectionService()
        mock_response = MagicMock(status_code=200, text="ok")
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            result = await svc.test_connection("http://obico:3333")
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["body"] == "ok"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_non_ok_body_is_not_ok(self):
        svc = ObicoDetectionService()
        mock_response = MagicMock(status_code=200, text="something else")
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            result = await svc.test_connection("http://obico:3333/")
        assert result["ok"] is False
        assert result["body"] == "something else"


class TestPollOneStateLifecycle:
    """Confirms per-printer state is reset when a new print starts."""

    @pytest.mark.asyncio
    async def test_new_task_name_resets_state(self):
        svc = ObicoDetectionService()
        # Seed a state that has been running for a while
        from backend.app.services.obico_smoothing import PrintState

        seeded = PrintState()
        for _ in range(WARMUP_FRAMES + 5):
            seeded.update(0.5)
        svc._states[1] = seeded
        svc._state_keys[1] = "old_task"
        svc._action_fired[1] = True

        settings = {
            "enabled": True,
            "ml_url": "http://obico:3333",
            "sensitivity": "medium",
            "action": "notify",
            "poll_interval": 10,
            "enabled_printers": None,
            "external_url": "http://bambuddy:8000",
        }
        status = MagicMock(state="RUNNING", task_name="new_task", subtask_name="")

        mock_response = MagicMock()
        mock_response.json.return_value = {"detections": []}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            await svc._check_printer(1, status, settings)

        # State was reset (frame_count is 1 after the single update, not 36)
        assert svc._states[1].frame_count == 1
        assert svc._state_keys[1] == "new_task"
        assert svc._action_fired[1] is False

    @pytest.mark.asyncio
    async def test_ml_api_error_does_not_crash(self):
        svc = ObicoDetectionService()
        settings = {
            "enabled": True,
            "ml_url": "http://obico:3333",
            "sensitivity": "medium",
            "action": "notify",
            "poll_interval": 10,
            "enabled_printers": None,
            "external_url": "http://bambuddy:8000",
        }
        status = MagicMock(state="RUNNING", task_name="job", subtask_name="")

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            await svc._check_printer(1, status, settings)

        assert svc._last_error is not None
        assert "connection refused" in svc._last_error

    @pytest.mark.asyncio
    async def test_failure_fires_action_only_once(self):
        """Once a failure has fired for a print, subsequent failures should not re-fire."""
        svc = ObicoDetectionService()
        settings = {
            "enabled": True,
            "ml_url": "http://obico:3333",
            "sensitivity": "medium",
            "action": "notify",
            "poll_interval": 10,
            "enabled_printers": None,
            "external_url": "http://bambuddy:8000",
        }
        status = MagicMock(state="RUNNING", task_name="job", subtask_name="")

        # Seed state so the next frame crosses HIGH immediately
        from backend.app.services.obico_smoothing import PrintState

        seeded = PrintState()
        for _ in range(WARMUP_FRAMES + 500):
            seeded.update(1.0)
        svc._states[1] = seeded
        svc._state_keys[1] = "job"
        svc._action_fired[1] = False

        mock_response = MagicMock()
        mock_response.json.return_value = {"detections": [["failure", 0.9, [0, 0, 1, 1]]]}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch("backend.app.services.obico_actions.execute_action", new=AsyncMock()) as mock_action,
        ):
            await svc._check_printer(1, status, settings)
            assert mock_action.call_count == 1
            await svc._check_printer(1, status, settings)
            # Second call must not dispatch again
            assert mock_action.call_count == 1
