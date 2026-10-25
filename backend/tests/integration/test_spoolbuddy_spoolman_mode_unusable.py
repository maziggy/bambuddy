"""SpoolBuddy with Spoolman switched on but not usable.

With Spoolman enabled and a URL set, the spools are Spoolman's, even while
that URL is refused. A tag lookup, a tag write and a scale reading then report
Spoolman as unavailable instead of reading or writing the built-in inventory,
whose spool ids are not Spoolman's. Switched on without a URL is the built-in
inventory, as every SpoolBuddy screen shows it.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice

API = "/api/v1/spoolbuddy"


@pytest.fixture
async def unusable_spoolman(db_session):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    # A cloud metadata address, which the URL guard refuses.
    db_session.add(Settings(key="spoolman_url", value="http://169.254.169.254"))
    db_session.add(
        SpoolBuddyDevice(
            device_id="sb-1",
            hostname="spoolbuddy-1",
            ip_address="10.0.0.1",
            firmware_version="1.0.0",
            has_nfc=True,
            has_scale=True,
            tare_offset=0,
            calibration_factor=1.0,
            last_seen=datetime.now(timezone.utc),
        )
    )
    spool = Spool(
        material="PLA",
        label_weight=1000,
        core_weight=250,
        weight_used=100,
        tag_uid="AABB1122",
    )
    db_session.add(spool)
    await db_session.commit()
    return spool.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_scanned_tag_is_not_looked_up_in_the_built_in_inventory(async_client: AsyncClient, unusable_spoolman):
    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()
        resp = await async_client.post(f"{API}/nfc/tag-scanned", json={"device_id": "sb-1", "tag_uid": "AABB1122"})

    assert resp.status_code == 200
    assert resp.json()["matched"] is False
    sent = [call.args[0]["type"] for call in mock_ws.broadcast.call_args_list]
    assert "spoolman_unavailable" in sent
    assert "spoolbuddy_tag_matched" not in sent


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_tag_write_is_refused(async_client: AsyncClient, unusable_spoolman, db_session):
    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()
        resp = await async_client.post(
            f"{API}/nfc/write-tag", json={"device_id": "sb-1", "spool_id": unusable_spoolman}
        )

    assert resp.status_code == 503
    device = await db_session.scalar(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == "sb-1"))
    await db_session.refresh(device)
    assert device.pending_command is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_scale_reading_does_not_touch_the_built_in_spool(
    async_client: AsyncClient, unusable_spoolman, db_session
):
    resp = await async_client.post(
        f"{API}/scale/update-spool-weight", json={"spool_id": unusable_spoolman, "weight_grams": 600}
    )

    assert resp.status_code == 503
    spool = await db_session.get(Spool, unusable_spoolman)
    await db_session.refresh(spool)
    assert spool.weight_used == 100


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_tag_written_after_spoolman_was_switched_off_reports_why(async_client: AsyncClient, db_session):
    """The write began in Spoolman mode; by the time the tag is written
    Spoolman is off, so the link cannot be stored."""
    import json

    db_session.add(
        SpoolBuddyDevice(
            device_id="sb-2",
            hostname="spoolbuddy-2",
            ip_address="10.0.0.2",
            firmware_version="1.0.0",
            has_nfc=True,
            has_scale=True,
            tare_offset=0,
            calibration_factor=1.0,
            last_seen=datetime.now(timezone.utc),
            pending_command="write_tag",
            pending_write_payload=json.dumps({"spool_id": 9, "ndef_data_hex": "deadbeef", "data_origin": "spoolman"}),
        )
    )
    await db_session.commit()

    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()
        resp = await async_client.post(
            f"{API}/nfc/write-result",
            json={"device_id": "sb-2", "spool_id": 9, "tag_uid": "AABBCCDD", "success": True},
        )

    assert resp.status_code == 502
    msg = mock_ws.broadcast.call_args[0][0]
    assert (msg["type"], msg["reason"]) == ("spoolbuddy_tag_link_failed", "spoolman_not_configured")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_switched_on_without_a_url_is_the_built_in_inventory(async_client: AsyncClient, db_session):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value=""))
    spool = Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=100)
    db_session.add(spool)
    await db_session.commit()

    resp = await async_client.post(f"{API}/scale/update-spool-weight", json={"spool_id": spool.id, "weight_grams": 600})

    assert resp.status_code == 200
    await db_session.refresh(spool)
    assert spool.weight_used == 650
