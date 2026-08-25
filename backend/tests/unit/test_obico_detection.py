"""Unit tests for Obico detection service (#172)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.schemas.settings import AppSettingsUpdate
from backend.app.services.obico_detection import (
    FRAME_CACHE_TTL,
    ObicoDetectionService,
    _frame_cache,
    pop_frame,
    stash_frame,
)
from backend.app.services.obico_smoothing import WARMUP_FRAMES

FAKE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


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

    def test_thresholds_reflect_configured_sensitivity(self):
        """#1469 — get_status() reports the thresholds for the passed
        sensitivity, not a hardcoded 'medium'. Each level must be distinct so
        the Status panel changes when the user changes the setting."""
        svc = ObicoDetectionService()
        low = svc.get_status("low")["thresholds"]
        medium = svc.get_status("medium")["thresholds"]
        high = svc.get_status("high")["thresholds"]

        # Higher sensitivity → lower thresholds (easier to trigger).
        assert low["low"] > medium["low"] > high["low"]
        assert low["high"] > medium["high"] > high["high"]
        # Default and unknown values fall back to medium.
        assert svc.get_status()["thresholds"] == medium
        assert svc.get_status("bogus")["thresholds"] == medium


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


class TestMlApiToken:
    """Obico's ML API gates /p/ behind ML_API_TOKEN (#2733)."""

    def test_auth_headers_only_when_configured(self):
        from backend.app.services.obico_detection import auth_headers

        assert auth_headers("s3cret") == {"Authorization": "Bearer s3cret"}
        # Unconfigured must stay byte-identical to the pre-setting request.
        assert auth_headers("") == {}
        assert auth_headers(None) == {}
        assert auth_headers("   ") == {}
        # Whitespace around a real token is a paste artefact, not part of it.
        assert auth_headers("  s3cret  ") == {"Authorization": "Bearer s3cret"}

    def test_settings_schema_accepts_a_token(self):
        assert AppSettingsUpdate(obico_ml_token="s3cret").obico_ml_token == "s3cret"
        assert AppSettingsUpdate(obico_ml_token="").obico_ml_token == ""
        assert AppSettingsUpdate().obico_ml_token is None

    @staticmethod
    def _settings(**overrides):
        base = {
            "enabled": True,
            "ml_url": "http://obico:3333",
            "ml_token": "",
            "sensitivity": "medium",
            "action": "notify",
            "poll_interval": 10,
            "enabled_printers": None,
            "external_url": "http://bambuddy:8000",
        }
        base.update(overrides)
        return base

    @staticmethod
    def _client(response):
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_detection_call_carries_the_bearer_header(self):
        svc = ObicoDetectionService()
        response = MagicMock(status_code=200)
        response.json.return_value = {"detections": []}
        response.raise_for_status = MagicMock()
        mock_client = self._client(response)
        status = MagicMock(state="RUNNING", task_name="job", subtask_name="")

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, self._settings(ml_token="s3cret"))

        assert mock_client.get.await_args.kwargs["headers"] == {"Authorization": "Bearer s3cret"}

    @pytest.mark.asyncio
    async def test_detection_call_sends_no_header_without_a_token(self):
        svc = ObicoDetectionService()
        response = MagicMock(status_code=200)
        response.json.return_value = {"detections": []}
        response.raise_for_status = MagicMock()
        mock_client = self._client(response)
        status = MagicMock(state="RUNNING", task_name="job", subtask_name="")

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, self._settings())

        assert mock_client.get.await_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_401_reports_the_token_rather_than_a_bare_http_error(self):
        svc = ObicoDetectionService()
        response = MagicMock(status_code=401)
        # raise_for_status would also raise here; the status check must come first
        # so the user gets an actionable message instead of "401 Unauthorized".
        response.raise_for_status = MagicMock(side_effect=AssertionError("must not reach raise_for_status"))
        mock_client = self._client(response)
        status = MagicMock(state="RUNNING", task_name="job", subtask_name="")

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, self._settings(ml_token="wrong"))

        assert "401" in svc._last_error
        assert "ML_API_TOKEN" in svc._last_error
        # A rejected call must not be scored as a clean frame.
        assert 1 not in svc._states or svc._states[1].frame_count == 0

    @pytest.mark.asyncio
    async def test_401_message_does_not_leak_the_token(self):
        svc = ObicoDetectionService()
        response = MagicMock(status_code=401)
        response.raise_for_status = MagicMock()
        mock_client = self._client(response)
        status = MagicMock(state="RUNNING", task_name="job", subtask_name="")

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, self._settings(ml_token="sup3rs3cret"))

        assert "sup3rs3cret" not in svc._last_error


class TestTestConnectionTokenProbe:
    """/hc/ is ungated, so health alone cannot validate the token (#2733)."""

    @staticmethod
    def _client(responses):
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=responses)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_healthy_but_rejected_token_is_not_ok(self):
        svc = ObicoDetectionService()
        mock_client = self._client([MagicMock(status_code=200, text="ok"), MagicMock(status_code=401)])

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            result = await svc.test_connection("http://obico:3333", "wrong")

        assert result["ok"] is False
        assert result["auth_ok"] is False
        assert result["status_code"] == 401
        assert "ML_API_TOKEN" in result["error"]

    @pytest.mark.asyncio
    async def test_accepted_token_is_ok(self):
        svc = ObicoDetectionService()
        # 422 = "Invalid request params": auth passed, then the handler rejected
        # the img-less probe. That is the success signal.
        mock_client = self._client([MagicMock(status_code=200, text="ok"), MagicMock(status_code=422)])

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            result = await svc.test_connection("http://obico:3333", "right")

        assert result["ok"] is True
        assert result["auth_ok"] is True
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_probe_failure_leaves_the_token_unknown_but_keeps_the_test_ok(self):
        svc = ObicoDetectionService()
        mock_client = self._client([MagicMock(status_code=200, text="ok"), RuntimeError("read timeout")])

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            result = await svc.test_connection("http://obico:3333", "maybe")

        assert result["ok"] is True
        assert result["auth_ok"] is None

    @pytest.mark.asyncio
    async def test_unhealthy_server_is_not_probed(self):
        svc = ObicoDetectionService()
        mock_client = self._client([MagicMock(status_code=200, text="error")])

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            result = await svc.test_connection("http://obico:3333", "any")

        assert result["ok"] is False
        assert result["auth_ok"] is None
        assert mock_client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_both_requests_carry_the_header(self):
        svc = ObicoDetectionService()
        mock_client = self._client([MagicMock(status_code=200, text="ok"), MagicMock(status_code=422)])

        with patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client):
            await svc.test_connection("http://obico:3333", "s3cret")

        assert [call.args[0] for call in mock_client.get.await_args_list] == [
            "http://obico:3333/hc/",
            "http://obico:3333/p/",
        ]
        for call in mock_client.get.await_args_list:
            assert call.kwargs["headers"] == {"Authorization": "Bearer s3cret"}

    @pytest.mark.asyncio
    async def test_url_policy_still_applies_before_any_request(self):
        svc = ObicoDetectionService()
        result = await svc.test_connection("http://169.254.169.254/latest/meta-data/", "s3cret")
        assert result["ok"] is False
        assert result["auth_ok"] is None
        assert result["error"]


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

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
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

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, settings)

        assert svc._last_error is not None
        assert "connection refused" in svc._last_error

    @pytest.mark.asyncio
    async def test_ml_api_empty_exception_message_falls_back_to_type(self):
        """If str(exc) is empty, log the exception class name instead of a blank suffix."""
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

        class _SilentError(Exception):
            def __str__(self) -> str:
                return ""

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=_SilentError())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, settings)

        assert svc._last_error is not None
        assert "_SilentError" in svc._last_error
        # The suffix is never blank
        assert not svc._last_error.rstrip().endswith(":")

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
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, settings)
            assert mock_action.call_count == 1
            await svc._check_printer(1, status, settings)
            # Second call must not dispatch again
            assert mock_action.call_count == 1


class TestCaptureFrameSharesBroadcasterUpstream:
    """#1271: Obico's per-poll snapshot must reuse the live-stream broadcaster's
    buffered frame when a viewer is watching, instead of opening a second RTSP
    socket. On X2D firmware 01.01.00.00 the second socket kicks the live stream.
    """

    @pytest.mark.asyncio
    async def test_returns_buffered_frame_when_stream_active(self):
        printer = MagicMock(
            external_camera_enabled=False,
            external_camera_url=None,
            ip_address="192.168.1.10",
            access_code="12345678",
            model="N6",
        )
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=printer)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = ObicoDetectionService()
        with (
            patch("backend.app.services.obico_detection.async_session", return_value=mock_ctx),
            patch(
                "backend.app.api.routes.camera.is_stream_active",
                return_value=True,
            ),
            patch(
                "backend.app.api.routes.camera.try_get_active_buffered_frame",
                return_value=FAKE_JPEG,
            ),
            patch(
                "backend.app.services.camera.capture_camera_frame_bytes",
                new=AsyncMock(return_value=b"FRESH-CAPTURE-SHOULD-NOT-BE-USED"),
            ) as mock_fresh,
        ):
            result = await svc._capture_frame(printer_id=1)

        assert result == FAKE_JPEG
        mock_fresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_poll_when_stream_active_but_buffer_empty(self):
        """#1348: viewer attached + buffer empty must NOT open a competing socket."""
        printer = MagicMock(
            external_camera_enabled=False,
            external_camera_url=None,
            ip_address="192.168.1.10",
            access_code="12345678",
            model="X1C",
        )
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=printer)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = ObicoDetectionService()
        with (
            patch("backend.app.services.obico_detection.async_session", return_value=mock_ctx),
            patch(
                "backend.app.api.routes.camera.is_stream_active",
                return_value=True,
            ),
            patch(
                "backend.app.api.routes.camera.try_get_active_buffered_frame",
                return_value=None,  # Stream active, but first frame not buffered yet
            ),
            patch(
                "backend.app.services.camera.capture_camera_frame_bytes",
                new=AsyncMock(return_value=b"FRESH-CAPTURE-WOULD-KICK-VIEWER"),
            ) as mock_fresh,
        ):
            result = await svc._capture_frame(printer_id=1)

        assert result is None, "must skip this poll cycle, not open a competing socket"
        mock_fresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_fresh_capture_when_no_stream(self):
        printer = MagicMock(
            external_camera_enabled=False,
            external_camera_url=None,
            ip_address="192.168.1.10",
            access_code="12345678",
            model="N6",
        )
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=printer)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        svc = ObicoDetectionService()
        with (
            patch("backend.app.services.obico_detection.async_session", return_value=mock_ctx),
            patch(
                "backend.app.api.routes.camera.is_stream_active",
                return_value=False,
            ),
            patch(
                "backend.app.services.camera.capture_camera_frame_bytes",
                new=AsyncMock(return_value=FAKE_JPEG),
            ) as mock_fresh,
        ):
            result = await svc._capture_frame(printer_id=1)

        assert result == FAKE_JPEG
        mock_fresh.assert_called_once()


class TestFrameCache:
    """One-shot JPEG cache that lets us sidestep Obico's 5s read timeout.

    Obico's ML API fetches snapshots via `GET /p/?img=URL` with `timeout=(0.1, 5)`.
    Our /camera/snapshot can exceed that on cold calls (RTSP keyframe wait). So the
    detection loop captures locally, stashes the JPEG bytes under a nonce, then hands
    Obico a URL that returns those bytes instantly. The cache is single-use + TTLed
    so a leaked nonce can't be replayed.
    """

    def setup_method(self):
        _frame_cache.clear()

    @pytest.mark.asyncio
    async def test_stash_and_pop_roundtrip(self):
        nonce = await stash_frame(FAKE_JPEG)
        assert nonce  # non-empty URL-safe token
        data = await pop_frame(nonce)
        assert data == FAKE_JPEG

    @pytest.mark.asyncio
    async def test_nonce_is_single_use(self):
        nonce = await stash_frame(FAKE_JPEG)
        assert await pop_frame(nonce) == FAKE_JPEG
        # Second pop returns None — caches replay protection
        assert await pop_frame(nonce) is None

    @pytest.mark.asyncio
    async def test_unknown_nonce_returns_none(self):
        assert await pop_frame("not-a-real-nonce") is None

    @pytest.mark.asyncio
    async def test_stash_produces_unique_nonces(self):
        nonces = {await stash_frame(FAKE_JPEG) for _ in range(10)}
        assert len(nonces) == 10

    @pytest.mark.asyncio
    async def test_expired_entries_are_pruned_on_stash(self):
        """New entries trigger pruning of TTL-expired ones — prevents unbounded growth."""
        # Manually seed an entry with a stale timestamp
        import time as time_module

        _frame_cache["stale-nonce"] = (FAKE_JPEG, time_module.monotonic() - FRAME_CACHE_TTL - 1)
        await stash_frame(FAKE_JPEG)
        # Stale entry was pruned
        assert "stale-nonce" not in _frame_cache

    @pytest.mark.asyncio
    async def test_pop_rejects_expired_nonce(self):
        """Even if the entry is still in the dict, an expired TTL returns None."""
        import time as time_module

        _frame_cache["aging-nonce"] = (FAKE_JPEG, time_module.monotonic() - FRAME_CACHE_TTL - 1)
        assert await pop_frame("aging-nonce") is None


class TestCheckPrinterUsesCachedFrameUrl:
    """The URL sent to Obico must point at our nonce endpoint, not /camera/snapshot."""

    def setup_method(self):
        _frame_cache.clear()

    @pytest.mark.asyncio
    async def test_ml_api_called_with_cached_frame_url(self):
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

        mock_response = MagicMock()
        mock_response.json.return_value = {"detections": []}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, settings)

        # ML API was called via GET (Obico's /p/ is GET-only)
        mock_client.get.assert_called_once()
        _args, kwargs = mock_client.get.call_args
        assert _args[0] == "http://obico:3333/p/"
        img_url = kwargs["params"]["img"]
        assert img_url.startswith("http://bambuddy:8000/api/v1/obico/cached-frame/")
        # The path segment after /cached-frame/ is the nonce itself — that nonce must
        # resolve back to our stashed frame (single-use guarantees freshness).
        nonce = img_url.rsplit("/", 1)[-1]
        assert await pop_frame(nonce) == FAKE_JPEG

    @pytest.mark.asyncio
    async def test_capture_failure_skips_ml_call(self):
        """If we can't capture a frame, don't bother the ML API."""
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
        mock_client.get = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=None)),
        ):
            await svc._check_printer(1, status, settings)

        mock_client.get.assert_not_called()
        assert svc._last_error is not None
        assert "Failed to capture snapshot" in svc._last_error

    @pytest.mark.asyncio
    async def test_missing_external_url_skips_ml_call(self):
        """Without external_url, Obico can't reach our cached-frame endpoint."""
        svc = ObicoDetectionService()
        settings = {
            "enabled": True,
            "ml_url": "http://obico:3333",
            "sensitivity": "medium",
            "action": "notify",
            "poll_interval": 10,
            "enabled_printers": None,
            "external_url": "",
        }
        status = MagicMock(state="RUNNING", task_name="job", subtask_name="")

        mock_client = MagicMock()
        mock_client.get = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, settings)

        mock_client.get.assert_not_called()
        assert svc._last_error is not None
        assert "external_url" in svc._last_error

    @pytest.mark.asyncio
    async def test_successful_cycle_clears_previous_error(self):
        """A cold-start RTSP timeout sets _last_error; the next successful poll must clear it.

        Regression for #172: the Status card banner ("Failed to capture snapshot for
        printer 1") stuck around after a one-off cold-start failure even though every
        subsequent poll captured + detected successfully.
        """
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

        # Seed a prior transient error, as would be left by a cold-start capture timeout.
        svc._last_error = "Failed to capture snapshot for printer 1"

        mock_response = MagicMock()
        mock_response.json.return_value = {"detections": []}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, status, settings)

        assert svc._last_error is None


class TestNoVerdictIsNotSafe:
    """A printer nothing is looking at must not report itself as safe (#2952).

    ``get_per_printer`` used to default to ``"safe"`` whenever no verdict had
    been recorded, and the state entry is created when the print is first seen —
    before the first capture, let alone the first inference. So a rejected token,
    an unreachable ML API, a camera that never yields a frame and an unset
    External URL all rendered as a green "Safe" badge at score 0.000, identical
    to a healthy monitored print.

    The reporter of #2952 read exactly that, concluded the detection loop had
    never started, and spent an evening on the network path — while the loop was
    calling the ML API every 10s and being turned away with a 401 that Obico's
    auth decorator rejects before its own request log ever sees it.
    """

    SETTINGS = {
        "enabled": True,
        "ml_url": "http://obico:3333",
        "ml_token": "wrong-token",
        "sensitivity": "medium",
        "action": "notify",
        "poll_interval": 10,
        "enabled_printers": None,
        "external_url": "http://bambuddy:8000",
    }

    @staticmethod
    def _status():
        return MagicMock(state="RUNNING", task_name="job", subtask_name="")

    @staticmethod
    def _client(**kwargs):
        client = MagicMock()
        client.get = AsyncMock(**kwargs)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_rejected_token_reports_error_and_names_the_setting(self):
        svc = ObicoDetectionService()
        response = MagicMock(status_code=401)
        with (
            patch(
                "backend.app.services.obico_detection.httpx.AsyncClient",
                return_value=self._client(return_value=response),
            ),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, self._status(), self.SETTINGS)

        entry = svc.get_per_printer()[1]
        assert entry["class"] == "error"
        assert "ML API Token" in entry["error"]
        assert entry["frame_count"] == 0

    @pytest.mark.asyncio
    async def test_unreachable_ml_api_reports_error(self):
        svc = ObicoDetectionService()
        with (
            patch(
                "backend.app.services.obico_detection.httpx.AsyncClient",
                return_value=self._client(side_effect=RuntimeError("connection refused")),
            ),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, self._status(), self.SETTINGS)

        entry = svc.get_per_printer()[1]
        assert entry["class"] == "error"
        assert "connection refused" in entry["error"]

    @pytest.mark.asyncio
    async def test_failed_capture_reports_error(self):
        svc = ObicoDetectionService()
        with patch.object(svc, "_capture_frame", new=AsyncMock(return_value=None)):
            await svc._check_printer(1, self._status(), self.SETTINGS)

        entry = svc.get_per_printer()[1]
        assert entry["class"] == "error"
        assert "capture" in entry["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_external_url_reports_error(self):
        svc = ObicoDetectionService()
        settings = {**self.SETTINGS, "external_url": ""}
        with patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)):
            await svc._check_printer(1, self._status(), settings)

        entry = svc.get_per_printer()[1]
        assert entry["class"] == "error"
        assert "External URL" in entry["error"]

    @pytest.mark.asyncio
    async def test_a_recovered_printer_goes_back_to_a_real_verdict(self):
        """The error must not stick once polling works again — otherwise the
        badge trades one permanent lie for another."""
        svc = ObicoDetectionService()
        with patch.object(svc, "_capture_frame", new=AsyncMock(return_value=None)):
            await svc._check_printer(1, self._status(), self.SETTINGS)
        assert svc.get_per_printer()[1]["class"] == "error"

        ok = MagicMock(status_code=200)
        ok.json.return_value = {"detections": []}
        ok.raise_for_status = MagicMock()
        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=self._client(return_value=ok)),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, self._status(), self.SETTINGS)

        entry = svc.get_per_printer()[1]
        assert entry["class"] == "safe"
        assert entry["error"] is None
        assert entry["frame_count"] == 1

    @pytest.mark.asyncio
    async def test_state_exists_before_the_first_inference_reports_unknown(self):
        """The window between "print seen" and "first result" is not safe either."""
        from backend.app.services.obico_smoothing import PrintState

        svc = ObicoDetectionService()
        svc._states[1] = PrintState()
        svc._state_keys[1] = "job"

        entry = svc.get_per_printer()[1]
        assert entry["class"] == "unknown"
        assert entry["error"] is None

    @pytest.mark.asyncio
    async def test_error_is_cleared_when_the_print_ends(self):
        """A stale error must not carry into the next print's first poll."""
        svc = ObicoDetectionService()
        with patch.object(svc, "_capture_frame", new=AsyncMock(return_value=None)):
            await svc._check_printer(1, self._status(), self.SETTINGS)
        assert 1 in svc._errors

        idle = MagicMock(state="IDLE", task_name="", subtask_name="")
        manager = MagicMock()
        manager.get_all_statuses.return_value = {1: idle}
        manager.is_connected.return_value = True
        with patch.dict(
            "sys.modules",
            {"backend.app.services.printer_manager": MagicMock(printer_manager=manager)},
        ):
            await svc._poll_once(self.SETTINGS)

        assert svc._errors == {}
        assert svc._last_class == {}
        assert svc.get_per_printer() == {}
