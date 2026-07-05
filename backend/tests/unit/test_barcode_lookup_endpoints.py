"""Unit tests for the scan-to-add barcode lookup endpoints.

Tests:
- GET /inventory/barcode/{barcode}: native-inventory hit, OFD hit, disabled
  setting, and unmatched cases
- POST /inventory/barcode/parse-label: heuristic parse with and without an
  embedded barcode
- Spoolman-mode awareness: when Spoolman is the active inventory backend,
  "the user's own inventory" means Spoolman's spools (via
  SpoolmanClient.find_spool_by_barcode), not the local Spool table.
"""

import itertools
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


def _make_settings_row(key: str, value: str):
    row = MagicMock()
    row.key = key
    row.value = value
    return row


def _db(spool=None, settings_rows=None):
    """Mock db.execute for the (settings, then local-Spool) query sequence.

    _resolve_barcode always loads settings first (_load_settings_map), then —
    only when Spoolman mode is off — queries the local Spool table. The first
    db.execute call gets the settings result; every call after that gets the
    spool result, so tests that never reach the local-table query (Spoolman
    mode, or no-barcode-in-text) don't need an exact call-count match.
    """
    db = AsyncMock()
    settings_result = MagicMock()
    settings_result.scalars.return_value.all.return_value = settings_rows or []
    spool_result = MagicMock()
    spool_result.scalars.return_value.first.return_value = spool
    results = itertools.chain([settings_result], itertools.repeat(spool_result))

    async def _execute(*_args, **_kwargs):
        return next(results)

    db.execute = _execute
    return db


class TestLookupBarcodeEndpoint:
    @pytest.mark.asyncio
    async def test_native_inventory_hit_takes_priority_over_ofd(self):
        spool = _make_mock_spool()
        db = _db(spool=spool)

        with patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup:
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        mock_ofd_lookup.assert_not_called()
        assert result.matched is True
        assert result.source == "inventory"
        assert result.material == "PLA"
        assert result.brand == "Sunlu"

    @pytest.mark.asyncio
    async def test_ofd_hit_when_no_native_match(self):
        db = _db(spool=None)
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 1000}

        with patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_fields)):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"
        assert result.material == "PETG"
        assert result.brand == "Overture"

    @pytest.mark.asyncio
    async def test_disabled_setting_skips_ofd_but_reports_disabled(self):
        db = _db(spool=None, settings_rows=[_make_settings_row("barcode_lookup_enabled", "false")])

        with patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup:
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        mock_ofd_lookup.assert_not_called()
        assert result.enabled is False
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_unmatched_barcode_returns_matched_false(self):
        db = _db(spool=None)

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="099999999999", db=db, _=None)

        assert result.matched is False
        assert result.source is None
        assert result.material is None

    @pytest.mark.asyncio
    async def test_barcode_is_canonicalized_in_response(self):
        db = _db(spool=None)

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="0012345678905", db=db, _=None)

        assert result.barcode == "12345678905"


class TestLookupBarcodeSpoolmanMode:
    """When Spoolman is the active inventory backend, resolution checks
    Spoolman's spools instead of the local Spool table (previously the local
    query always ran — and always missed — since Spoolman-mode users have no
    rows in the local Spool table at all)."""

    @pytest.mark.asyncio
    async def test_spoolman_hit_takes_priority_and_skips_local_table(self):
        db = _db(spool=None)  # local Spool table would have no rows for a Spoolman user
        spoolman_spool = {
            "id": 7,
            "filament": {
                "material": "PLA",
                "name": "PLA Basic",
                "vendor": {"name": "Bambu Lab"},
                "color_hex": "FF0000",
            },
            "extra": {},
        }
        mock_client = AsyncMock()
        mock_client.find_spool_by_barcode = AsyncMock(return_value=spoolman_spool)

        with (
            patch(
                "backend.app.api.routes.inventory._ensure_spoolman_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup,
        ):
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        mock_client.find_spool_by_barcode.assert_called_once_with("6938936716785")
        mock_ofd_lookup.assert_not_called()
        assert result.matched is True
        assert result.source == "inventory"
        assert result.material == "PLA"
        assert result.brand == "Bambu Lab"

    @pytest.mark.asyncio
    async def test_spoolman_miss_falls_back_to_ofd(self):
        db = _db(spool=None)
        mock_client = AsyncMock()
        mock_client.find_spool_by_barcode = AsyncMock(return_value=None)
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 1000}

        with (
            patch(
                "backend.app.api.routes.inventory._ensure_spoolman_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_fields)),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"
        assert result.material == "PETG"

    @pytest.mark.asyncio
    async def test_spoolman_lookup_error_falls_back_to_ofd(self):
        """A Spoolman connectivity failure degrades to OFD instead of erroring the request."""
        db = _db(spool=None)
        mock_client = AsyncMock()
        mock_client.find_spool_by_barcode = AsyncMock(side_effect=RuntimeError("unreachable"))
        ofd_fields = {"material": "ABS", "brand": "Generic"}

        with (
            patch(
                "backend.app.api.routes.inventory._ensure_spoolman_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_fields)),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"


class TestLookupBarcodeSpoolmanDbCommunityFallback:
    """SpoolmanDB-Community is consulted only after both the native-inventory
    check AND OFD miss — it has broader brand coverage than OFD but far
    sparser barcode coverage, so it stays a secondary fallback, not a
    replacement (see _resolve_barcode's docstring)."""

    @pytest.mark.asyncio
    async def test_spoolmandb_community_hit_when_ofd_misses(self):
        db = _db(spool=None)
        smdb_fields = {"material": "PLA", "brand": "Bambu Lab", "color_name": "Ivory White"}

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup",
                new=AsyncMock(return_value=smdb_fields),
            ),
        ):
            result = await lookup_barcode(barcode="6975337031345", db=db, _=None)

        assert result.matched is True
        assert result.source == "spoolmandb-community"
        assert result.material == "PLA"
        assert result.brand == "Bambu Lab"

    @pytest.mark.asyncio
    async def test_ofd_hit_takes_priority_over_spoolmandb_community(self):
        """Same barcode resolvable by both — OFD (purpose-built for barcodes) must win."""
        db = _db(spool=None)
        ofd_fields = {"material": "PETG", "brand": "Overture"}

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_fields)),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock()) as mock_smdb_lookup,
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        mock_smdb_lookup.assert_not_called()
        assert result.source == "ofd"
        assert result.material == "PETG"

    @pytest.mark.asyncio
    async def test_spoolmandb_community_lookup_error_degrades_to_unmatched(self):
        """A SpoolmanDB-Community connectivity failure must not error the request."""
        db = _db(spool=None)

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup",
                new=AsyncMock(side_effect=RuntimeError("unreachable")),
            ),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is False
        assert result.source is None

    @pytest.mark.asyncio
    async def test_disabled_setting_also_skips_spoolmandb_community(self):
        db = _db(spool=None, settings_rows=[_make_settings_row("barcode_lookup_enabled", "false")])

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup,
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock()) as mock_smdb_lookup,
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        mock_ofd_lookup.assert_not_called()
        mock_smdb_lookup.assert_not_called()
        assert result.enabled is False
        assert result.matched is False


class TestParseLabelEndpoint:
    @pytest.mark.asyncio
    async def test_parses_text_without_barcode(self):
        db = _db(spool=None)

        with patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])):
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
        db = _db(spool=None)
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 500}

        with (
            patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])),
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

    @pytest.mark.asyncio
    async def test_embedded_barcode_resolved_via_spoolmandb_community_when_ofd_misses(self):
        """A SpoolmanDB-Community-only hit must still report matched=True — regression
        guard for the `matched = source in (...)` check needing the new source value."""
        db = _db(spool=None)
        smdb_fields = {"material": "PLA", "brand": "Bambu Lab", "label_weight": 1000}

        with (
            patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup",
                new=AsyncMock(return_value=smdb_fields),
            ),
        ):
            result = await parse_label(
                payload=LabelParseRequest(text="Generic PLA Ivory EAN: 6975337031345"),
                db=db,
                _=None,
            )

        assert result.material == "PLA"
        assert result.brand == "Bambu Lab"
        assert result.source == "spoolmandb-community"
        assert result.matched is True
