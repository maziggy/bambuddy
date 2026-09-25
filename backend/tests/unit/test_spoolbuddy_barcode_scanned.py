"""Unit tests for the SpoolBuddy hardware barcode-scan endpoint.

POST /spoolbuddy/barcode/scanned resolves a scanned code through the shared
inventory barcode chain and broadcasts a `spoolbuddy_barcode_scanned` WS event
whose payload mirrors BarcodeLookupResponse field-for-field. These tests pin:
  - matched broadcast shape (fields + source + linked_codes)
  - unmatched broadcast (matched:false, source:null)
  - invalid code short-circuits resolution (valid:false, no _resolve_barcode)
  - a disabled device ignores the scan entirely (belt-and-braces vs daemon lag)
  - WS payload key parity with BarcodeLookupResponse
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.spoolbuddy import barcode_scanned
from backend.app.schemas.spool import BarcodeLookupResponse
from backend.app.schemas.spoolbuddy import BarcodeScannedRequest


def _device(enabled=True):
    d = MagicMock()
    d.barcode_enabled = enabled
    return d


def _db_returning(device):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = device
    db.execute = AsyncMock(return_value=result)
    return db


@contextmanager
def _patch_resolution(fields, source, all_codes):
    """Patch the lazily-imported plumbing + resolver used inside the endpoint."""
    with (
        patch.multiple(
            "backend.app.api.routes.inventory",
            _load_settings_map=AsyncMock(return_value={}),
            _ensure_spoolman_client=AsyncMock(return_value=None),
        ),
        patch(
            "backend.app.services.barcode_resolver.resolve_barcode",
            new=AsyncMock(return_value=(fields, source, all_codes)),
        ),
    ):
        yield


class TestBarcodeScanned:
    @pytest.mark.asyncio
    async def test_matched_broadcasts_full_fields(self):
        req = BarcodeScannedRequest(device_id="sb-1", barcode="06938936716785")
        db = _db_returning(_device())
        fields = {
            "material": "PLA",
            "brand": "Polymaker",
            "subtype": "PolyTerra Matte",
            "color_name": "Charcoal Black",
            "rgba": "3B3B3FFF",
            "label_weight": 1000,
            "nozzle_temp_min": 190,
            "nozzle_temp_max": 230,
        }
        all_codes = [
            {"code": "6938936716785", "kind": "gtin", "is_refill": False},
            {"code": "6938936716786", "kind": "gtin", "is_refill": True},
        ]

        with (
            _patch_resolution(fields, "ofd", all_codes),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            out = await barcode_scanned(req=req, db=db, _=None)

        assert out == {"status": "ok", "matched": True}
        payload = mock_bcast.call_args[0][0]
        assert payload["type"] == "spoolbuddy_barcode_scanned"
        assert payload["device_id"] == "sb-1"
        assert payload["valid"] is True
        assert payload["matched"] is True
        assert payload["source"] == "ofd"
        assert payload["material"] == "PLA"
        assert payload["color_name"] == "Charcoal Black"
        # The scanned (primary) code is excluded from linked_codes.
        assert {c["code"] for c in payload["linked_codes"]} == {"6938936716786"}

    @pytest.mark.asyncio
    async def test_unmatched_broadcasts_null_source(self):
        req = BarcodeScannedRequest(device_id="sb-1", barcode="06938936716785")
        db = _db_returning(_device())

        with (
            _patch_resolution({}, None, []),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            out = await barcode_scanned(req=req, db=db, _=None)

        assert out == {"status": "ok", "matched": False}
        payload = mock_bcast.call_args[0][0]
        assert payload["valid"] is True
        assert payload["matched"] is False
        assert payload["source"] is None
        assert payload["material"] is None

    @pytest.mark.asyncio
    async def test_url_payload_is_ignored_without_broadcast(self):
        # The Bambu spool QR decodes as a URL (colon-less through the HID
        # keymap) — never a product code, so no resolution and no modal.
        req = BarcodeScannedRequest(device_id="sb-1", barcode="HTTPS//E.BAMBULAB.COM/T?C=SMY5WWK0")
        db = _db_returning(_device())
        resolve = AsyncMock()

        with (
            patch("backend.app.services.barcode_resolver.resolve_barcode", new=resolve),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            out = await barcode_scanned(req=req, db=db, _=None)

        assert out == {"status": "ok", "matched": False, "ignored": True}
        resolve.assert_not_awaited()
        mock_bcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_qr_symbology_is_ignored_even_with_plain_payload(self):
        # AIM says the symbol was a QR code — drop regardless of content.
        req = BarcodeScannedRequest(device_id="sb-1", barcode="6938936716785", symbology="qr")
        db = _db_returning(_device())

        with (
            _patch_resolution({}, None, []),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            out = await barcode_scanned(req=req, db=db, _=None)

        assert out.get("ignored") is True
        mock_bcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_code128_symbology_demotes_lucky_checksum_numeric(self):
        # 06938936716785 passes the GTIN checksum, but the scanner read it
        # from a Code 128 symbol — an arbitrary wrapped numeric (lot number),
        # so the GTIN rung must be skipped and the kind fall down the ladder.
        req = BarcodeScannedRequest(device_id="sb-1", barcode="06938936716785", symbology="code128")
        db = _db_returning(_device())
        resolve = AsyncMock(return_value=({}, None, []))

        with (
            patch("backend.app.services.barcode_resolver.resolve_barcode", new=resolve),
            patch("backend.app.api.routes.inventory._load_settings_map", new=AsyncMock(return_value={})),
            patch("backend.app.api.routes.inventory._ensure_spoolman_client", new=AsyncMock(return_value=None)),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            await barcode_scanned(req=req, db=db, _=None)

        # Resolution ran with the demoted kind, and the WS payload reports it.
        assert resolve.await_args[0][2] == "sku"
        payload = mock_bcast.call_args[0][0]
        assert payload["kind"] == "sku"
        assert payload["symbology"] == "code128"

    @pytest.mark.asyncio
    async def test_ean_upc_symbology_keeps_gtin_kind(self):
        req = BarcodeScannedRequest(device_id="sb-1", barcode="06938936716785", symbology="ean-upc")
        db = _db_returning(_device())

        with (
            _patch_resolution({}, None, []),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            await barcode_scanned(req=req, db=db, _=None)

        payload = mock_bcast.call_args[0][0]
        assert payload["kind"] == "gtin"
        assert payload["symbology"] == "ean-upc"

    @pytest.mark.asyncio
    async def test_no_symbology_keeps_heuristic_and_null_field(self):
        req = BarcodeScannedRequest(device_id="sb-1", barcode="06938936716785")
        db = _db_returning(_device())

        with (
            _patch_resolution({}, None, []),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            await barcode_scanned(req=req, db=db, _=None)

        payload = mock_bcast.call_args[0][0]
        assert payload["kind"] == "gtin"
        assert payload["symbology"] is None

    @pytest.mark.asyncio
    async def test_invalid_code_skips_resolution(self):
        # "12" canonicalizes to a <3-char code → invalid, resolution not called.
        req = BarcodeScannedRequest(device_id="sb-1", barcode="12")
        db = _db_returning(_device())
        resolve = AsyncMock()

        with (
            patch("backend.app.services.barcode_resolver.resolve_barcode", new=resolve),
            patch("backend.app.api.routes.inventory._load_settings_map", new=AsyncMock(return_value={})),
            patch("backend.app.api.routes.inventory._ensure_spoolman_client", new=AsyncMock(return_value=None)),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            out = await barcode_scanned(req=req, db=db, _=None)

        resolve.assert_not_awaited()
        payload = mock_bcast.call_args[0][0]
        assert payload["valid"] is False
        assert payload["matched"] is False
        assert out["matched"] is False

    @pytest.mark.asyncio
    async def test_disabled_device_ignores_scan(self):
        req = BarcodeScannedRequest(device_id="sb-1", barcode="06938936716785")
        db = _db_returning(_device(enabled=False))
        resolve = AsyncMock()

        with (
            patch("backend.app.services.barcode_resolver.resolve_barcode", new=resolve),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            out = await barcode_scanned(req=req, db=db, _=None)

        assert out == {"status": "ok", "matched": False, "ignored": True}
        resolve.assert_not_awaited()
        mock_bcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ws_payload_keys_match_lookup_response_schema(self):
        """The broadcast must carry every filament field BarcodeLookupResponse
        exposes, so the kiosk can share types with the regular lookup endpoint."""
        req = BarcodeScannedRequest(device_id="sb-1", barcode="06938936716785")
        db = _db_returning(_device())

        with (
            _patch_resolution({"material": "PLA"}, "inventory", []),
            patch("backend.app.api.routes.spoolbuddy.ws_manager.broadcast", new=AsyncMock()) as mock_bcast,
        ):
            await barcode_scanned(req=req, db=db, _=None)

        payload = mock_bcast.call_args[0][0]
        schema_fields = set(BarcodeLookupResponse.model_fields) - {"enabled"}
        assert schema_fields.issubset(payload.keys())
