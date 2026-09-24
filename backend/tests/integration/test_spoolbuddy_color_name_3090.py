"""The kiosk has to be told when a spool's colour name is only a stand-in (#3090).

SpoolBuddy showed "Unknown color" for spools Bambuddy names perfectly well. The
name is not in the spool record: Bambu's RFID tags frequently carry none, and
Spoolman has no colour-name field at all, so the frontend resolves the swatch's
hex against the colour catalog instead. The kiosk was reading the raw column.

That is a frontend fix, except for one thing the frontend cannot work out on
its own. In Spoolman mode ``_map_spoolman_spool`` puts the spool's *subtype*
in ``color_name`` when nothing is stored, so the kiosk receives "Silk+" — a
plausible-looking string that would beat the catalog if it were taken at face
value. ``color_name_is_synthesized`` is how the backend already marks that,
and these tests pin it onto the tag-matched broadcast, which is the one place
the kiosk learns about a scanned spool.
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


def _spoolman_spool_without_a_colour_name() -> dict:
    """A Silk+ roll as Spoolman holds it: a swatch, and nowhere to put a name."""
    return {
        "id": 38,
        "filament": {
            "material": "PLA",
            "name": "PLA Silk+",
            "color_hex": "D02727",  # Candy Red, in the colour catalog
            "weight": 1000.0,
            "spool_weight": 250.0,
            "vendor": {"name": "Bambu Lab"},
        },
        "used_weight": 0.0,
        "archived": False,
        "registered": "2024-01-01T00:00:00Z",
    }


def _mock_spoolman_client(spool: dict) -> MagicMock:
    client = MagicMock()
    client.base_url = "http://spoolman.local:7912"
    client.get_spools = AsyncMock(return_value=[spool])
    client.find_spool_by_tag = AsyncMock(return_value=spool)
    client.merge_spool_extra = AsyncMock(return_value={})
    return client


async def _scan(async_client: AsyncClient) -> dict:
    """Scan a tag and return the broadcast the kiosk would receive."""
    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()
        resp = await async_client.post(
            f"{SPOOLBUDDY_API}/nfc/tag-scanned",
            json={
                "device_id": "sb-test",
                "tag_uid": "AABB1122334455FF",
                "tray_uuid": "DEADBEEFDEADBEEFDEADBEEFDEADBEEF",
            },
        )
    assert resp.status_code == 200
    mock_ws.broadcast.assert_called_once()
    return mock_ws.broadcast.call_args[0][0]


class TestTheScanBroadcastSaysWhereTheNameCameFrom:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_spoolman_spool_is_marked_as_having_no_real_name(self, async_client: AsyncClient, spoolman_enabled):
        """Spoolman keeps no colour name, so what arrives is the subtype."""
        spool = _spoolman_spool_without_a_colour_name()
        client = _mock_spoolman_client(spool)
        with (
            patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
            patch("backend.app.services.spoolman.init_spoolman_client", AsyncMock(return_value=client)),
        ):
            msg = await _scan(async_client)

        assert msg["type"] == "spoolbuddy_tag_matched"
        # The stand-in is still sent — it is the only thing there, and a kiosk
        # that cannot resolve the hex should show something.
        assert msg["spool"]["color_name"] == "Silk+"
        assert msg["spool"]["color_name_is_synthesized"] is True, (
            'without this the kiosk shows "Silk+" where the catalog knows the colour'
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_local_spool_is_never_marked_synthesized(self, async_client: AsyncClient, spoolman_disabled):
        """Local inventory stores what the user or their tag set, or nothing.

        A name that is present is a real one, and an absent one must stay
        absent rather than acquiring a stand-in — the kiosk resolves the empty
        case from the swatch, and cannot do that for a name it is told to
        trust.
        """
        spool = MagicMock()
        spool.id = 38
        spool.material = "PLA"
        spool.subtype = "Silk+"
        spool.color_name = None
        spool.rgba = "D02727FF"
        spool.brand = "Bambu Lab"
        spool.label_weight = 1000
        spool.core_weight = 250
        spool.weight_used = 0

        with patch(
            "backend.app.api.routes.spoolbuddy.get_spool_by_tag",
            new_callable=AsyncMock,
            return_value=spool,
        ):
            msg = await _scan(async_client)

        assert msg["type"] == "spoolbuddy_tag_matched"
        assert msg["spool"]["color_name"] is None
        assert msg["spool"]["color_name_is_synthesized"] is False
