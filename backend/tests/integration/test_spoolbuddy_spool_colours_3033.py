"""The kiosk paints a scanned spool with its extra colours and effect (#3033).

The tag-matched broadcast is the only place the kiosk learns about a scanned
spool, and it carried `rgba` alone, so a dual-colour or marble spool arrived as
one flat colour however the frontend drew it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings

SPOOLBUDDY_API = "/api/v1/spoolbuddy"


@pytest.fixture
async def spoolman_enabled(db_session: AsyncSession):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://spoolman.local:7912"))
    await db_session.commit()


@pytest.fixture
async def spoolman_disabled(db_session: AsyncSession):
    db_session.add(Settings(key="spoolman_enabled", value="false"))
    await db_session.commit()


async def _scan(async_client: AsyncClient) -> dict:
    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()
        resp = await async_client.post(
            f"{SPOOLBUDDY_API}/nfc/tag-scanned",
            json={"device_id": "sb-test", "tag_uid": "AABB1122334455FF", "tray_uuid": None},
        )
    assert resp.status_code == 200
    mock_ws.broadcast.assert_called_once()
    return mock_ws.broadcast.call_args[0][0]


class TestTheScanBroadcastCarriesTheSpoolsColours:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_local_dual_colour_spool(self, async_client: AsyncClient, spoolman_disabled):
        """The reporter's spool #23, Inland PLA Silk Blue-Yellow."""
        spool = MagicMock()
        spool.id = 23
        spool.material = "PLA"
        spool.subtype = "Silk"
        spool.color_name = "Blue-Yellow"
        spool.rgba = "044482FF"
        spool.extra_colors = "044482,f8d008"
        spool.effect_type = "dual-color"
        spool.brand = "Inland"
        spool.label_weight = 1000
        spool.core_weight = 250
        spool.weight_used = 0

        with patch("backend.app.api.routes.spoolbuddy.get_spool_by_tag", new_callable=AsyncMock, return_value=spool):
            msg = await _scan(async_client)

        assert msg["type"] == "spoolbuddy_tag_matched"
        assert msg["spool"]["extra_colors"] == "044482,f8d008"
        assert msg["spool"]["effect_type"] == "dual-color"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_spoolman_spool_sends_its_stops_and_no_effect(self, async_client: AsyncClient, spoolman_enabled):
        """Spoolman keeps the stops in `multi_color_hexes` and has no effect
        field, so the effect stays None rather than being guessed."""
        sm_spool = {
            "id": 23,
            "filament": {
                "material": "PLA",
                "name": "PLA Silk",
                "color_hex": "044482",
                "multi_color_hexes": "044482,F8D008",
                "weight": 1000.0,
                "vendor": {"name": "Inland"},
            },
            "used_weight": 0.0,
            "archived": False,
            "registered": "2024-01-01T00:00:00Z",
        }
        client = MagicMock()
        client.base_url = "http://spoolman.local:7912"
        client.get_spools = AsyncMock(return_value=[sm_spool])
        client.find_spool_by_tag = AsyncMock(return_value=sm_spool)
        client.merge_spool_extra = AsyncMock(return_value={})
        with (
            patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
            patch("backend.app.services.spoolman.init_spoolman_client", AsyncMock(return_value=client)),
        ):
            msg = await _scan(async_client)

        assert msg["type"] == "spoolbuddy_tag_matched"
        assert msg["spool"]["extra_colors"] == "044482,F8D008"
        assert msg["spool"]["effect_type"] is None
