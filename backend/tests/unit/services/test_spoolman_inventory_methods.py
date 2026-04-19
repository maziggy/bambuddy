"""Unit tests for new SpoolmanClient inventory methods.

Covers: get_spool, get_all_spools, delete_spool, set_spool_archived,
update_spool_full, find_or_create_vendor, find_or_create_filament.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.spoolman import SpoolmanClient


@pytest.fixture
def client():
    return SpoolmanClient("http://localhost:7912")


def _make_response(json_data, status_code=200):
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_SPOOL = {
    "id": 42,
    "remaining_weight": 750.0,
    "used_weight": 250.0,
    "archived": False,
    "filament": {"id": 7, "name": "PLA Basic", "material": "PLA"},
}

SAMPLE_FILAMENT = {
    "id": 7,
    "name": "PLA Basic",
    "material": "PLA",
    "color_hex": "FF0000",
    "weight": 1000.0,
    "vendor": {"id": 3, "name": "Bambu Lab"},
}

SAMPLE_VENDOR = {"id": 3, "name": "Bambu Lab"}


# ---------------------------------------------------------------------------
# get_spool
# ---------------------------------------------------------------------------


class TestGetSpool:
    @pytest.mark.asyncio
    async def test_returns_spool_dict_on_success(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.get_spool(42)
        assert result == SAMPLE_SPOOL
        mock_http.get.assert_called_once_with("http://localhost:7912/api/v1/spool/42")

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("not found"))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.get_spool(99)
        assert result is None


# ---------------------------------------------------------------------------
# get_all_spools
# ---------------------------------------------------------------------------


class TestGetAllSpools:
    @pytest.mark.asyncio
    async def test_returns_list_without_archived_by_default(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_make_response([SAMPLE_SPOOL]))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.get_all_spools()
        assert result == [SAMPLE_SPOOL]
        mock_http.get.assert_called_once_with("http://localhost:7912/api/v1/spool", params=None)

    @pytest.mark.asyncio
    async def test_passes_allow_archived_param(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_make_response([SAMPLE_SPOOL]))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.get_all_spools(allow_archived=True)
        mock_http.get.assert_called_once_with("http://localhost:7912/api/v1/spool", params={"allow_archived": "true"})

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("connection error"))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.get_all_spools()
        assert result == []


# ---------------------------------------------------------------------------
# delete_spool
# ---------------------------------------------------------------------------


class TestDeleteSpool:
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, client):
        mock_http = AsyncMock()
        mock_http.delete = AsyncMock(return_value=_make_response(None))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.delete_spool(42)
        assert result is True
        mock_http.delete.assert_called_once_with("http://localhost:7912/api/v1/spool/42")

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.delete = AsyncMock(side_effect=Exception("server error"))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.delete_spool(42)
        assert result is False


# ---------------------------------------------------------------------------
# set_spool_archived
# ---------------------------------------------------------------------------


class TestSetSpoolArchived:
    @pytest.mark.asyncio
    async def test_archives_spool(self, client):
        archived_spool = {**SAMPLE_SPOOL, "archived": True}
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=_make_response(archived_spool))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.set_spool_archived(42, archived=True)
        assert result == archived_spool
        mock_http.patch.assert_called_once_with(
            "http://localhost:7912/api/v1/spool/42",
            json={"archived": True},
        )

    @pytest.mark.asyncio
    async def test_restores_spool(self, client):
        restored_spool = {**SAMPLE_SPOOL, "archived": False}
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=_make_response(restored_spool))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.set_spool_archived(42, archived=False)
        assert result == restored_spool
        mock_http.patch.assert_called_once_with(
            "http://localhost:7912/api/v1/spool/42",
            json={"archived": False},
        )

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(side_effect=Exception("timeout"))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.set_spool_archived(42, archived=True)
        assert result is None


# ---------------------------------------------------------------------------
# update_spool_full
# ---------------------------------------------------------------------------


class TestUpdateSpoolFull:
    @pytest.mark.asyncio
    async def test_sends_only_provided_fields(self, client):
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, remaining_weight=600.0, comment="note")
        call_json = mock_http.patch.call_args.kwargs["json"]
        assert call_json == {"remaining_weight": 600.0, "comment": "note"}

    @pytest.mark.asyncio
    async def test_clear_location_sets_none(self, client):
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, clear_location=True)
        call_json = mock_http.patch.call_args.kwargs["json"]
        assert call_json == {"location": None}

    @pytest.mark.asyncio
    async def test_location_set_when_not_clearing(self, client):
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, location="Shelf A")
        call_json = mock_http.patch.call_args.kwargs["json"]
        assert call_json == {"location": "Shelf A"}

    @pytest.mark.asyncio
    async def test_empty_comment_sent_as_none(self, client):
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, comment="")
        call_json = mock_http.patch.call_args.kwargs["json"]
        assert call_json == {"comment": None}

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(side_effect=Exception("network"))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.update_spool_full(42, remaining_weight=500.0)
        assert result is None


# ---------------------------------------------------------------------------
# find_or_create_vendor
# ---------------------------------------------------------------------------


class TestFindOrCreateVendor:
    @pytest.mark.asyncio
    async def test_returns_existing_vendor_id(self, client):
        with patch.object(client, "get_vendors", AsyncMock(return_value=[SAMPLE_VENDOR])):
            result = await client.find_or_create_vendor("Bambu Lab")
        assert result == 3

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self, client):
        with patch.object(client, "get_vendors", AsyncMock(return_value=[SAMPLE_VENDOR])):
            result = await client.find_or_create_vendor("bambu lab")
        assert result == 3

    @pytest.mark.asyncio
    async def test_creates_vendor_when_not_found(self, client):
        new_vendor = {"id": 10, "name": "New Brand"}
        with (
            patch.object(client, "get_vendors", AsyncMock(return_value=[])),
            patch.object(client, "create_vendor", AsyncMock(return_value=new_vendor)) as mock_create,
        ):
            result = await client.find_or_create_vendor("New Brand")
        assert result == 10
        mock_create.assert_called_once_with("New Brand")

    @pytest.mark.asyncio
    async def test_returns_none_when_create_fails(self, client):
        with (
            patch.object(client, "get_vendors", AsyncMock(return_value=[])),
            patch.object(client, "create_vendor", AsyncMock(return_value=None)),
        ):
            result = await client.find_or_create_vendor("Ghost Brand")
        assert result is None


# ---------------------------------------------------------------------------
# find_or_create_filament
# ---------------------------------------------------------------------------


class TestFindOrCreateFilament:
    @pytest.mark.asyncio
    async def test_returns_existing_filament_id(self, client):
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=3)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[SAMPLE_FILAMENT])),
        ):
            result = await client.find_or_create_filament("PLA", "Basic", "Bambu Lab", "FF0000", 1000)
        assert result == 7

    @pytest.mark.asyncio
    async def test_creates_filament_when_no_match(self, client):
        new_filament = {"id": 99, "name": "PETG Pro"}
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=3)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[])),
            patch.object(client, "create_filament", AsyncMock(return_value=new_filament)) as mock_create,
        ):
            result = await client.find_or_create_filament("PETG", "Pro", "Bambu Lab", "00FF00", 1000)
        assert result == 99
        mock_create.assert_called_once_with(
            name="PETG Pro",
            vendor_id=3,
            material="PETG",
            color_hex="00FF00",
            weight=1000.0,
        )

    @pytest.mark.asyncio
    async def test_no_brand_skips_vendor_lookup(self, client):
        filament_no_vendor = {
            **SAMPLE_FILAMENT,
            "vendor": None,
            "name": "PLA Basic",
            "color_hex": "FF0000",
        }
        with (
            patch.object(client, "get_filaments", AsyncMock(return_value=[filament_no_vendor])),
        ):
            result = await client.find_or_create_filament("PLA", "Basic", None, "FF0000", 1000)
        assert result == 7

    @pytest.mark.asyncio
    async def test_color_hex_normalised_to_uppercase(self, client):
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=None)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[])),
            patch.object(client, "create_filament", AsyncMock(return_value={"id": 5})) as mock_create,
        ):
            await client.find_or_create_filament("ABS", "", None, "ff0000", 750)
        mock_create.assert_called_once_with(
            name="ABS",
            vendor_id=None,
            material="ABS",
            color_hex="FF0000",
            weight=750.0,
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_create_fails(self, client):
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=None)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[])),
            patch.object(client, "create_filament", AsyncMock(return_value=None)),
        ):
            result = await client.find_or_create_filament("TPU", "Flex", "Generic", "000000", 500)
        assert result is None
