"""Tests for SlicerApiService."""

from __future__ import annotations

import asyncio

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
            filament_profile_jsons=['{"name": "f"}'],
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
                filament_profile_jsons=["{}"],
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
                filament_profile_jsons=["{}"],
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
                filament_profile_jsons=["{}"],
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
                filament_profile_jsons=["{}"],
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
                filament_profile_jsons=["{}"],
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
                filament_profile_jsons=["{}"],
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
            filament_profile_jsons=["{}"],
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
    async def test_multi_filament_sends_one_part_per_profile(self):
        # Multi-color slicing requires N filament profiles, in plate-slot
        # order, sent as N repeated multipart `filamentProfile` parts (NOT a
        # single concatenated value). The CLI joins their resulting paths
        # with `;` for --load-filaments. A future regression to a dict-shaped
        # `files=` would silently keep prior tests green but ship only the
        # last filament — pin the wire shape.
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
            model_filename="Cube.3mf",
            printer_profile_json="{}",
            process_profile_json="{}",
            filament_profile_jsons=['{"a":1}', '{"b":2}', '{"c":3}'],
        )

        body = captured["body"]
        # Three repeated `filamentProfile` parts, in submission order.
        assert body.count(b'name="filamentProfile"') == 3
        assert b'{"a":1}' in body and b'{"b":2}' in body and b'{"c":3}' in body
        # Parts present in plate order — the 'a' bytes appear before 'b'
        # which appear before 'c'. (httpx preserves the list order.)
        assert body.index(b'{"a":1}') < body.index(b'{"b":2}') < body.index(b'{"c":3}')

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
            filament_profile_jsons=["{}"],
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


class TestSliceWithProfilesProgress:
    """Live-progress wiring for slice_with_profiles.

    When the caller supplies a ``request_id`` and an ``on_progress``
    callback, the service forwards the id as a ``requestId`` form field
    (the sidecar uses it to wire up `--pipe` per request) and spawns a
    background poller that calls back into ``on_progress`` for each
    snapshot the sidecar publishes. The poller is cancelled the moment
    the slice POST returns.
    """

    @pytest.mark.asyncio
    async def test_request_id_forwarded_as_form_field(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/slice":
                captured["body"] = request.content
                return httpx.Response(
                    status_code=200,
                    content=b"PK\x03\x04 fake",
                    headers={"x-print-time-seconds": "1", "x-filament-used-g": "0", "x-filament-used-mm": "0"},
                )
            # /slice/progress/<id> — return 404 so the poller exits cleanly.
            return httpx.Response(status_code=404, json={"error": "not_found"})

        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        await service.slice_with_profiles(
            model_bytes=b"x",
            model_filename="Cube.stl",
            printer_profile_json="{}",
            process_profile_json="{}",
            filament_profile_jsons=["{}"],
            request_id="abc-123",
            on_progress=lambda _snap: None,
        )
        # The form field name on the wire is `requestId` (camelCase) to
        # match the sidecar's SlicingSettings shape.
        body = captured["body"].decode("utf-8", errors="ignore")
        assert "requestId" in body
        assert "abc-123" in body

    @pytest.mark.asyncio
    async def test_on_progress_called_with_snapshots(self):
        # Drive enough poller ticks for at least one progress 200 to land
        # before the slice response unblocks the caller.
        slice_release = asyncio.Event()
        snapshots: list[dict] = []

        async def slice_handler() -> httpx.Response:
            # Hold the slice POST until the test signals release, mimicking
            # a real long-running slice.
            await slice_release.wait()
            return httpx.Response(
                status_code=200,
                content=b"PK\x03\x04",
                headers={"x-print-time-seconds": "1", "x-filament-used-g": "0", "x-filament-used-mm": "0"},
            )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/slice":
                # MockTransport supports async handlers if we return a
                # coroutine — but the simpler path is to drive completion
                # via the captured event below.
                pass
            if request.url.path == "/slice/progress/req-1":
                return httpx.Response(
                    status_code=200,
                    json={
                        "stage": "Generating G-code",
                        "total_percent": 75,
                        "plate_percent": 80,
                        "plate_index": 1,
                        "plate_count": 1,
                        "updated_at": 0,
                    },
                )
            return httpx.Response(404)

        # Use an async handler so the slice POST blocks until released.
        async def async_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/slice":
                return await slice_handler()
            return handler(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(async_handler))
        service = SlicerApiService("http://sidecar:3000", client=client)

        # Run the slice with progress callback, releasing it after a beat.
        async def release_after_first_snapshot():
            # Wait until the poller has published at least one snapshot
            # via the on_progress callback, then unblock the slice POST.
            for _ in range(60):
                if snapshots:
                    break
                await asyncio.sleep(0.05)
            slice_release.set()

        release_task = asyncio.create_task(release_after_first_snapshot())
        try:
            await service.slice_with_profiles(
                model_bytes=b"x",
                model_filename="Cube.stl",
                printer_profile_json="{}",
                process_profile_json="{}",
                filament_profile_jsons=["{}"],
                request_id="req-1",
                on_progress=lambda snap: snapshots.append(snap),
            )
        finally:
            release_task.cancel()
            await asyncio.gather(release_task, return_exceptions=True)
            await client.aclose()

        assert snapshots, "on_progress was never called"
        first = snapshots[0]
        assert first["stage"] == "Generating G-code"
        assert first["total_percent"] == 75

    @pytest.mark.asyncio
    async def test_progress_404_does_not_crash_or_stop_polling(self):
        """A 404 from /slice/progress/:id is expected during the early
        race window (POST fired before sidecar's progressStore.start()
        ran) and from older sidecars without progress support. Neither
        should crash the slice or block the response — the poller just
        keeps trying until the outer cancel fires."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/slice":
                return httpx.Response(
                    status_code=200,
                    content=b"PK\x03\x04",
                    headers={"x-print-time-seconds": "1", "x-filament-used-g": "0", "x-filament-used-mm": "0"},
                )
            return httpx.Response(status_code=404, json={"error": "not_found"})

        snapshots: list[dict] = []
        service = SlicerApiService("http://sidecar:3000", client=_mock_client(handler))
        result = await service.slice_with_profiles(
            model_bytes=b"x",
            model_filename="Cube.stl",
            printer_profile_json="{}",
            process_profile_json="{}",
            filament_profile_jsons=["{}"],
            request_id="legacy-sidecar",
            on_progress=lambda snap: snapshots.append(snap),
        )
        assert result is not None
        # Sustained 404 → no snapshots ever forwarded.
        assert snapshots == []
