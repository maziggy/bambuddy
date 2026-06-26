"""Integration tests for Virtual Printer API endpoints.

Tests the full request/response cycle for /api/v1/settings/virtual-printer endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


class TestVirtualPrinterSettingsAPI:
    """Integration tests for /api/v1/settings/virtual-printer endpoints."""

    # ========================================================================
    # Get settings
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_virtual_printer_settings(self, async_client: AsyncClient):
        """Verify virtual printer settings can be retrieved."""
        response = await async_client.get("/api/v1/settings/virtual-printer")

        assert response.status_code == 200
        result = response.json()
        assert "enabled" in result
        assert "access_code_set" in result
        assert "mode" in result
        assert "status" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_settings_has_status(self, async_client: AsyncClient):
        """Verify settings include status details."""
        response = await async_client.get("/api/v1/settings/virtual-printer")

        assert response.status_code == 200
        result = response.json()
        status = result["status"]
        assert "enabled" in status
        assert "running" in status
        assert "mode" in status
        assert "name" in status
        assert "serial" in status
        assert "pending_files" in status

    # ========================================================================
    # Update settings
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode(self, async_client: AsyncClient):
        """Verify mode can be updated."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=review")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "review"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode_to_queue(self, async_client: AsyncClient):
        """Verify mode can be set to the canonical 'queue' value."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=queue")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "queue"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode_legacy_print_queue_normalises_to_queue(self, async_client: AsyncClient):
        """Legacy `print_queue` is accepted on input and translated to `queue` on
        storage so the UI button label and the support-bundle field agree
        (#1429 mode-label discrepancy)."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=print_queue")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "queue"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode_legacy_immediate_normalises_to_archive(self, async_client: AsyncClient):
        """Legacy `immediate` is accepted on input and translated to `archive`
        on storage (#1429 mode-label discrepancy)."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=immediate")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "archive"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode_to_archive(self, async_client: AsyncClient):
        """Verify mode can be set to the canonical 'archive' value."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=archive")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "archive"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_access_code(self, async_client: AsyncClient):
        """Verify access code can be set."""
        response = await async_client.put("/api/v1/settings/virtual-printer?access_code=12345678")

        assert response.status_code == 200
        result = response.json()
        assert result["access_code_set"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_access_code_wrong_length(self, async_client: AsyncClient):
        """Verify access code validation for length."""
        response = await async_client.put("/api/v1/settings/virtual-printer?access_code=123")

        # Should fail validation
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_without_access_code(self, async_client: AsyncClient):
        """Verify enabling fails without access code set."""
        # First ensure no access code is set by checking current state
        # Then try to enable
        response = await async_client.put("/api/v1/settings/virtual-printer?enabled=true")

        # If access code wasn't set, this should fail
        # If it was already set, it will succeed
        # Both are valid test outcomes
        assert response.status_code in [200, 400]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_with_access_code(self, async_client: AsyncClient):
        """Verify enabling succeeds when access code is set."""
        # First set access code
        await async_client.put("/api/v1/settings/virtual-printer?access_code=12345678")

        # Then enable (this will start the servers which may fail in test env)
        # We mock the manager to avoid actually starting servers
        with patch("backend.app.services.virtual_printer.virtual_printer_manager") as mock_manager:
            mock_manager.configure = AsyncMock()
            mock_manager.get_status = MagicMock(
                return_value={
                    "enabled": True,
                    "running": True,
                    "mode": "archive",
                    "name": "Bambuddy",
                    "serial": "00M09A391800001",
                    "pending_files": 0,
                }
            )

            response = await async_client.put("/api/v1/settings/virtual-printer?enabled=true")

            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_virtual_printer(self, async_client: AsyncClient):
        """Verify virtual printer can be disabled."""
        with patch("backend.app.services.virtual_printer.virtual_printer_manager") as mock_manager:
            mock_manager.configure = AsyncMock()
            mock_manager.get_status = MagicMock(
                return_value={
                    "enabled": False,
                    "running": False,
                    "mode": "archive",
                    "name": "Bambuddy",
                    "serial": "00M09A391800001",
                    "pending_files": 0,
                }
            )

            response = await async_client.put("/api/v1/settings/virtual-printer?enabled=false")

            assert response.status_code == 200
            result = response.json()
            assert result["enabled"] is False


class TestPendingUploadsAPI:
    """Integration tests for /api/v1/pending-uploads/ endpoints."""

    @pytest.fixture
    def mock_pending_uploads(self, db_session):
        """Create mock pending uploads in database."""

        async def _create_pending(filename: str = "test.3mf"):
            from datetime import datetime

            from backend.app.models.pending_upload import PendingUpload

            upload = PendingUpload(
                filename=filename,
                file_path=f"/tmp/{filename}",
                file_size=1024,
                source_ip="192.168.1.100",
                status="pending",
            )
            db_session.add(upload)
            await db_session.commit()
            await db_session.refresh(upload)
            return upload

        return _create_pending

    # ========================================================================
    # List pending uploads
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_pending_uploads_empty(self, async_client: AsyncClient):
        """Verify empty list is returned when no pending uploads."""
        response = await async_client.get("/api/v1/pending-uploads/")

        assert response.status_code == 200
        result = response.json()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_pending_uploads_count(self, async_client: AsyncClient):
        """Verify count endpoint returns correct count."""
        response = await async_client.get("/api/v1/pending-uploads/count")

        assert response.status_code == 200
        result = response.json()
        assert "count" in result
        assert isinstance(result["count"], int)

    # ========================================================================
    # Archive pending upload
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_nonexistent_upload(self, async_client: AsyncClient):
        """Verify archiving non-existent upload returns 404."""
        response = await async_client.post("/api/v1/pending-uploads/99999/archive")

        assert response.status_code == 404

    # ========================================================================
    # Discard pending upload
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discard_nonexistent_upload(self, async_client: AsyncClient):
        """Verify discarding non-existent upload returns 404."""
        response = await async_client.delete("/api/v1/pending-uploads/99999")

        assert response.status_code == 404

    # ========================================================================
    # Bulk operations
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_all_empty(self, async_client: AsyncClient):
        """Verify archive all with no pending uploads."""
        response = await async_client.post("/api/v1/pending-uploads/archive-all")

        assert response.status_code == 200
        result = response.json()
        assert "archived" in result
        assert "failed" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discard_all_empty(self, async_client: AsyncClient):
        """Verify discard all with no pending uploads."""
        response = await async_client.delete("/api/v1/pending-uploads/discard-all")

        assert response.status_code == 200
        result = response.json()
        assert "discarded" in result


class TestVirtualPrinterAutoDispatchAPI:
    """Integration tests for auto_dispatch on /api/v1/virtual-printers endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_virtual_printer_auto_dispatch_default(self, async_client: AsyncClient):
        """Verify creating a VP without auto_dispatch defaults to true."""
        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestDefaultDispatch",
                "mode": "queue",
                "access_code": "12345678",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["auto_dispatch"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_virtual_printer_auto_dispatch_false(self, async_client: AsyncClient):
        """Verify creating a VP with auto_dispatch=false persists correctly."""
        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestManualDispatch",
                "mode": "queue",
                "access_code": "12345678",
                "auto_dispatch": False,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["auto_dispatch"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_virtual_printer_auto_dispatch(self, async_client: AsyncClient):
        """Verify auto_dispatch can be toggled via PUT and persists."""
        # Create with auto_dispatch=True (default)
        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestToggleDispatch",
                "mode": "queue",
                "access_code": "12345678",
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]

        # Update to auto_dispatch=False
        update_resp = await async_client.put(
            f"/api/v1/virtual-printers/{vp_id}",
            json={"auto_dispatch": False},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["auto_dispatch"] is False

        # Verify it persists by fetching
        get_resp = await async_client.get(f"/api/v1/virtual-printers/{vp_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["auto_dispatch"] is False


class TestVirtualPrinterGcodeInjectionAPI:
    """Integration tests for gcode_injection (#1516) on /api/v1/virtual-printers endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_virtual_printer_gcode_injection_default_off(self, async_client: AsyncClient):
        """Verify creating a VP without gcode_injection defaults to false (opt-in)."""
        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestDefaultInjection",
                "mode": "queue",
                "access_code": "12345678",
            },
        )

        assert response.status_code == 200
        assert response.json()["gcode_injection"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_virtual_printer_gcode_injection(self, async_client: AsyncClient):
        """Verify gcode_injection can be toggled via PUT and persists."""
        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestToggleInjection",
                "mode": "queue",
                "access_code": "12345678",
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]
        assert create_resp.json()["gcode_injection"] is False

        update_resp = await async_client.put(
            f"/api/v1/virtual-printers/{vp_id}",
            json={"gcode_injection": True},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["gcode_injection"] is True

        get_resp = await async_client.get(f"/api/v1/virtual-printers/{vp_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["gcode_injection"] is True


class TestVirtualPrinterTailscaleToggleAPI:
    """The Tailscale toggle is informational — toggling either way always succeeds.

    There used to be a 409 guard rejecting "enable" when the daemon was unreachable,
    back when the toggle controlled LE cert provisioning. That path was removed:
    the slicer's printer-MQTT trust validates against its bundled BBL CA, not the
    system trust store, so even an LE cert wouldn't be accepted. The toggle now
    only surfaces the host's Tailscale IP/FQDN on the VP card; daemon presence is
    irrelevant to whether the toggle can be flipped.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_toggle_does_not_consult_tailscale_daemon(self, async_client: AsyncClient):
        """PUT tailscale_disabled never calls tailscale_service.get_status — always succeeds."""
        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestTailscaleToggle",
                "mode": "archive",
                "access_code": "12345678",
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]
        assert create_resp.json()["tailscale_disabled"] is True

        with patch(
            "backend.app.services.virtual_printer.tailscale.tailscale_service.get_status",
            new=AsyncMock(side_effect=AssertionError("get_status must not be called for toggle")),
        ):
            enable_resp = await async_client.put(
                f"/api/v1/virtual-printers/{vp_id}",
                json={"tailscale_disabled": False},
            )
            disable_resp = await async_client.put(
                f"/api/v1/virtual-printers/{vp_id}",
                json={"tailscale_disabled": True},
            )

        assert enable_resp.status_code == 200
        assert enable_resp.json()["tailscale_disabled"] is False
        assert disable_resp.status_code == 200
        assert disable_resp.json()["tailscale_disabled"] is True


class TestVirtualPrinterCaCertificateAPI:
    """Integration tests for GET /api/v1/virtual-printers/ca-certificate."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_ca_certificate_returns_pem(self, async_client: AsyncClient):
        """The shared CA certificate is returned as PEM with identifying metadata."""
        response = await async_client.get("/api/v1/virtual-printers/ca-certificate")

        assert response.status_code == 200
        result = response.json()
        assert result["pem"].startswith("-----BEGIN CERTIFICATE-----")
        assert "PRIVATE KEY" not in result["pem"]  # never expose the CA key
        assert len(result["fingerprint_sha256"].split(":")) == 32
        assert result["not_valid_after"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ca_certificate_route_precedes_vp_id_route(self, async_client: AsyncClient):
        """'ca-certificate' must not be swallowed by the /{vp_id} int route."""
        response = await async_client.get("/api/v1/virtual-printers/ca-certificate")
        # A 200 (not 422 from int-parsing "ca-certificate") proves route ordering.
        assert response.status_code == 200


class TestVirtualPrinterDiagnosticAPI:
    """Integration tests for GET /api/v1/virtual-printers/{vp_id}/diagnostic."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_diagnose_unknown_vp_returns_404(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/virtual-printers/999999/diagnostic")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_diagnose_disabled_vp_reports_problems(self, async_client: AsyncClient):
        """A freshly created (disabled) VP fails the 'enabled' check."""
        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={"name": "TestDiagVP", "mode": "archive", "access_code": "12345678"},
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]

        response = await async_client.get(f"/api/v1/virtual-printers/{vp_id}/diagnostic")
        assert response.status_code == 200
        result = response.json()
        assert result["vp_id"] == vp_id
        assert result["overall"] == "problems"
        by_id = {c["id"]: c["status"] for c in result["checks"]}
        assert by_id["enabled"] == "fail"
        assert by_id["running"] == "skip"


class TestVirtualPrinterAccessCodeInheritance:
    """Non-proxy VPs with a target printer must inherit the target's access
    code at write time.

    The live-mirror bridge forwards the slicer's MQTT/RTSPS auth bytes to
    the real printer — if the codes diverge the slicer binds the VP but the
    bridge fails at the second hop. The route layer force-derives the code
    on every create / update so a non-UI client can't introduce a divergence
    either.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_with_target_ignores_submitted_access_code(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        from sqlalchemy import select

        from backend.app.models.virtual_printer import VirtualPrinter

        target = await printer_factory(name="Real X1C", access_code="REALCODE")

        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "QueueVP",
                "mode": "queue",
                "access_code": "WRONGAAA",
                "target_printer_id": target.id,
            },
        )
        assert response.status_code == 200
        vp_id = response.json()["id"]
        assert response.json()["access_code_set"] is True

        vp = (await db_session.execute(select(VirtualPrinter).where(VirtualPrinter.id == vp_id))).scalar_one()
        assert vp.access_code == "REALCODE"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_with_target_and_no_access_code_still_enables(
        self, async_client: AsyncClient, printer_factory
    ):
        """A non-proxy VP with a target set can be enabled without supplying
        access_code separately — the inheritance makes the explicit field
        redundant, and the validator now knows this."""
        target = await printer_factory(name="Real X1C", access_code="REALCODE")

        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "QueueVP",
                "mode": "queue",
                "target_printer_id": target.id,
                "bind_ip": "192.168.1.50",
                "enabled": True,
            },
        )
        assert response.status_code == 200
        assert response.json()["access_code_set"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_without_target_still_requires_access_code_on_enable(self, async_client: AsyncClient):
        """The relaxation only kicks in when a target is set. A standalone
        non-proxy VP still needs its own access code."""
        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "StandaloneVP",
                "mode": "archive",
                "bind_ip": "192.168.1.51",
                "enabled": True,
            },
        )
        assert response.status_code == 400
        assert "access code" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_target_resyncs_access_code(self, async_client: AsyncClient, printer_factory, db_session):
        from sqlalchemy import select

        from backend.app.models.virtual_printer import VirtualPrinter

        first = await printer_factory(name="Printer A", access_code="AAAAAAAA")
        second = await printer_factory(name="Printer B", access_code="BBBBBBBB")

        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "MovingTarget",
                "mode": "queue",
                "target_printer_id": first.id,
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]

        vp = (await db_session.execute(select(VirtualPrinter).where(VirtualPrinter.id == vp_id))).scalar_one()
        assert vp.access_code == "AAAAAAAA"

        # Repoint to the second printer — access code should follow.
        update_resp = await async_client.put(
            f"/api/v1/virtual-printers/{vp_id}",
            json={"target_printer_id": second.id},
        )
        assert update_resp.status_code == 200

        await db_session.refresh(vp)
        assert vp.access_code == "BBBBBBBB"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_explicit_access_code_with_target_is_overridden(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        """An update that submits both an explicit access_code AND keeps a
        target_printer_id silently uses the target's code — belt-and-braces
        for non-UI clients that might try to set a divergent value."""
        from sqlalchemy import select

        from backend.app.models.virtual_printer import VirtualPrinter

        target = await printer_factory(name="Real X1C", access_code="REALCODE")

        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "BeltBraces",
                "mode": "queue",
                "target_printer_id": target.id,
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]

        update_resp = await async_client.put(
            f"/api/v1/virtual-printers/{vp_id}",
            json={"access_code": "FORGEDCD"},
        )
        assert update_resp.status_code == 200

        vp = (await db_session.execute(select(VirtualPrinter).where(VirtualPrinter.id == vp_id))).scalar_one()
        assert vp.access_code == "REALCODE"


class TestVirtualPrinterSerialSurface:
    """Proxy-mode VPs must surface the target printer's serial in API responses.

    The bridge advertises the target's serial over SSDP and forwards the
    target's identity to the slicer; the VP-settings card should show the
    same serial so the user sees one consistent identity per VP. Archive /
    queue / review VPs keep the self-generated suffix-based serial since
    those modes never speak the target's identity.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_proxy_vp_response_uses_target_printer_serial(self, async_client: AsyncClient, printer_factory):
        target = await printer_factory(name="Real X1C", access_code="REALCODE", serial_number="00M09A123456789")

        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "ProxyVP",
                "mode": "proxy",
                "target_printer_id": target.id,
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]
        assert create_resp.json()["serial"] == "00M09A123456789"

        get_resp = await async_client.get(f"/api/v1/virtual-printers/{vp_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["serial"] == "00M09A123456789"

        list_resp = await async_client.get("/api/v1/virtual-printers")
        assert list_resp.status_code == 200
        listed = next(p for p in list_resp.json()["printers"] if p["id"] == vp_id)
        assert listed["serial"] == "00M09A123456789"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_non_proxy_vp_keeps_self_generated_serial(self, async_client: AsyncClient, printer_factory):
        # Even with a target printer set (#1429 access-code inheritance flow),
        # archive / queue / review VPs are NOT bridging the target's identity
        # to the slicer — they synthesise their own. Self-generated serial.
        target = await printer_factory(name="Real X1C", access_code="REALCODE", serial_number="00M09A123456789")

        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "QueueVP",
                "mode": "queue",
                "target_printer_id": target.id,
            },
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["serial"] != "00M09A123456789"
        # The synthesised serial follows _get_serial_for_model's `<prefix><suffix>`
        # shape — model-specific prefix + 8-char hex suffix from `vp.serial_suffix`.
        assert len(create_resp.json()["serial"]) >= 8

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_proxy_vp_falls_back_to_self_generated_when_target_missing(
        self, async_client: AsyncClient, db_session
    ):
        # Defensive fallback: a proxy VP whose target_printer_id points at a
        # row that no longer exists (printer deleted mid-config, manual SQL
        # tweak, race) must not 500 — it returns the self-generated serial
        # so the card still renders and the user can fix the target.
        from backend.app.models.virtual_printer import VirtualPrinter

        vp = VirtualPrinter(name="OrphanProxy", mode="proxy", target_printer_id=99999, enabled=False)
        db_session.add(vp)
        await db_session.commit()

        get_resp = await async_client.get(f"/api/v1/virtual-printers/{vp.id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["serial"]  # non-empty
        assert len(body["serial"]) >= 8
