"""Tests for SlicerApiService."""

from __future__ import annotations

import httpx
import pytest

from backend.app.services.slicer_api import (
    SlicerApiServerError,
    SlicerApiService,
    SlicerApiUnavailableError,
    SliceResult,
    SlicerInputError,
    _guess_model_content_type,
)


def _mock_client(handler) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient that routes every request through `handler`.
    handler signature: (httpx.Request) -> httpx.Response.
    """
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=10.0)


class TestGuessModelContentType:
    """The sidecar's multer middleware rejects octet-stream for STL uploads,
    so we guess by extension."""

    def test_stl(self):
        assert _guess_model_content_type("Cube.stl") == "model/stl"

    def test_3mf(self):
        assert _guess_model_content_type("Bank.3mf") == "model/3mf"

    def test_3mf_uppercase(self):
        assert _guess_model_content_type("Bank.3MF") == "model/3mf"

    def test_step(self):
        assert _guess_model_content_type("Cube.step") == "model/step"

    def test_stp(self):
        assert _guess_model_content_type("Cube.stp") == "model/step"

    def test_unknown(self):
        assert _guess_model_content_type("foo.bar") == "application/octet-stream"


class TestSliceWithProfiles:
    @pytest.mark.asyncio
    async def test_happy_path_returns_gcode_and_metadata(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body_len"] = len(request.content)
            captured["content_type"] = request.headers.get("content-type", "")
            return httpx.Response(
                status_code=200,
                content=b"; G-CODE START\nG28\n",
                headers={
                    "content-type": "application/octet-stream",
                    "x-print-time-seconds": "656",
                    "x-filament-used-g": "0.94",
                    "x-filament-used-mm": "302.5",
                },
            )

        client = _mock_client(handler)
        service = SlicerApiService("http://sidecar:3000", client=client)

        result = await service.slice_with_profiles(
            model_bytes=b"solid Cube\n",
            model_filename="Cube.stl",
            printer_profile_json='{"name": "p"}',
            process_profile_json='{"name": "pr"}',
            filament_profile_json='{"name": "f"}',
        )

        assert isinstance(result, SliceResult)
        assert result.content == b"; G-CODE START\nG28\n"
        assert result.print_time_seconds == 656
        assert result.filament_used_g == 0.94
        assert result.filament_used_mm == 302.5
        assert captured["url"].endswith("/slice")
        assert captured["content_type"].startswith("multipart/form-data")
        # Roughly: model bytes (>0) + 3 profile JSONs (>0). Sanity check that
        # all four parts hit the wire.
        assert captured["body_len"] > 0

    @pytest.mark.asyncio
    async def test_4xx_raises_slicer_input_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=400,
                json={"message": "Invalid file type for printerProfile."},
            )

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        with pytest.raises(SlicerInputError) as exc_info:
            await service.slice_with_profiles(
                model_bytes=b"x",
                model_filename="Cube.stl",
                printer_profile_json="{}",
                process_profile_json="{}",
                filament_profile_json="{}",
            )
        assert "Invalid file type" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_5xx_raises_server_error(self):
        # 5xx from the sidecar = wrapped CLI failed (segfault, range-check
        # reject, etc). Distinguished from connection failures so callers
        # can retry with a different request shape.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=500,
                json={"message": "Failed to slice the model"},
            )

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        with pytest.raises(SlicerApiServerError) as exc_info:
            await service.slice_with_profiles(
                model_bytes=b"x",
                model_filename="Cube.stl",
                printer_profile_json="{}",
                process_profile_json="{}",
                filament_profile_json="{}",
            )
        assert "Failed to slice the model" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_5xx_includes_sidecar_details_field(self):
        """Sidecar's AppError emits ``{message, details}`` — both must end up
        in the raised error so ``bambuddy.log`` carries the actual CLI
        rejection reason instead of just the generic outer message.
        Pinned to fix the regression where every 3MF slice surfaced as
        the unhelpful ``Failed to slice the model`` line in production."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=500,
                json={
                    "message": "Failed to slice the model",
                    "details": "prime_tower_brim_width: -1 not in range [0, 100]",
                },
            )

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        with pytest.raises(SlicerApiServerError) as exc_info:
            await service.slice_with_profiles(
                model_bytes=b"x",
                model_filename="Cube.stl",
                printer_profile_json="{}",
                process_profile_json="{}",
                filament_profile_json="{}",
            )
        msg = str(exc_info.value)
        assert "Failed to slice the model" in msg
        assert "prime_tower_brim_width: -1" in msg

    @pytest.mark.asyncio
    async def test_5xx_with_only_details_still_surfaces(self):
        """If a future sidecar version emits ``details`` without
        ``message``, fall back to the details string so we don't end up
        with an empty error."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=500,
                json={"details": "Slicer killed by SIGSEGV"},
            )

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        with pytest.raises(SlicerApiServerError) as exc_info:
            await service.slice_with_profiles(
                model_bytes=b"x",
                model_filename="Cube.stl",
                printer_profile_json="{}",
                process_profile_json="{}",
                filament_profile_json="{}",
            )
        assert "SIGSEGV" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_5xx_with_non_json_body_falls_back_to_text(self):
        """Some failure paths (gateway timeouts, bare nginx 502s) return
        plain text rather than the JSON envelope. Don't crash trying to
        decode it — fall back to the text body."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=502, content=b"Bad Gateway")

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        with pytest.raises(SlicerApiServerError) as exc_info:
            await service.slice_with_profiles(
                model_bytes=b"x",
                model_filename="Cube.stl",
                printer_profile_json="{}",
                process_profile_json="{}",
                filament_profile_json="{}",
            )
        assert "Bad Gateway" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connection_error_raises_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        with pytest.raises(SlicerApiUnavailableError) as exc_info:
            await service.slice_with_profiles(
                model_bytes=b"x",
                model_filename="Cube.stl",
                printer_profile_json="{}",
                process_profile_json="{}",
                filament_profile_json="{}",
            )
        assert "unreachable" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_passes_plate_and_export_3mf_options(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(
                status_code=200,
                content=b"3MF-BYTES",
                headers={"x-print-time-seconds": "0", "x-filament-used-g": "0", "x-filament-used-mm": "0"},
            )

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        await service.slice_with_profiles(
            model_bytes=b"x",
            model_filename="Cube.stl",
            printer_profile_json="{}",
            process_profile_json="{}",
            filament_profile_json="{}",
            plate=2,
            export_3mf=True,
        )

        body = captured["body"]
        # Multipart body should contain the form fields. Quick membership
        # check beats parsing the multipart envelope.
        assert b'name="plate"' in body
        assert b"\r\n2\r\n" in body or b'name="plate"\r\n\r\n2' in body
        assert b'name="exportType"' in body
        assert b"3mf" in body

    @pytest.mark.asyncio
    async def test_missing_metadata_headers_default_to_zero(self):
        # The /slice endpoint always sets these on success, but be defensive
        # so a stripped reverse-proxy or older sidecar doesn't crash callers.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b"; gcode")

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        result = await service.slice_with_profiles(
            model_bytes=b"x",
            model_filename="Cube.stl",
            printer_profile_json="{}",
            process_profile_json="{}",
            filament_profile_json="{}",
        )
        assert result.print_time_seconds == 0
        assert result.filament_used_g == 0.0
        assert result.filament_used_mm == 0.0


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={"status": "healthy", "checks": {"orcaslicer": {"available": True}}},
            )

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        body = await service.health()
        assert body["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_unreachable_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        with pytest.raises(SlicerApiUnavailableError):
            await service.health()
