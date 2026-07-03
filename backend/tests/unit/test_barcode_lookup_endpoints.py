"""Unit tests for the scan-to-add barcode lookup endpoints.

Tests:
- GET /inventory/barcode/{barcode}: native-inventory hit, OFD hit, disabled
  setting, and unmatched cases
- POST /inventory/barcode/parse-label: heuristic parse with and without an
  embedded barcode
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.inventory import LabelParseRequest, lookup_barcode, parse_label


def _make_mock_spool(**overrides):
    spool = MagicMock()
    defaults = {
        "material": "PLA",
        "brand": "Sunlu",
        "subtype": "Plus",
        "color_name": "Black",
        "rgba": "000000FF",
        "label_weight": 1000,
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(spool, key, value)
    return spool


def _db_with_spool_result(spool):
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = spool
    db.execute = AsyncMock(return_value=mock_result)
    return db


class TestLookupBarcodeEndpoint:
    @pytest.mark.asyncio
    async def test_native_inventory_hit_takes_priority_over_ofd(self):
        spool = _make_mock_spool()
        db = _db_with_spool_result(spool)

        with (
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value="true")),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup,
        ):
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        mock_ofd_lookup.assert_not_called()
        assert result.matched is True
        assert result.source == "inventory"
        assert result.material == "PLA"
        assert result.brand == "Sunlu"

    @pytest.mark.asyncio
    async def test_ofd_hit_when_no_native_match(self):
        db = _db_with_spool_result(None)
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 1000}

        with (
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value="true")),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_fields)),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"
        assert result.material == "PETG"
        assert result.brand == "Overture"

    @pytest.mark.asyncio
    async def test_disabled_setting_skips_ofd_but_reports_disabled(self):
        db = _db_with_spool_result(None)

        with (
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value="false")),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup,
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        mock_ofd_lookup.assert_not_called()
        assert result.enabled is False
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_unmatched_barcode_returns_matched_false(self):
        db = _db_with_spool_result(None)

        with (
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value="true")),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="099999999999", db=db, _=None)

        assert result.matched is False
        assert result.source is None
        assert result.material is None

    @pytest.mark.asyncio
    async def test_barcode_is_canonicalized_in_response(self):
        db = _db_with_spool_result(None)

        with (
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value="true")),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="0012345678905", db=db, _=None)

        assert result.barcode == "12345678905"


class TestParseLabelEndpoint:
    @pytest.mark.asyncio
    async def test_parses_text_without_barcode(self):
        db = AsyncMock()

        with (
            patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])),
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value="true")),
        ):
            result = await parse_label(
                payload=LabelParseRequest(text="SUNLU PLA+ Filament 1.75mm Black 1KG"),
                db=db,
                _=None,
            )

        assert result.material == "PLA"
        assert result.brand == "Sunlu"
        assert result.barcode is None
        assert result.source == "parsed"
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_embedded_barcode_resolved_and_overrides_guesses(self):
        db = _db_with_spool_result(None)
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 500}

        with (
            patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])),
            patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value="true")),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_fields)),
        ):
            result = await parse_label(
                payload=LabelParseRequest(text="Generic PLA Black EAN: 6938936716785"),
                db=db,
                _=None,
            )

        # OFD-resolved fields win over the "PLA"/"Black" text guesses.
        assert result.material == "PETG"
        assert result.brand == "Overture"
        assert result.source == "ofd"
        assert result.matched is True
        assert result.barcode == "6938936716785"
