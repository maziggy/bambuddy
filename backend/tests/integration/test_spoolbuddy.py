"""Integration tests for SpoolBuddy API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice

API = "/api/v1/spoolbuddy"


@pytest.fixture
def device_factory(db_session: AsyncSession):
    """Factory to create SpoolBuddyDevice records."""
    _counter = [0]

    async def _create(**kwargs):
        _counter[0] += 1
        n = _counter[0]
        defaults = {
            "device_id": f"sb-{n:04d}",
            "hostname": f"spoolbuddy-{n}",
            "ip_address": f"10.0.0.{n}",
            "firmware_version": "1.0.0",
            "has_nfc": True,
            "has_scale": True,
            "tare_offset": 0,
            "calibration_factor": 1.0,
            "last_seen": datetime.now(timezone.utc),
        }
        defaults.update(kwargs)
        device = SpoolBuddyDevice(**defaults)
        db_session.add(device)
        await db_session.commit()
        await db_session.refresh(device)
        return device

    return _create


@pytest.fixture
def spool_factory(db_session: AsyncSession):
    """Factory to create Spool records."""
    _counter = [0]

    async def _create(**kwargs):
        _counter[0] += 1
        defaults = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Polymaker",
            "color_name": "Red",
            "rgba": "FF0000FF",
            "label_weight": 1000,
            "core_weight": 250,
            "weight_used": 0,
        }
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create


# ============================================================================
# Device endpoints
# ============================================================================


class TestDeviceEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_register_new_device(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/register",
                json={
                    "device_id": "sb-new",
                    "hostname": "spoolbuddy-new",
                    "ip_address": "10.0.0.99",
                    "firmware_version": "1.2.0",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["device_id"] == "sb-new"
        assert data["hostname"] == "spoolbuddy-new"
        assert data["online"] is True
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_online"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_re_register_existing_device(self, async_client: AsyncClient, device_factory):
        device = await device_factory(
            device_id="sb-exist",
            tare_offset=12345,
            calibration_factor=0.0042,
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/register",
                json={
                    "device_id": "sb-exist",
                    "hostname": "updated-host",
                    "ip_address": "10.0.0.200",
                    "firmware_version": "2.0.0",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == device.id
        assert data["hostname"] == "updated-host"
        assert data["ip_address"] == "10.0.0.200"
        assert data["firmware_version"] == "2.0.0"
        # Calibration preserved on re-register
        assert data["tare_offset"] == 12345
        assert data["calibration_factor"] == pytest.approx(0.0042)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_devices_empty(self, async_client: AsyncClient):
        resp = await async_client.get(f"{API}/devices")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_devices(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-a", hostname="alpha")
        await device_factory(device_id="sb-b", hostname="beta")

        resp = await async_client.get(f"{API}/devices")
        assert resp.status_code == 200
        devices = resp.json()
        assert len(devices) == 2
        hostnames = {d["hostname"] for d in devices}
        assert hostnames == {"alpha", "beta"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_updates_status(self, async_client: AsyncClient, device_factory):
        device = await device_factory(device_id="sb-hb")

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/sb-hb/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 600},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["tare_offset"] == device.tare_offset
        assert data["calibration_factor"] == pytest.approx(device.calibration_factor)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_returns_pending_command(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-cmd", pending_command="tare")

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/sb-cmd/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )

        assert resp.status_code == 200
        assert resp.json()["pending_command"] == "tare"

        # Second heartbeat should have no pending command (cleared)
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp2 = await async_client.post(
                f"{API}/devices/sb-cmd/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 20},
            )

        assert resp2.json()["pending_command"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_unknown_device_404(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/nonexistent/heartbeat",
                json={"nfc_ok": False, "scale_ok": False, "uptime_s": 0},
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_broadcasts_online_when_was_offline(self, async_client: AsyncClient, device_factory):
        # Create device with last_seen far in the past (offline)
        await device_factory(
            device_id="sb-offline",
            last_seen=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/sb-offline/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 5},
            )

        assert resp.status_code == 200
        # Should broadcast online since device was offline
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_online"
        assert msg["device_id"] == "sb-offline"


# ============================================================================
# NFC endpoints
# ============================================================================


class TestNfcEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_scanned_matched(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory(tag_uid="AABB1122", material="PLA")
        mock_spool = MagicMock()
        mock_spool.id = spool.id
        mock_spool.material = spool.material
        mock_spool.subtype = spool.subtype
        mock_spool.color_name = spool.color_name
        mock_spool.rgba = spool.rgba
        mock_spool.brand = spool.brand
        mock_spool.label_weight = spool.label_weight
        mock_spool.core_weight = spool.core_weight
        mock_spool.weight_used = spool.weight_used

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch("backend.app.api.routes.spoolbuddy.get_spool_by_tag", new_callable=AsyncMock) as mock_lookup,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_lookup.return_value = mock_spool

            resp = await async_client.post(
                f"{API}/nfc/tag-scanned",
                json={"device_id": "sb-1", "tag_uid": "AABB1122"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is True
        assert data["spool_id"] == spool.id
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_matched"
        assert msg["spool"]["id"] == spool.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_scanned_unmatched(self, async_client: AsyncClient):
        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch("backend.app.api.routes.spoolbuddy.get_spool_by_tag", new_callable=AsyncMock) as mock_lookup,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_lookup.return_value = None

            resp = await async_client.post(
                f"{API}/nfc/tag-scanned",
                json={"device_id": "sb-1", "tag_uid": "DEADBEEF"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is False
        assert data["spool_id"] is None
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_unknown_tag"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_removed(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/tag-removed",
                json={"device_id": "sb-1", "tag_uid": "AABB1122"},
            )

        assert resp.status_code == 200
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_removed"
        assert msg["device_id"] == "sb-1"
        assert msg["tag_uid"] == "AABB1122"


# ============================================================================
# NFC write-tag endpoints
# ============================================================================


class TestWriteTagEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_tag_queues_command(self, async_client: AsyncClient, device_factory, spool_factory):
        device = await device_factory(device_id="sb-wt")
        spool = await spool_factory(material="PLA", brand="Polymaker", color_name="Red", rgba="FF0000FF")

        resp = await async_client.post(
            f"{API}/nfc/write-tag",
            json={"device_id": device.device_id, "spool_id": spool.id},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

        # Verify heartbeat returns write_tag command with payload
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb = await async_client.post(
                f"{API}/devices/{device.device_id}/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )

        hb_data = hb.json()
        assert hb_data["pending_command"] == "write_tag"
        assert hb_data["pending_write_payload"] is not None
        assert hb_data["pending_write_payload"]["spool_id"] == spool.id
        assert "ndef_data_hex" in hb_data["pending_write_payload"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_tag_heartbeat_not_cleared(self, async_client: AsyncClient, device_factory, spool_factory):
        """write_tag command persists across heartbeats until write-result clears it."""
        device = await device_factory(device_id="sb-wt-persist")
        spool = await spool_factory(material="PETG")

        await async_client.post(
            f"{API}/nfc/write-tag",
            json={"device_id": device.device_id, "spool_id": spool.id},
        )

        # First heartbeat — command present
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb1 = await async_client.post(
                f"{API}/devices/{device.device_id}/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )
        assert hb1.json()["pending_command"] == "write_tag"

        # Second heartbeat — should still be present (not cleared like tare)
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb2 = await async_client.post(
                f"{API}/devices/{device.device_id}/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 20},
            )
        assert hb2.json()["pending_command"] == "write_tag"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_tag_missing_spool_404(self, async_client: AsyncClient, device_factory):
        device = await device_factory(device_id="sb-wt-nospool")

        resp = await async_client.post(
            f"{API}/nfc/write-tag",
            json={"device_id": device.device_id, "spool_id": 99999},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_tag_missing_device_404(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()

        resp = await async_client.post(
            f"{API}/nfc/write-tag",
            json={"device_id": "nonexistent", "spool_id": spool.id},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_result_success_links_tag(self, async_client: AsyncClient, device_factory, spool_factory):
        device = await device_factory(device_id="sb-wr", pending_command="write_tag")
        spool = await spool_factory(material="PLA", tag_uid=None)

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": device.device_id,
                    "spool_id": spool.id,
                    "tag_uid": "04AABB11223344",
                    "success": True,
                },
            )

        assert resp.status_code == 200
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_written"
        assert msg["spool_id"] == spool.id
        assert msg["tag_uid"] == "04AABB11223344"

        # Verify spool got tag linked
        spool_resp = await async_client.get(f"/api/v1/inventory/spools/{spool.id}")
        spool_data = spool_resp.json()
        assert spool_data["tag_uid"] == "04AABB11223344"
        assert spool_data["tag_type"] == "ntag"
        assert spool_data["data_origin"] == "opentag3d"
        assert spool_data["encode_time"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_result_failure_broadcasts_error(
        self, async_client: AsyncClient, device_factory, spool_factory
    ):
        device = await device_factory(device_id="sb-wr-fail", pending_command="write_tag")
        spool = await spool_factory(material="PLA", tag_uid=None)

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": device.device_id,
                    "spool_id": spool.id,
                    "tag_uid": "04AABB",
                    "success": False,
                    "message": "Write or verification failed",
                },
            )

        assert resp.status_code == 200
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_write_failed"
        assert msg["message"] == "Write or verification failed"

        # Verify spool NOT linked
        spool_resp = await async_client.get(f"/api/v1/inventory/spools/{spool.id}")
        assert spool_resp.json()["tag_uid"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_result_clears_pending_command(self, async_client: AsyncClient, device_factory, spool_factory):
        device = await device_factory(
            device_id="sb-wr-clear",
            pending_command="write_tag",
            pending_write_payload='{"spool_id": 1, "ndef_data_hex": "E110120003"}',
        )
        spool = await spool_factory()

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": device.device_id,
                    "spool_id": spool.id,
                    "tag_uid": "AABB",
                    "success": True,
                },
            )

        # Heartbeat should have no pending command
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb = await async_client.post(
                f"{API}/devices/{device.device_id}/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 30},
            )
        assert hb.json()["pending_command"] is None
        assert hb.json()["pending_write_payload"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_write(self, async_client: AsyncClient, device_factory, spool_factory):
        device = await device_factory(device_id="sb-cancel")
        spool = await spool_factory()

        # Queue a write
        await async_client.post(
            f"{API}/nfc/write-tag",
            json={"device_id": device.device_id, "spool_id": spool.id},
        )

        # Cancel it
        resp = await async_client.post(f"{API}/devices/{device.device_id}/cancel-write", json={})
        assert resp.status_code == 200

        # Heartbeat should have no pending command
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb = await async_client.post(
                f"{API}/devices/{device.device_id}/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )
        assert hb.json()["pending_command"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_write_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.post(f"{API}/devices/ghost/cancel-write", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_write_tag_ndef_data_is_valid(self, async_client: AsyncClient, device_factory, spool_factory):
        """Verify the NDEF data in the heartbeat is a valid OpenTag3D message."""
        device = await device_factory(device_id="sb-wt-ndef")
        spool = await spool_factory(
            material="PLA",
            brand="Polymaker",
            color_name="White",
            rgba="FFFFFFFF",
            label_weight=1000,
        )

        await async_client.post(
            f"{API}/nfc/write-tag",
            json={"device_id": device.device_id, "spool_id": spool.id},
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb = await async_client.post(
                f"{API}/devices/{device.device_id}/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )

        payload = hb.json()["pending_write_payload"]
        ndef_bytes = bytes.fromhex(payload["ndef_data_hex"])

        # CC bytes
        assert ndef_bytes[:4] == bytes([0xE1, 0x10, 0x12, 0x00])
        # TLV type
        assert ndef_bytes[4] == 0x03
        # NDEF record: TNF=MIME, type=application/opentag3d
        assert ndef_bytes[6] == 0xD2
        assert ndef_bytes[9:30] == b"application/opentag3d"
        # Terminator
        assert ndef_bytes[-1] == 0xFE
        # Total size fits NTAG213
        assert len(ndef_bytes) <= 144


# ============================================================================
# Scale endpoints
# ============================================================================


class TestScaleEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scale_reading_broadcast(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/scale/reading",
                json={
                    "device_id": "sb-1",
                    "weight_grams": 823.5,
                    "stable": True,
                    "raw_adc": 456789,
                },
            )

        assert resp.status_code == 200
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_weight"
        assert msg["device_id"] == "sb-1"
        assert msg["weight_grams"] == 823.5
        assert msg["stable"] is True
        assert msg["raw_adc"] == 456789

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_weight_calculates_correctly(self, async_client: AsyncClient, spool_factory):
        # label=1000g, core=250g, scale reads 750g
        # net_filament = max(0, 750 - 250) = 500
        # weight_used = max(0, 1000 - 500) = 500
        spool = await spool_factory(label_weight=1000, core_weight=250, weight_used=0)

        resp = await async_client.post(
            f"{API}/scale/update-spool-weight",
            json={"spool_id": spool.id, "weight_grams": 750},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["weight_used"] == 500

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_weight_full_spool(self, async_client: AsyncClient, spool_factory):
        # label=1000g, core=250g, scale reads 1250g (full spool)
        # net_filament = max(0, 1250 - 250) = 1000
        # weight_used = max(0, 1000 - 1000) = 0
        spool = await spool_factory(label_weight=1000, core_weight=250, weight_used=200)

        resp = await async_client.post(
            f"{API}/scale/update-spool-weight",
            json={"spool_id": spool.id, "weight_grams": 1250},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["weight_used"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_weight_stores_scale_reading(self, async_client: AsyncClient, spool_factory):
        """Verify last_scale_weight and last_weighed_at are stored after weight sync."""
        spool = await spool_factory(label_weight=1000, core_weight=250, weight_used=0)

        resp = await async_client.post(
            f"{API}/scale/update-spool-weight",
            json={"spool_id": spool.id, "weight_grams": 750},
        )
        assert resp.status_code == 200

        # Fetch the spool via inventory API to verify stored fields
        spool_resp = await async_client.get(f"/api/v1/inventory/spools/{spool.id}")
        assert spool_resp.status_code == 200
        spool_data = spool_resp.json()
        assert spool_data["last_scale_weight"] == 750
        assert spool_data["last_weighed_at"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_weight_missing_spool_404(self, async_client: AsyncClient):
        resp = await async_client.post(
            f"{API}/scale/update-spool-weight",
            json={"spool_id": 99999, "weight_grams": 500},
        )
        assert resp.status_code == 404


# ============================================================================
# Calibration endpoints
# ============================================================================


class TestCalibrationEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tare_queues_command(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-tare")

        resp = await async_client.post(f"{API}/devices/sb-tare/calibration/tare", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify pending_command via heartbeat
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb = await async_client.post(
                f"{API}/devices/sb-tare/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 1},
            )
        assert hb.json()["pending_command"] == "tare"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tare_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.post(f"{API}/devices/ghost/calibration/tare", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_set_tare_offset(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-st", calibration_factor=0.005)

        resp = await async_client.post(
            f"{API}/devices/sb-st/calibration/set-tare",
            json={"tare_offset": 54321},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["tare_offset"] == 54321
        assert data["calibration_factor"] == pytest.approx(0.005)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_set_calibration_factor(self, async_client: AsyncClient, device_factory):
        # known_weight=200g, raw_adc=50000, tare=10000 → factor=200/(50000-10000)=0.005
        await device_factory(device_id="sb-cf", tare_offset=10000)

        resp = await async_client.post(
            f"{API}/devices/sb-cf/calibration/set-factor",
            json={"known_weight_grams": 200, "raw_adc": 50000},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["calibration_factor"] == pytest.approx(0.005)
        assert data["tare_offset"] == 10000

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_set_calibration_factor_zero_delta_400(self, async_client: AsyncClient, device_factory):
        # raw_adc == tare_offset → delta is 0 → 400 error
        await device_factory(device_id="sb-zero", tare_offset=5000)

        resp = await async_client.post(
            f"{API}/devices/sb-zero/calibration/set-factor",
            json={"known_weight_grams": 100, "raw_adc": 5000},
        )

        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_calibration(self, async_client: AsyncClient, device_factory):
        await device_factory(
            device_id="sb-gcal",
            tare_offset=11111,
            calibration_factor=0.0042,
        )

        resp = await async_client.get(f"{API}/devices/sb-gcal/calibration")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tare_offset"] == 11111
        assert data["calibration_factor"] == pytest.approx(0.0042)


# ============================================================================
# Display endpoints
# ============================================================================


class TestDisplayEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_display_settings(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-disp", display_brightness=100, display_blank_timeout=0)

        resp = await async_client.put(
            f"{API}/devices/sb-disp/display",
            json={"brightness": 75, "blank_timeout": 300},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["brightness"] == 75
        assert data["blank_timeout"] == 300

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_display_persists_via_heartbeat(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-disp-hb")

        await async_client.put(
            f"{API}/devices/sb-disp-hb/display",
            json={"brightness": 50, "blank_timeout": 600},
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb = await async_client.post(
                f"{API}/devices/sb-disp-hb/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )

        assert hb.json()["display_brightness"] == 50
        assert hb.json()["display_blank_timeout"] == 600

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_display_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.put(
            f"{API}/devices/ghost/display",
            json={"brightness": 50, "blank_timeout": 60},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_display_validates_brightness(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-disp-val")

        resp = await async_client.put(
            f"{API}/devices/sb-disp-val/display",
            json={"brightness": 150, "blank_timeout": 0},
        )
        assert resp.status_code == 422  # Validation error: brightness > 100


# ============================================================================
# Update endpoints
# ============================================================================


class TestUpdateEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_update_starts_ssh_update(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-upd")

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch("backend.app.services.spoolbuddy_ssh.perform_ssh_update", new_callable=AsyncMock),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(f"{API}/devices/sb-upd/update")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_update_offline_device_409(self, async_client: AsyncClient, device_factory):
        await device_factory(
            device_id="sb-upd-off",
            last_seen=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        resp = await async_client.post(f"{API}/devices/sb-upd-off/update")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_update_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.post(f"{API}/devices/ghost/update")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_update_already_updating(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-upd-dup", update_status="updating")

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(f"{API}/devices/sb-upd-dup/update")

        assert resp.status_code == 200
        assert resp.json()["status"] == "already_updating"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_report_update_status_updating(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-upd-st", pending_command="update", update_status="pending")

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/sb-upd-st/update-status",
                json={"status": "updating", "message": "Fetching latest code..."},
            )

        assert resp.status_code == 200
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_update"
        assert msg["update_status"] == "updating"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_report_update_status_complete_clears_command(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-upd-done", pending_command="update", update_status="updating")

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            await async_client.post(
                f"{API}/devices/sb-upd-done/update-status",
                json={"status": "complete", "message": "Update complete, restarting..."},
            )

        # Heartbeat should have no pending command
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            hb = await async_client.post(
                f"{API}/devices/sb-upd-done/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )

        assert hb.json()["pending_command"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_report_update_status_error(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-upd-err", pending_command="update", update_status="updating")

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/sb-upd-err/update-status",
                json={"status": "error", "message": "git fetch failed: network unreachable"},
            )

        assert resp.status_code == 200
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["update_status"] == "error"
        assert "git fetch failed" in msg["update_message"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_report_update_status_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.post(
            f"{API}/devices/ghost/update-status",
            json={"status": "updating", "message": "test"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_device_response_includes_update_fields(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-upd-resp", update_status="complete", update_message="Done!")

        resp = await async_client.get(f"{API}/devices")
        assert resp.status_code == 200
        device = next(d for d in resp.json() if d["device_id"] == "sb-upd-resp")
        assert device["update_status"] == "complete"
        assert device["update_message"] == "Done!"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_check_returns_version_info(self, async_client: AsyncClient, device_factory):
        """GET /devices/{id}/update-check compares device version against APP_VERSION."""
        await device_factory(device_id="sb-uc", firmware_version="0.1.0")

        resp = await async_client.get(f"{API}/devices/sb-uc/update-check")

        assert resp.status_code == 200
        data = resp.json()
        assert data["current_version"] == "0.1.0"
        assert data["latest_version"] is not None
        assert data["update_available"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_check_up_to_date(self, async_client: AsyncClient, device_factory):
        from backend.app.core.config import APP_VERSION

        await device_factory(device_id="sb-uc2", firmware_version=APP_VERSION)

        resp = await async_client.get(f"{API}/devices/sb-uc2/update-check")

        assert resp.status_code == 200
        assert resp.json()["update_available"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_check_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.get(f"{API}/devices/ghost/update-check")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_update_broadcasts_websocket(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-upd-ws")

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch("backend.app.services.spoolbuddy_ssh.perform_ssh_update", new_callable=AsyncMock),
        ):
            mock_ws.broadcast = AsyncMock()
            await async_client.post(f"{API}/devices/sb-upd-ws/update")

        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_update"
        assert msg["device_id"] == "sb-upd-ws"
        assert msg["update_status"] == "pending"
