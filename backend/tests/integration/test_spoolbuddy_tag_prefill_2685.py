"""An unknown Bambu tag scanned on SpoolBuddy carries what its blocks say about the roll (#2685).

The kiosk's Quick Add creates the spool from ``tag_data``, so it must hold the
same values AMS auto-creation would set for that roll.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.color_catalog import ColorCatalogEntry

API = "/api/v1/spoolbuddy"

# Read from a real Bambu PLA Basic Cyan spool (product 10603)
CYAN_PLA_BASIC = {
    "1": "4130302D423038004746413030000000",
    "2": "504C4100000000000000000000000000",
    "4": "504C4120426173696300000000000000",
    "5": "0086D6FFE80300000000E03F00000000",
    "6": "3700080000000000E600BE0000000000",
    "9": "36B6C12DB3DA4A39A50C275F510BD399",
}
TRAY_UUID = "36B6C12DB3DA4A39A50C275F510BD399"


async def _scan(async_client: AsyncClient, **body) -> dict:
    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()
        resp = await async_client.post(
            f"{API}/nfc/tag-scanned",
            json={"device_id": "sb-1", "tag_uid": "865947E2", "tray_uuid": TRAY_UUID, **body},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["matched"] is False
    return mock_ws.broadcast.call_args[0][0]


class TestUnknownTagPrefill:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_blocks_become_the_spool_ams_sync_would_create(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        db_session.add(
            ColorCatalogEntry(
                manufacturer="Bambu Lab", color_name="Cyan", hex_color="#0086D6", material="PLA Basic", is_default=True
            )
        )
        await db_session.commit()

        msg = await _scan(async_client, raw_blocks=CYAN_PLA_BASIC)

        assert msg["type"] == "spoolbuddy_unknown_tag"
        assert msg["tag_data"] == {
            "material": "PLA",
            "subtype": "Basic",
            "color_name": "Cyan",
            "rgba": "0086D6FF",
            "extra_colors": None,
            "effect_type": None,
            "brand": "Bambu Lab",
            "label_weight": 1000,
            "slicer_filament": "GFA00",
            "slicer_filament_name": "Bambu PLA Basic",
            "nozzle_temp_min": 190,
            "nozzle_temp_max": 230,
            "core_weight": 250,
            "core_weight_catalog_id": None,
            "variant_id": "A00-B08",
            "diameter": 1.75,
            "drying_temp": 55,
            "drying_time": 8,
        }

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_blocks_means_no_tag_data(self, async_client: AsyncClient):
        """A non-Bambu tag, or an older daemon, keeps today's Quick Add."""
        msg = await _scan(async_client)
        assert msg["type"] == "spoolbuddy_unknown_tag"
        assert msg["tag_data"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_undecodable_blocks_mean_no_tag_data(self, async_client: AsyncClient):
        """An incomplete read falls back to today's Quick Add instead of a half-filled spool."""
        incomplete = {k: v for k, v in CYAN_PLA_BASIC.items() if k != "2"}
        msg = await _scan(async_client, raw_blocks=incomplete)
        assert msg["tag_data"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_malformed_blocks_are_rejected(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/tag-scanned",
                json={"device_id": "sb-1", "tag_uid": "865947E2", "raw_blocks": {"1": "zz"}},
            )
        assert resp.status_code == 422
        mock_ws.broadcast.assert_not_called()
