"""Unit tests for the staged camera diagnostic.

Covers the per-stage pass/fail contract that drives the frontend
remediation hints. The live-stream shortcut and the failure-to-summary
mapping are the load-bearing pieces — both are pinned with explicit
tests so future profile/protocol changes don't silently turn
"camera_port_closed" into "printer_unreachable".
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.camera_diagnose import (
    _LIVE_FRAME_FRESHNESS_SECONDS,
    diagnose_camera,
)


class TestLiveStreamShortcut:
    """If a viewer is currently watching the camera with a fresh frame,
    diagnose must NOT open a fresh socket — single-camera-connection
    firmwares would kick the live viewer off. Trust the live evidence.
    """

    @pytest.mark.asyncio
    async def test_skips_test_when_fresh_frame_in_active_stream(self):
        result = await diagnose_camera(
            ip_address="192.0.2.1",
            access_code="x",
            model="X1C",
            printer_id=1,
            has_live_stream=True,
            live_frame_age_seconds=2.0,
        )
        assert result.overall_status == "ok"
        assert result.summary_code == "live_stream_active_healthy"
        assert len(result.stages) == 1
        assert result.stages[0].name == "live_stream_active"
        assert result.stages[0].status == "ok"

    @pytest.mark.asyncio
    async def test_runs_test_when_stale_frame_in_active_stream(self):
        """An active stream with a stale buffered frame (e.g. mid-
        reconnect) shouldn't short-circuit — the stream might be
        wedged and the user needs the real test."""
        with patch(
            "backend.app.services.camera_diagnose.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="X1C",
                printer_id=1,
                has_live_stream=True,
                live_frame_age_seconds=_LIVE_FRAME_FRESHNESS_SECONDS + 5,
            )
        # No short-circuit — we ran the real check and it failed.
        assert result.summary_code != "live_stream_active_healthy"
        assert any(s.name == "tcp_reachable" for s in result.stages)


class TestTcpStage:
    """The first stage answers "can we even talk to the printer at all".
    The three failure modes (timeout / refused / unreachable) map to
    distinct user-facing remediation hints, so the codes must round-
    trip correctly through ``_summary_for_stages``."""

    @pytest.mark.asyncio
    async def test_timeout_maps_to_printer_unreachable(self):
        with patch(
            "backend.app.services.camera_diagnose.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.99",
                access_code="x",
                model="P2S",
                printer_id=1,
            )
        assert result.overall_status == "failed"
        assert result.summary_code == "printer_unreachable"
        first = result.stages[0]
        assert first.name == "tcp_reachable"
        assert first.code == "tcp_timeout"
        # Second stage was skipped — no point spawning ffmpeg with no socket.
        assert result.stages[1].name == "first_frame"
        assert result.stages[1].status == "skipped"

    @pytest.mark.asyncio
    async def test_connection_refused_maps_to_camera_port_closed(self):
        """ConnectionRefusedError = printer up, port closed. Common
        cause: LAN-only mode off, or developer mode off. The user
        sees a specific remediation hint, not the generic
        'unreachable' message."""
        with patch(
            "backend.app.services.camera_diagnose.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError(),
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="P2S",
                printer_id=1,
            )
        assert result.summary_code == "camera_port_closed"
        assert result.stages[0].code == "tcp_refused"

    @pytest.mark.asyncio
    async def test_oserror_maps_to_printer_unreachable(self):
        """Generic OSError (no-route-to-host etc.) lumps under
        'printer_unreachable' — same remediation as timeout."""
        with patch(
            "backend.app.services.camera_diagnose.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=OSError("No route to host"),
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="P2S",
                printer_id=1,
            )
        assert result.summary_code == "printer_unreachable"
        assert result.stages[0].code == "tcp_unreachable"


class TestFirstFrameStage:
    """The second stage answers "is the camera actually producing
    frames". If TCP passes but no frame comes back, the answer is the
    same regardless of which sub-layer failed (auth, RTSP handshake,
    keyframe probe): the user can't see the camera."""

    @pytest.mark.asyncio
    async def test_no_frame_maps_to_no_frame_summary(self):
        async def _tcp_ok(*_a, **_kw):
            writer = AsyncMock()
            return AsyncMock(), writer

        with (
            patch(
                "backend.app.services.camera_diagnose.asyncio.open_connection",
                new=_tcp_ok,
            ),
            patch(
                "backend.app.services.camera_diagnose.capture_camera_frame_bytes",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="P2S",
                printer_id=1,
            )
        assert result.overall_status == "failed"
        assert result.summary_code == "no_frame"
        assert result.stages[0].status == "ok"
        assert result.stages[1].name == "first_frame"
        assert result.stages[1].code == "no_frame"

    @pytest.mark.asyncio
    async def test_capture_exception_maps_to_no_frame_summary(self):
        """ffmpeg crash / TLS proxy startup failure / etc. — all the
        sub-layer exceptions surface as 'no_frame' for the user, with
        a distinct ``capture_exception`` code in the stage so the
        support log retains the distinction."""

        async def _tcp_ok(*_a, **_kw):
            writer = AsyncMock()
            return AsyncMock(), writer

        with (
            patch(
                "backend.app.services.camera_diagnose.asyncio.open_connection",
                new=_tcp_ok,
            ),
            patch(
                "backend.app.services.camera_diagnose.capture_camera_frame_bytes",
                new_callable=AsyncMock,
                side_effect=RuntimeError("ffmpeg died"),
            ),
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="P2S",
                printer_id=1,
            )
        assert result.summary_code == "no_frame"
        assert result.stages[1].code == "capture_exception"

    @pytest.mark.asyncio
    async def test_full_success_path(self):
        async def _tcp_ok(*_a, **_kw):
            writer = AsyncMock()
            return AsyncMock(), writer

        with (
            patch(
                "backend.app.services.camera_diagnose.asyncio.open_connection",
                new=_tcp_ok,
            ),
            patch(
                "backend.app.services.camera_diagnose.capture_camera_frame_bytes",
                new_callable=AsyncMock,
                return_value=b"\xff\xd8\xff\xd9",  # tiny valid-looking JPEG
            ),
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="P2S",
                printer_id=1,
            )
        assert result.overall_status == "ok"
        assert result.summary_code == "all_ok"
        assert all(s.status == "ok" for s in result.stages)


class TestResultMetadata:
    """Surface fields the support triage relies on — protocol, port,
    profile name. The frontend renders these so we can ask the user
    'is your profile 'P2S' or 'default'?' over a screenshot rather
    than asking for the support bundle."""

    @pytest.mark.asyncio
    async def test_p2s_reports_p2s_profile_and_rtsp_protocol(self):
        with patch(
            "backend.app.services.camera_diagnose.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="P2S",
                printer_id=1,
            )
        assert result.protocol == "rtsp"
        assert result.profile == "P2S"
        assert result.port == 322

    @pytest.mark.asyncio
    async def test_a1_reports_default_profile_and_chamber_protocol(self):
        with patch(
            "backend.app.services.camera_diagnose.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="A1",
                printer_id=1,
            )
        assert result.protocol == "chamber_image"
        assert result.profile == "default"
        assert result.port == 6000

    @pytest.mark.asyncio
    async def test_x1c_reports_default_profile_and_rtsp(self):
        with patch(
            "backend.app.services.camera_diagnose.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            result = await diagnose_camera(
                ip_address="192.0.2.1",
                access_code="x",
                model="X1C",
                printer_id=1,
            )
        assert result.protocol == "rtsp"
        assert result.profile == "default"
        assert result.port == 322
