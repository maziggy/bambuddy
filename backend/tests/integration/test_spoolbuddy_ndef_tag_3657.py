"""An unknown NTAG scanned on SpoolBuddy carries what its NDEF message says about the roll (#3657).

The decoded prefill goes out on the ``spoolbuddy_unknown_tag`` broadcast, for
the kiosk's Quick Add, and back in the HTTP response, for clients with their
own screen.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool

API = "/api/v1/spoolbuddy"
NTAG_UID = "04E115D15F6180"

# OpenSpool README example as NFC Tools on iOS wrote it (see the unit tests)
OPENSPOOL_HEX = (
    "0391d4107e6170706c69636174696f6e2f6a736f6e7b2270726f746f636f6c223a226f70656e73706f6f6c222c227665"
    "7273696f6e223a22312e30222c2274797065223a22504c41222c22636f6c6f725f686578223a22464641414242222c22"
    "6272616e64223a2247656e65726963222c226d696e5f74656d70223a22323230222c226d61785f74656d70223a223234"
    "30227dfe"
)
OPENSPOOL_PREFILL = {
    "material": "PLA",
    "subtype": None,
    "color_name": None,
    "rgba": "FFAABBFF",
    "extra_colors": None,
    "effect_type": None,
    "brand": "Generic",
    "label_weight": None,
    "slicer_filament": None,
    "slicer_filament_name": None,
    "nozzle_temp_min": 220,
    "nozzle_temp_max": 240,
    "core_weight": None,
    "core_weight_catalog_id": None,
    "variant_id": None,
    "diameter": None,
    "drying_temp": None,
    "drying_time": None,
}


async def _scan(async_client: AsyncClient, **body) -> tuple[dict, dict]:
    """The HTTP response and the websocket broadcast of one scan."""
    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()
        resp = await async_client.post(
            f"{API}/nfc/tag-scanned",
            json={"device_id": "sb-1", "tag_uid": NTAG_UID, "sak": 0, "tag_type": "ntag", **body},
        )
    assert resp.status_code == 200, resp.text
    return resp.json(), mock_ws.broadcast.call_args[0][0]


class TestNdefPrefill:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_openspool_tag_prefills_quick_add(self, async_client: AsyncClient):
        body, msg = await _scan(async_client, ndef_hex=OPENSPOOL_HEX)

        assert msg["type"] == "spoolbuddy_unknown_tag"
        assert msg["tag_format"] == "openspool"
        assert msg["tag_data"] == OPENSPOOL_PREFILL
        assert body == {
            "status": "ok",
            "matched": False,
            "spool_id": None,
            "tag_data": OPENSPOOL_PREFILL,
            "tag_format": "openspool",
        }

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_blank_tag(self, async_client: AsyncClient):
        body, msg = await _scan(async_client, ndef_hex="0300fe")
        assert msg["tag_format"] == body["tag_format"] == "blank"
        assert msg["tag_data"] is None
        assert body["tag_data"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unrecognised_tag_is_logged_and_scanned_as_today(self, async_client: AsyncClient, caplog):
        # A URI record only
        uri = "030fd1010b5504" + b"example.com".hex() + "fe"
        with caplog.at_level("INFO"):
            body, msg = await _scan(async_client, ndef_hex=uri)
        assert msg["type"] == "spoolbuddy_unknown_tag"
        assert msg["tag_format"] is None
        assert msg["tag_data"] is None
        assert body["matched"] is False
        assert f"NDEF on {NTAG_UID}: no known filament record" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_ndef_hex_keeps_todays_scan(self, async_client: AsyncClient):
        """An older client sends no NDEF data; the response only gains two empty keys."""
        body, msg = await _scan(async_client)
        assert msg["tag_data"] is None
        assert msg["tag_format"] is None
        assert body == {"status": "ok", "matched": False, "spool_id": None, "tag_data": None, "tag_format": None}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_known_tag_is_matched_not_decoded(self, async_client: AsyncClient, db_session: AsyncSession):
        spool = Spool(material="PETG", label_weight=1000, core_weight=250, weight_used=0, tag_uid=NTAG_UID)
        db_session.add(spool)
        await db_session.commit()

        with patch("backend.app.api.routes.spoolbuddy.decode_ndef_tag") as decode:
            body, msg = await _scan(async_client, ndef_hex=OPENSPOOL_HEX)

        decode.assert_not_called()
        assert msg["type"] == "spoolbuddy_tag_matched"
        assert body == {"status": "ok", "matched": True, "spool_id": spool.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_colour_name_comes_from_the_catalogue(self, async_client: AsyncClient, db_session: AsyncSession):
        """OpenSpool has no colour name; a catalogue row for that brand and hex gives one."""
        db_session.add(
            ColorCatalogEntry(manufacturer="Generic", color_name="Pastel Pink", hex_color="#FFAABB", material="PLA")
        )
        await db_session.commit()

        body, msg = await _scan(async_client, ndef_hex=OPENSPOOL_HEX)

        assert msg["tag_data"] == {**OPENSPOOL_PREFILL, "color_name": "Pastel Pink"}
        assert body["tag_data"] == msg["tag_data"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_catalogue_failure_does_not_fail_the_scan(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.spoolbuddy.resolve_brand_color", side_effect=RuntimeError("boom")):
            body, msg = await _scan(async_client, ndef_hex=OPENSPOOL_HEX)
        assert msg["tag_data"] == OPENSPOOL_PREFILL
        assert body["matched"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_decoder_failure_does_not_fail_the_scan(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.spoolbuddy.decode_ndef_tag", side_effect=RuntimeError("boom")):
            body, msg = await _scan(async_client, ndef_hex=OPENSPOOL_HEX)
        assert msg["type"] == "spoolbuddy_unknown_tag"
        assert msg["tag_data"] is None
        assert body["matched"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_mode_unknown_tag_is_decoded(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="http://spoolman.local:7912"))
        await db_session.commit()
        client = MagicMock()
        client.has_tag_api = AsyncMock(return_value=False)
        client.base_url = "http://spoolman.local:7912"
        client.get_spools = AsyncMock(return_value=[])
        client.find_spool_by_tag = AsyncMock(return_value=None)

        with (
            patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
            patch("backend.app.services.spoolman.init_spoolman_client", AsyncMock(return_value=client)),
        ):
            body, msg = await _scan(async_client, ndef_hex=OPENSPOOL_HEX)

        assert msg["tag_format"] == body["tag_format"] == "openspool"
        assert body["tag_data"] == OPENSPOOL_PREFILL

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("ndef_hex", ["03f", "zz00", "00" * 889])
    async def test_malformed_ndef_hex_is_rejected(self, async_client: AsyncClient, ndef_hex: str):
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                f"{API}/nfc/tag-scanned",
                json={"device_id": "sb-1", "tag_uid": NTAG_UID, "ndef_hex": ndef_hex},
            )
        assert resp.status_code == 422
        mock_ws.broadcast.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bambu_blocks_win_over_ndef_data(self, async_client: AsyncClient):
        """A client sends one or the other; if both come, the Bambu blocks are read and the NDEF data is not."""
        with patch("backend.app.api.routes.spoolbuddy.decode_ndef_tag") as decode:
            body, msg = await _scan(async_client, ndef_hex=OPENSPOOL_HEX, raw_blocks={"1": "00" * 16})
        decode.assert_not_called()
        # The blocks are incomplete, so there is no prefill from either
        assert msg["tag_data"] is None
        assert body["tag_format"] is None
