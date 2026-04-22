"""Integration tests for SpoolBuddy API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import backend.app.services.spoolbuddy_ssh  # noqa: F401 — ensures patch() can resolve the dotted path
from backend.app.api.routes import spoolbuddy as spoolbuddy_routes
from backend.app.models.spool import Spool
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice
from backend.app.services.spoolman import SpoolmanNotFoundError, SpoolmanUnavailableError

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
    async def test_unregister_device(self, async_client: AsyncClient, device_factory, db_session):
        await device_factory(device_id="sb-keep", hostname="keep")
        await device_factory(device_id="sb-drop", hostname="drop")
        spoolbuddy_routes._spoolbuddy_online_last_broadcast["sb-drop"] = 123.0

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.delete(f"{API}/devices/sb-drop")

        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted", "device_id": "sb-drop"}
        assert "sb-drop" not in spoolbuddy_routes._spoolbuddy_online_last_broadcast
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_unregistered"
        assert msg["device_id"] == "sb-drop"

        # Other device still present
        resp = await async_client.get(f"{API}/devices")
        remaining = {d["device_id"] for d in resp.json()}
        assert remaining == {"sb-keep"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unregister_device_not_found(self, async_client: AsyncClient):
        resp = await async_client.delete(f"{API}/devices/sb-ghost")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_updates_status(self, async_client: AsyncClient, device_factory):
        device = await device_factory(device_id="sb-hb")
        spoolbuddy_routes._spoolbuddy_online_last_broadcast.clear()

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
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_online"
        assert msg["device_id"] == "sb-hb"

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
        spoolbuddy_routes._spoolbuddy_online_last_broadcast.clear()
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

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_broadcasts_online_when_already_online(self, async_client: AsyncClient, device_factory):
        spoolbuddy_routes._spoolbuddy_online_last_broadcast.clear()
        await device_factory(
            device_id="sb-already-online",
            last_seen=datetime.now(timezone.utc),
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/devices/sb-already-online/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 42},
            )

        assert resp.status_code == 200
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_online"
        assert msg["device_id"] == "sb-already-online"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_online_broadcast_is_throttled(self, async_client: AsyncClient, device_factory):
        spoolbuddy_routes._spoolbuddy_online_last_broadcast.clear()
        await device_factory(
            device_id="sb-throttle",
            last_seen=datetime.now(timezone.utc),
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp1 = await async_client.post(
                f"{API}/devices/sb-throttle/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 10},
            )
            resp2 = await async_client.post(
                f"{API}/devices/sb-throttle/heartbeat",
                json={"nfc_ok": True, "scale_ok": True, "uptime_s": 11},
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_online"
        assert msg["device_id"] == "sb-throttle"


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

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_display_settings(self, async_client: AsyncClient, device_factory):
        """The kiosk idle watchdog (install/spoolbuddy-idle.sh) reads this
        endpoint on autostart to configure swayidle with the user-selected
        blank timeout before launching. See issue #937."""
        await device_factory(device_id="sb-disp-get", display_brightness=60, display_blank_timeout=450)

        resp = await async_client.get(f"{API}/devices/sb-disp-get/display")

        assert resp.status_code == 200
        data = resp.json()
        assert data["brightness"] == 60
        assert data["blank_timeout"] == 450

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_display_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.get(f"{API}/devices/ghost/display")
        assert resp.status_code == 404


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


# ============================================================================
# System command endpoints
# ============================================================================


class TestSystemCommandEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_reboot(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-reboot")

        resp = await async_client.post(
            f"{API}/devices/sb-reboot/system/command",
            json={"command": "reboot"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["command"] == "reboot"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_shutdown(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-shutdown")

        resp = await async_client.post(
            f"{API}/devices/sb-shutdown/system/command",
            json={"command": "shutdown"},
        )
        assert resp.status_code == 200
        assert resp.json()["command"] == "shutdown"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_restart_daemon(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-rd")

        resp = await async_client.post(
            f"{API}/devices/sb-rd/system/command",
            json={"command": "restart_daemon"},
        )
        assert resp.status_code == 200
        assert resp.json()["command"] == "restart_daemon"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_restart_browser(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-rb")

        resp = await async_client.post(
            f"{API}/devices/sb-rb/system/command",
            json={"command": "restart_browser"},
        )
        assert resp.status_code == 200
        assert resp.json()["command"] == "restart_browser"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_command_rejected(self, async_client: AsyncClient, device_factory):
        await device_factory(device_id="sb-invalid")

        resp = await async_client.post(
            f"{API}/devices/sb-invalid/system/command",
            json={"command": "format_disk"},
        )
        assert resp.status_code == 400
        assert "Invalid command" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_command_unknown_device_404(self, async_client: AsyncClient):
        resp = await async_client.post(
            f"{API}/devices/ghost/system/command",
            json={"command": "reboot"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_command_offline_device_409(self, async_client: AsyncClient, device_factory):
        await device_factory(
            device_id="sb-offline-cmd",
            last_seen=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        resp = await async_client.post(
            f"{API}/devices/sb-offline-cmd/system/command",
            json={"command": "reboot"},
        )
        assert resp.status_code == 409
        assert "offline" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_command_sets_pending_command(self, async_client: AsyncClient, device_factory, db_session):
        device = await device_factory(device_id="sb-pending")

        await async_client.post(
            f"{API}/devices/sb-pending/system/command",
            json={"command": "restart_daemon"},
        )

        await db_session.refresh(device)
        assert device.pending_command == "restart_daemon"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heartbeat_clears_system_command(self, async_client: AsyncClient, device_factory):
        """System commands (reboot/shutdown/restart_*) are fire-and-forget — heartbeat clears them."""
        await device_factory(device_id="sb-hb-clear")

        # Queue a command
        await async_client.post(
            f"{API}/devices/sb-hb-clear/system/command",
            json={"command": "restart_browser"},
        )

        # Heartbeat should return the command and clear it
        resp = await async_client.post(
            f"{API}/devices/sb-hb-clear/heartbeat",
            json={"nfc_ok": True, "scale_ok": True, "uptime_s": 100},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending_command"] == "restart_browser"


# ============================================================================
# Spoolman-aware SpoolBuddy endpoints
# ============================================================================


@pytest.fixture
async def spoolman_settings(db_session: AsyncSession):
    """Create Spoolman settings in the database (enabled with URL)."""
    from backend.app.models.settings import Settings

    settings = [
        Settings(key="spoolman_enabled", value="true"),
        Settings(key="spoolman_url", value="http://spoolman.local:7912"),
    ]
    for s in settings:
        db_session.add(s)
    await db_session.commit()
    return settings


def _mock_spoolman_client(base_url: str = "http://spoolman.local:7912") -> MagicMock:
    client = MagicMock()
    client.base_url = base_url
    client.get_spools = AsyncMock(return_value=[])
    client.get_spool = AsyncMock(return_value={})
    client.find_spool_by_tag = AsyncMock(return_value=None)
    client.update_spool = AsyncMock(return_value=None)
    client.merge_spool_extra = AsyncMock(return_value={"id": 0})
    return client


def _spoolman_spool_fixture(spool_id: int, spool_weight: float = 196.0, filament_weight: float = 1000.0) -> dict:
    """Build a minimal Spoolman spool dict with realistic core weight from filament.spool_weight."""
    return {
        "id": spool_id,
        "filament": {"weight": filament_weight, "spool_weight": spool_weight},
        "used_weight": 0.0,
    }


class TestUpdateSpoolWeightSpoolman:
    """update-spool-weight routes to Spoolman when Spoolman mode is active."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_mode_uses_filament_spool_weight(self, async_client: AsyncClient, spoolman_settings):
        """core_weight comes from filament.spool_weight, not a hardcoded constant."""
        sm_spool = _spoolman_spool_fixture(42, spool_weight=196.0, filament_weight=1000.0)
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=sm_spool)
        mock_client.update_spool = AsyncMock(return_value=sm_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/scale/update-spool-weight",
                json={"spool_id": 42, "weight_grams": 750},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        # remaining = max(0, 750 - 196) = 554 → weight_used = 1000 - 554 = 446
        assert data["weight_used"] == pytest.approx(446.0)
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(554.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_mode_clamps_remaining_to_zero(self, async_client: AsyncClient, spoolman_settings):
        """Scale weight below core weight → remaining_weight = 0."""
        sm_spool = _spoolman_spool_fixture(7, spool_weight=196.0, filament_weight=1000.0)
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=sm_spool)
        mock_client.update_spool = AsyncMock(return_value=sm_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/scale/update-spool-weight",
                json={"spool_id": 7, "weight_grams": 100},
            )

        assert resp.status_code == 200
        mock_client.update_spool.assert_called_once_with(spool_id=7, remaining_weight=0.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_mode_404_when_spool_not_found(self, async_client: AsyncClient, spoolman_settings):
        """404 when Spoolman doesn't know the spool."""
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(side_effect=SpoolmanNotFoundError("Spool 9999 not found"))

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/scale/update-spool-weight",
                json={"spool_id": 9999, "weight_grams": 500},
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_mode_502_on_client_failure(self, async_client: AsyncClient, spoolman_settings):
        """502 is returned when Spoolman client update fails (returns None)."""
        sm_spool = _spoolman_spool_fixture(99)
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=sm_spool)
        mock_client.update_spool = AsyncMock(return_value=None)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/scale/update-spool-weight",
                json={"spool_id": 99, "weight_grams": 500},
            )

        assert resp.status_code == 502

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_local_mode_unchanged(self, async_client: AsyncClient, spool_factory):
        """When Spoolman is NOT enabled, local DB update still works."""
        spool = await spool_factory(label_weight=1000, core_weight=250, weight_used=0)

        resp = await async_client.post(
            f"{API}/scale/update-spool-weight",
            json={"spool_id": spool.id, "weight_grams": 750},
        )

        assert resp.status_code == 200
        assert resp.json()["weight_used"] == 500


class TestTagScannedSpoolmanFallback:
    """nfc/tag-scanned falls back to Spoolman when local DB has no match."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_fallback_on_local_miss(self, async_client: AsyncClient, spoolman_settings):
        raw_spool = {
            "id": 5,
            "filament": {
                "material": "PETG",
                "name": "PETG Basic",
                "color_hex": "00FF00",
                "weight": 1000,
                "vendor": {"name": "Polymaker"},
            },
            "used_weight": 100.0,
            "archived": False,
            "registered": "2024-01-01T00:00:00+00:00",
            "extra": {"tag": '"DEADBEEF12345678"'},
        }
        mock_client = _mock_spoolman_client()
        mock_client.get_spools = AsyncMock(return_value=[raw_spool])
        mock_client.find_spool_by_tag = AsyncMock(return_value=raw_spool)

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/tag-scanned",
                json={"device_id": "sb-1", "tag_uid": "DEADBEEF12345678"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is True
        assert data["spool_id"] == 5
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_matched"
        assert msg["spool"]["id"] == 5
        assert msg["spool"]["material"] == "PETG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_fallback_unknown_when_no_spoolman_match(self, async_client: AsyncClient, spoolman_settings):
        """Unknown tag broadcast when both local DB and Spoolman miss."""
        mock_client = _mock_spoolman_client()
        mock_client.get_spools = AsyncMock(return_value=[])
        mock_client.find_spool_by_tag = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/tag-scanned",
                json={"device_id": "sb-1", "tag_uid": "UNKNOWN0000000FF"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is False
        assert data["spool_id"] is None
        mock_ws.broadcast.assert_called_once()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_unknown_tag"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_local_match_skips_spoolman(self, async_client: AsyncClient, spool_factory):
        """When local DB matches, Spoolman is never queried."""
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
            patch(
                "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
                new_callable=AsyncMock,
                return_value=mock_spool,
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/tag-scanned",
                json={"device_id": "sb-1", "tag_uid": "AABB1122"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is True
        assert data["spool_id"] == spool.id


# ============================================================================
# NFC write-tag / write-result — Spoolman-aware
# ============================================================================


def _full_spoolman_spool(spool_id: int) -> dict:
    """Complete Spoolman spool dict sufficient for NDEF encoding."""
    return {
        "id": spool_id,
        "filament": {
            "material": "PLA",
            "name": "PLA Basic",
            "color_hex": "FF0000",
            "weight": 1000.0,
            "spool_weight": 196.0,
            "vendor": {"name": "Bambu Lab"},
        },
        "used_weight": 0.0,
        "archived": False,
        "registered": "2024-01-01T00:00:00Z",
    }


class TestNfcWriteTagSpoolman:
    """nfc/write-tag falls back to Spoolman when local DB has no matching spool."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_spool_queued_when_local_miss(
        self, async_client: AsyncClient, device_factory, spoolman_settings
    ):
        """write-tag encodes NDEF from Spoolman data when spool not in local DB."""
        await device_factory(device_id="sb-write-sm")
        sm_spool = _full_spoolman_spool(77)
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=sm_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/nfc/write-tag",
                json={"device_id": "sb-write-sm", "spool_id": 77},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        mock_client.get_spool.assert_called_once_with(77)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_data_origin_spoolman_stored_in_payload(
        self, async_client: AsyncClient, device_factory, db_session, spoolman_settings
    ):
        """Pending write payload records data_origin=spoolman for Spoolman spools."""
        import json as _json

        device = await device_factory(device_id="sb-origin")
        sm_spool = _full_spoolman_spool(88)
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=sm_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            await async_client.post(
                f"{API}/nfc/write-tag",
                json={"device_id": "sb-origin", "spool_id": 88},
            )

        await db_session.refresh(device)
        payload = _json.loads(device.pending_write_payload)
        assert payload["data_origin"] == "spoolman"
        assert payload["spool_id"] == 88
        assert "ndef_data_hex" in payload

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_404_when_neither_local_nor_spoolman(
        self, async_client: AsyncClient, device_factory, spoolman_settings
    ):
        """404 returned when spool is missing from both local DB and Spoolman."""
        await device_factory(device_id="sb-miss")
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(side_effect=SpoolmanNotFoundError("Spool 9999 not found"))

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/nfc/write-tag",
                json={"device_id": "sb-miss", "spool_id": 9999},
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_local_spool_used_when_present(self, async_client: AsyncClient, device_factory, spool_factory):
        """Local DB spool is encoded directly without contacting Spoolman."""
        await device_factory(device_id="sb-local-write")
        spool = await spool_factory(material="PETG")

        resp = await async_client.post(
            f"{API}/nfc/write-tag",
            json={"device_id": "sb-local-write", "spool_id": spool.id},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"


class TestNfcWriteResultSpoolman:
    """nfc/write-result updates Spoolman extra.tag on success for Spoolman spools."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_success_updates_spoolman_extra_tag(
        self, async_client: AsyncClient, device_factory, spoolman_settings
    ):
        """Successful write for a Spoolman spool calls merge_spool_extra with extra.tag."""
        import json as _json

        await device_factory(
            device_id="sb-wr-sm",
            pending_command="write_tag",
            pending_write_payload=_json.dumps({"spool_id": 55, "ndef_data_hex": "deadbeef", "data_origin": "spoolman"}),
        )
        mock_client = _mock_spoolman_client()
        mock_client.merge_spool_extra = AsyncMock(return_value={"id": 55})

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": "sb-wr-sm",
                    "spool_id": 55,
                    "tag_uid": "AABBCCDD11223344",
                    "success": True,
                },
            )

        assert resp.status_code == 200
        mock_client.merge_spool_extra.assert_called_once_with(55, {"tag": '"AABBCCDD11223344"'})
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_written"
        assert msg["tag_uid"] == "AABBCCDD11223344"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_failure_does_not_call_spoolman(self, async_client: AsyncClient, device_factory, spoolman_settings):
        """Failed write never calls Spoolman update."""
        import json as _json

        await device_factory(
            device_id="sb-wr-fail",
            pending_command="write_tag",
            pending_write_payload=_json.dumps({"spool_id": 66, "ndef_data_hex": "deadbeef", "data_origin": "spoolman"}),
        )
        mock_client = _mock_spoolman_client()

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": "sb-wr-fail",
                    "spool_id": 66,
                    "tag_uid": "AABBCCDD11223344",
                    "success": False,
                    "message": "write timeout",
                },
            )

        assert resp.status_code == 200
        mock_client.update_spool.assert_not_called()
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_write_failed"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_success_local_spool_writes_to_db(
        self, async_client: AsyncClient, device_factory, spool_factory, db_session
    ):
        """Successful write for a local spool still updates local DB tag_uid."""
        import json as _json

        spool = await spool_factory()
        await device_factory(
            device_id="sb-wr-local",
            pending_command="write_tag",
            pending_write_payload=_json.dumps(
                {"spool_id": spool.id, "ndef_data_hex": "deadbeef", "data_origin": "local"}
            ),
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": "sb-wr-local",
                    "spool_id": spool.id,
                    "tag_uid": "DEADBEEF12345678",
                    "success": True,
                },
            )

        assert resp.status_code == 200
        await db_session.refresh(spool)
        assert spool.tag_uid == "DEADBEEF12345678"
        assert spool.tag_type == "ntag"


# ============================================================================
# Security fix tests — write-tag ValueError + write-result exception safety
# ============================================================================


class TestNfcWriteTagSpoolmanSecurityFixes:
    """Regression tests for security fixes in nfc/write-tag Spoolman path."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_spoolman_spool_id_returns_502(
        self, async_client: AsyncClient, device_factory, spoolman_settings
    ):
        """Malformed Spoolman spool (invalid id=0) raises 502, not 404 — spool exists but is bad data."""
        await device_factory(device_id="sb-invalid-id")
        # Spoolman returns spool with id=0 (invalid — caught by _map_spoolman_spool guard)
        bad_spool = {**_full_spoolman_spool(1), "id": 0}
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=bad_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/nfc/write-tag",
                json={"device_id": "sb-invalid-id", "spool_id": 99},
            )

        # 502: spool exists in Spoolman but its data is malformed — not a "not found"
        assert resp.status_code == 502

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oversized_label_weight_does_not_crash(
        self, async_client: AsyncClient, device_factory, spoolman_settings
    ):
        """label_weight > 65535 from Spoolman must not crash with struct.error."""
        await device_factory(device_id="sb-overflow")
        big_weight_spool = {
            **_full_spoolman_spool(42),
            "filament": {**_full_spoolman_spool(42)["filament"], "weight": 70000},
        }
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=big_weight_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/nfc/write-tag",
                json={"device_id": "sb-overflow", "spool_id": 42},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"


class TestNfcWriteResultSpoolmanSecurityFixes:
    """Regression tests for transaction safety in nfc/write-result Spoolman path."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_client_exception_still_clears_device_state(
        self, async_client: AsyncClient, device_factory, db_session, spoolman_settings
    ):
        """If Spoolman client raises, device pending_command is still cleared in DB."""
        import json as _json

        device = await device_factory(
            device_id="sb-exc-safe",
            pending_command="write_tag",
            pending_write_payload=_json.dumps({"spool_id": 77, "ndef_data_hex": "deadbeef", "data_origin": "spoolman"}),
        )
        mock_client = _mock_spoolman_client()
        mock_client.merge_spool_extra = AsyncMock(side_effect=Exception("connection refused"))

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": "sb-exc-safe",
                    "spool_id": 77,
                    "tag_uid": "AABBCCDD11223344",
                    "success": True,
                },
            )

        # 502: tag written to NFC but Spoolman link failed (not best-effort — caller must retry)
        assert resp.status_code == 502
        # Device state must be cleared despite the exception (no spurious re-write)
        await db_session.refresh(device)
        assert device.pending_command is None
        assert device.pending_write_payload is None
        # Failure broadcast fires so the UI can show the error
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_link_failed"


class TestNfcWriteResultInputValidation:
    """Input validation and JSON safety for nfc/write-result."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_too_long_rejected(self, async_client: AsyncClient, device_factory):
        """tag_uid longer than 64 chars must be rejected with 422."""
        import json as _json

        await device_factory(
            device_id="sb-uid-long",
            pending_command="write_tag",
            pending_write_payload=_json.dumps({"spool_id": 1, "ndef_data_hex": "dead", "data_origin": "local"}),
        )

        resp = await async_client.post(
            f"{API}/nfc/write-result",
            json={
                "device_id": "sb-uid-long",
                "spool_id": 1,
                "tag_uid": "A" * 65,
                "success": True,
            },
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_malformed_pending_payload_falls_back_to_local(
        self, async_client: AsyncClient, device_factory, spool_factory, db_session
    ):
        """Corrupted pending_write_payload JSON falls back to local mode gracefully."""
        spool = await spool_factory()
        await device_factory(
            device_id="sb-corrupt-json",
            pending_command="write_tag",
            pending_write_payload="{not valid json!!!",
        )

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": "sb-corrupt-json",
                    "spool_id": spool.id,
                    "tag_uid": "DEADBEEF12345678",
                    "success": True,
                },
            )

        # Must return 200, not 500
        assert resp.status_code == 200
        # Falls back to local mode — tag written to DB
        await db_session.refresh(spool)
        assert spool.tag_uid == "DEADBEEF12345678"


# ============================================================================
# B1: NFC write-tag warnings appear in response body
# ============================================================================


class TestNfcWriteTagWarningsBody:
    """B1: resp.json()['warnings'] is populated when Spoolman fields are absent."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_warnings_returned_for_missing_color_and_temp(
        self, async_client: AsyncClient, device_factory, spoolman_settings
    ):
        """Both color_name=None and settings_extruder_temp=None produce 2 warnings."""
        await device_factory(device_id="sb-warn-b1")
        # Spoolman spool with no color_name or nozzle temp
        sparse_spool = {
            "id": 99,
            "filament": {
                "material": "PLA",
                "name": "PLA Basic",
                "color_hex": "808080",
                # color_name absent → None after mapping
                # settings_extruder_temp absent → nozzle_temp_min=None
                "weight": 1000.0,
                "vendor": {"name": "Bambu Lab"},
            },
            "used_weight": 0.0,
            "archived": False,
            "registered": "2024-01-01T00:00:00Z",
        }
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=sparse_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/nfc/write-tag",
                json={"device_id": "sb-warn-b1", "spool_id": 99},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "warnings" in body, "Response should contain 'warnings' key when fields are absent"
        warnings = body["warnings"]
        assert len(warnings) >= 2, f"Expected at least 2 warnings for missing color_name + nozzle_temp, got: {warnings}"
        # Confirm the specific fields are mentioned
        warn_text = " ".join(warnings)
        assert "color_name" in warn_text
        assert "nozzle_temp" in warn_text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_warnings_key_when_all_fields_present(
        self, async_client: AsyncClient, device_factory, spoolman_settings
    ):
        """No 'warnings' key in response when all fields are populated."""
        await device_factory(device_id="sb-nowarn")
        full_spool = _full_spoolman_spool(100)
        # Add color_name and extruder temp
        full_spool["filament"]["color_name"] = "Red"
        full_spool["filament"]["settings_extruder_temp"] = 220
        mock_client = _mock_spoolman_client()
        mock_client.get_spool = AsyncMock(return_value=full_spool)

        with (
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            resp = await async_client.post(
                f"{API}/nfc/write-tag",
                json={"device_id": "sb-nowarn", "spool_id": 100},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "warnings" not in body or body["warnings"] == []


# ============================================================================
# B5: Exception text scrubbed from WebSocket broadcast message
# ============================================================================


class TestNfcWriteResultExceptionScrubbing:
    """B5: Internal exception details must not appear in WebSocket 'message' field."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_exception_text_not_leaked_in_ws_message(
        self, async_client: AsyncClient, device_factory, db_session, spoolman_settings
    ):
        """When Spoolman merge raises, WS message is generic; 'connection refused' absent."""
        import json as _json

        await device_factory(
            device_id="sb-scrub-b5",
            pending_command="write_tag",
            pending_write_payload=_json.dumps({"spool_id": 77, "ndef_data_hex": "deadbeef", "data_origin": "spoolman"}),
        )
        mock_client = _mock_spoolman_client()
        mock_client.merge_spool_extra = AsyncMock(side_effect=Exception("connection refused to 192.168.1.1:7912"))

        with (
            patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws,
            patch(
                "backend.app.services.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.services.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/write-result",
                json={
                    "device_id": "sb-scrub-b5",
                    "spool_id": 77,
                    "tag_uid": "AABBCCDD11223344",
                    "success": True,
                },
            )

        assert resp.status_code == 502
        msg = mock_ws.broadcast.call_args[0][0]
        assert msg["type"] == "spoolbuddy_tag_link_failed"
        # Generic message — no internal exception details leaked
        assert msg["message"] == "Spoolman link failed", f"Expected generic message but got: {msg['message']!r}"
        assert "connection refused" not in str(msg), f"Exception text must not appear in WS message: {msg}"
        assert "192.168.1" not in str(msg), f"Internal IP must not appear in WS message: {msg}"
