"""Integration tests for Obico API endpoints (#172 follow-up).

Verifies the /obico/cached-frame/{nonce} endpoint used by Obico's ML API to fetch
pre-captured JPEG frames. This endpoint lets the detection loop sidestep Obico's
hardcoded 5s read timeout by pre-populating a cache before issuing the ML call.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, Request, Response

from backend.app.core.printer_scope import ALL_PRINTERS
from backend.app.services.obico_detection import (
    ObicoDetectionService,
    _frame_cache,
    obico_detection_service,
    stash_frame,
)
from backend.app.services.obico_smoothing import PrintState

FAKE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


@pytest.fixture(autouse=True)
def clear_cache():
    _frame_cache.clear()
    yield
    _frame_cache.clear()


class TestObicoCachedFrame:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_valid_nonce_returns_jpeg(self, async_client: AsyncClient):
        """A stashed nonce returns the stored JPEG bytes with image/jpeg."""
        nonce = await stash_frame(FAKE_JPEG)
        response = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == FAKE_JPEG

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_nonce_is_404(self, async_client: AsyncClient):
        """An unguessable URL must not leak that the endpoint exists — return 404."""
        response = await async_client.get("/api/v1/obico/cached-frame/definitely-not-a-real-nonce")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nonce_is_single_use(self, async_client: AsyncClient):
        """A second fetch with the same nonce returns 404 — prevents replay."""
        nonce = await stash_frame(FAKE_JPEG)
        first = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert first.status_code == 200
        second = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert second.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_endpoint_is_public(self, async_client: AsyncClient):
        """Obico's ML API can't send auth headers, so the nonce IS the credential.
        The path must be in PUBLIC_API_PATTERNS (no auth wall)."""
        nonce = await stash_frame(FAKE_JPEG)
        # Intentionally omit any auth headers even if the fixture would normally inject them
        response = await async_client.get(
            f"/api/v1/obico/cached-frame/{nonce}",
            headers={},  # no Authorization header
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_response_is_not_cached(self, async_client: AsyncClient):
        """Browsers/proxies must not hold onto the image after Obico consumes it."""
        nonce = await stash_frame(FAKE_JPEG)
        response = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert response.status_code == 200
        assert "no-store" in response.headers.get("cache-control", "")


class TestBambuddyInternalUrl:
    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("bambuddy_internal_url", "external_url", "expected_base"),
        [
            (None, "https://bambuddy.example.com", "https://bambuddy.example.com"),
            ("", "https://bambuddy.example.com/", "https://bambuddy.example.com"),
            ("http://bambuddy:8000/", "https://bambuddy.example.com", "http://bambuddy:8000"),
            ("http://192.168.1.20:8000", "", "http://192.168.1.20:8000"),
        ],
    )
    async def test_saved_url_controls_snapshot_callback(
        self, async_client: AsyncClient, bambuddy_internal_url, external_url, expected_base
    ):
        updates = {"external_url": external_url, "obico_ml_url": "http://obico:3333"}
        if bambuddy_internal_url is not None:
            updates["bambuddy_internal_url"] = bambuddy_internal_url
        response = await async_client.put("/api/v1/settings/", json=updates)
        assert response.status_code == 200
        saved = (await async_client.get("/api/v1/settings/")).json()
        assert saved["external_url"] == external_url
        assert saved["bambuddy_internal_url"] == (bambuddy_internal_url or "")
        status = (await async_client.get("/api/v1/obico/status")).json()
        assert status["external_url_configured"] is True

        async def fetch_snapshot(url, *, params, headers):
            assert url == "http://obico:3333/p/"
            assert params["img"].startswith(f"{expected_base}/api/v1/obico/cached-frame/")
            frame = await async_client.get(params["img"])
            assert frame.status_code == 200
            assert frame.content == FAKE_JPEG
            return Response(200, json={"detections": []}, request=Request("GET", url))

        svc = ObicoDetectionService()
        settings = await svc._load_settings()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=fetch_snapshot)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=mock_client),
            patch.object(svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            await svc._check_printer(1, MagicMock(state="RUNNING", task_name="job", subtask_name=""), settings)
        mock_client.get.assert_awaited_once()
        assert svc.get_per_printer()[1]["class"] == "safe"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_value_clears_the_bambuddy_internal_url(self, async_client: AsyncClient):
        response = await async_client.put(
            "/api/v1/settings/",
            json={"bambuddy_internal_url": "http://bambuddy:8000", "external_url": "https://bambuddy.example.com"},
        )
        assert response.status_code == 200
        response = await async_client.put("/api/v1/settings/", json={"bambuddy_internal_url": ""})
        assert response.status_code == 200
        assert response.json()["bambuddy_internal_url"] == ""
        assert response.json()["external_url"] == "https://bambuddy.example.com"
        assert (await ObicoDetectionService()._load_settings())["snapshot_base_url"] == "https://bambuddy.example.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "value",
        ["bambuddy:8000", "192.168.1.20:8000", "ftp://bambuddy", "http://", "http://bam buddy:8000", "http://h:99999"],
    )
    async def test_address_without_http_scheme_is_rejected(self, async_client: AsyncClient, value):
        response = await async_client.put("/api/v1/settings/", json={"bambuddy_internal_url": value})
        assert response.status_code == 422
        saved = (await async_client.get("/api/v1/settings/")).json()
        assert saved["bambuddy_internal_url"] == ""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_null_clears_instead_of_storing_none(self, async_client: AsyncClient):
        await async_client.put(
            "/api/v1/settings/",
            json={"bambuddy_internal_url": "http://bambuddy:8000", "external_url": "https://bambuddy.example.com"},
        )
        response = await async_client.put("/api/v1/settings/", json={"bambuddy_internal_url": None})
        assert response.status_code == 200
        assert response.json()["bambuddy_internal_url"] == ""
        assert (await ObicoDetectionService()._load_settings())["snapshot_base_url"] == "https://bambuddy.example.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_surrounding_whitespace_is_stripped(self, async_client: AsyncClient):
        response = await async_client.put(
            "/api/v1/settings/", json={"bambuddy_internal_url": "  http://bambuddy:8000/ "}
        )
        assert response.status_code == 200
        assert response.json()["bambuddy_internal_url"] == "http://bambuddy:8000/"
        assert (await ObicoDetectionService()._load_settings())["snapshot_base_url"] == "http://bambuddy:8000"


class TestObicoPrinterStatus:
    """The lightweight /obico/printer-status endpoint for printer-card badges (#1546)."""

    @pytest.fixture(autouse=True)
    def clear_detection_state(self):
        obico_detection_service._states.clear()
        obico_detection_service._last_class.clear()
        obico_detection_service._errors.clear()
        obico_detection_service._last_error = None
        yield
        obico_detection_service._states.clear()
        obico_detection_service._last_class.clear()
        obico_detection_service._errors.clear()
        obico_detection_service._last_error = None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_per_printer_classification(self, async_client: AsyncClient):
        state = PrintState()
        state.update(0.5)
        obico_detection_service._states[1] = state
        obico_detection_service._last_class[1] = "warning"

        response = await async_client.get("/api/v1/obico/printer-status")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        # None = all printers monitored (no obico_enabled_printers subset configured)
        assert data["monitored_printers"] is None
        entry = data["per_printer"]["1"]
        assert entry["class"] == "warning"
        assert entry["frame_count"] == 1
        assert isinstance(entry["score"], float)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_when_nothing_monitored(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/obico/printer-status")
        assert response.status_code == 200
        assert response.json()["per_printer"] == {}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_monitored_subset_is_returned(self, async_client: AsyncClient, db_session):
        """A configured obico_enabled_printers subset surfaces (as a sorted list) so
        the frontend can show the idle badge only on monitored printers."""
        from backend.app.models.printer import Printer

        # The printers must exist: the setting refuses ids that have none.
        printers = [
            Printer(name=f"P{i}", serial_number=f"OBICO-SUBSET-{i}", ip_address=f"192.0.2.4{i}", access_code="x")
            for i in range(2)
        ]
        db_session.add_all(printers)
        await db_session.commit()
        first, second = sorted(p.id for p in printers)
        update = await async_client.put("/api/v1/settings/", json={"obico_enabled_printers": f"[{second}, {first}]"})
        assert update.status_code == 200, update.text
        try:
            response = await async_client.get("/api/v1/obico/printer-status")
            assert response.json()["monitored_printers"] == [first, second]
        finally:
            await async_client.put("/api/v1/settings/", json={"obico_enabled_printers": ""})

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_last_error_is_surfaced(self, async_client: AsyncClient):
        """The badge modal shows the service's last error (auth disabled in the
        test env, so the settings:read gate on the field is open)."""
        obico_detection_service._last_error = "Failed to capture snapshot for printer 1"
        response = await async_client.get("/api/v1/obico/printer-status")
        assert response.json()["last_error"] == "Failed to capture snapshot for printer 1"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_does_not_leak_settings(self, async_client: AsyncClient):
        """Unlike /obico/status, this endpoint is readable with printers:read only,
        so it must not expose the ML URL or other configuration."""
        response = await async_client.get("/api/v1/obico/printer-status")
        data = response.json()
        for key in ("ml_url", "action", "history", "poll_interval", "external_url_configured"):
            assert key not in data


class TestObicoPrinterStatusNoVerdict:
    """A printer whose detection is not working must not read as monitored (#2952)."""

    @pytest.fixture(autouse=True)
    def clear_detection_state(self):
        obico_detection_service._states.clear()
        obico_detection_service._last_class.clear()
        obico_detection_service._errors.clear()
        obico_detection_service._last_error = None
        yield
        obico_detection_service._states.clear()
        obico_detection_service._last_class.clear()
        obico_detection_service._errors.clear()
        obico_detection_service._last_error = None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_error_class_and_reason_reach_the_card(self, async_client: AsyncClient):
        obico_detection_service._states[1] = PrintState()
        obico_detection_service._errors[1] = "Obico ML API rejected the token (401)."

        response = await async_client.get("/api/v1/obico/printer-status")
        entry = response.json()["per_printer"]["1"]
        assert entry["class"] == "error"
        assert entry["error"] == "Obico ML API rejected the token (401)."

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_monitored_but_no_result_yet_is_unknown_not_safe(self, async_client: AsyncClient):
        obico_detection_service._states[1] = PrintState()

        response = await async_client.get("/api/v1/obico/printer-status")
        entry = response.json()["per_printer"]["1"]
        assert entry["class"] == "unknown"
        assert entry["error"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reason_is_withheld_without_settings_read_but_the_class_is_not(self):
        """The reason can name the ML API base or the External URL, so it stays
        behind settings:read. Whether the print is being watched is not
        configuration, so a printers:read user still gets the class."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.app.api.routes.obico import get_printer_status

        obico_detection_service._states[1] = PrintState()
        obico_detection_service._errors[1] = "ML API call failed: http://192.168.8.9:3333 refused"

        user = MagicMock()
        user.has_permission.return_value = False

        # The route calls _load_settings for the enabled/monitored fields; the
        # redaction under test is independent of them.
        loaded = {"enabled": True, "enabled_printers": None}
        with patch.object(obico_detection_service, "_load_settings", new=AsyncMock(return_value=loaded)):
            data = await get_printer_status(user=user, printer_scope=ALL_PRINTERS, actor=user)
        entry = data["per_printer"][1]
        assert entry["class"] == "error"
        assert entry["error"] is None
        assert data["last_error"] is None
        assert "192.168.8.9" not in str(data)
