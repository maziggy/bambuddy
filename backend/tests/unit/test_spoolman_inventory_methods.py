"""Unit tests for new SpoolmanClient inventory methods.

Covers: get_spool, get_all_spools, delete_spool, set_spool_archived,
update_spool_full, find_or_create_vendor, find_or_create_filament.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services.spoolman import SpoolmanClient, SpoolmanUnavailableError


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
        mock_http.request = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.get_spool(42)
        assert result == SAMPLE_SPOOL
        mock_http.request.assert_called_once_with("GET", "http://localhost:7912/api/v1/spool/42", json=None)

    @pytest.mark.asyncio
    async def test_raises_unavailable_on_http_error(self, client):
        from backend.app.services.spoolman import SpoolmanUnavailableError

        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=Exception("not found"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.get_spool(99)

    @pytest.mark.asyncio
    async def test_raises_not_found_on_404_response(self, client):
        """get_spool raises SpoolmanNotFoundError when Spoolman returns HTTP 404 (PT-I3)."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(None, status_code=404))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanNotFoundError),
        ):
            await client.get_spool(99)

    @pytest.mark.asyncio
    async def test_raises_client_error_on_4xx_response(self, client):
        """get_spool raises SpoolmanClientError (not SpoolmanUnavailableError) on non-404 4xx (H2)."""
        from backend.app.services.spoolman import SpoolmanClientError

        mock_request = MagicMock()
        mock_request.url = "http://localhost:7912/api/v1/spool/42"
        mock_resp_obj = MagicMock()
        mock_resp_obj.status_code = 422

        mock_http = AsyncMock()
        resp = _make_response(None, status_code=422)
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("Unprocessable", request=mock_request, response=mock_resp_obj)
        )
        mock_http.request = AsyncMock(return_value=resp)
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanClientError) as exc_info,
        ):
            await client.get_spool(42)
        assert exc_info.value.status_code == 422


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
    async def test_raises_unavailable_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("connection error"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.get_all_spools()


# ---------------------------------------------------------------------------
# delete_spool
# ---------------------------------------------------------------------------


class TestDeleteSpool:
    @pytest.mark.asyncio
    async def test_returns_none_on_success(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(None))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.delete_spool(42)
        assert result is None
        mock_http.request.assert_called_once_with("DELETE", "http://localhost:7912/api/v1/spool/42", json=None)

    @pytest.mark.asyncio
    async def test_raises_unavailable_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=Exception("server error"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.delete_spool(42)


# ---------------------------------------------------------------------------
# set_spool_archived
# ---------------------------------------------------------------------------


class TestSetSpoolArchived:
    @pytest.mark.asyncio
    async def test_archives_spool(self, client):
        archived_spool = {**SAMPLE_SPOOL, "archived": True}
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(archived_spool))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.set_spool_archived(42, archived=True)
        assert result == archived_spool
        mock_http.request.assert_called_once_with(
            "PATCH",
            "http://localhost:7912/api/v1/spool/42",
            json={"archived": True},
        )

    @pytest.mark.asyncio
    async def test_restores_spool(self, client):
        restored_spool = {**SAMPLE_SPOOL, "archived": False}
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(restored_spool))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.set_spool_archived(42, archived=False)
        assert result == restored_spool
        mock_http.request.assert_called_once_with(
            "PATCH",
            "http://localhost:7912/api/v1/spool/42",
            json={"archived": False},
        )

    @pytest.mark.asyncio
    async def test_raises_unavailable_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=Exception("timeout"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.set_spool_archived(42, archived=True)


# ---------------------------------------------------------------------------
# update_spool_full
# ---------------------------------------------------------------------------


class TestUpdateSpoolFull:
    @pytest.mark.asyncio
    async def test_sends_only_provided_fields(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, remaining_weight=600.0, comment="note")
        call_json = mock_http.request.call_args.kwargs["json"]
        assert call_json == {"remaining_weight": 600.0, "comment": "note"}

    @pytest.mark.asyncio
    async def test_clear_location_sets_none(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, clear_location=True)
        call_json = mock_http.request.call_args.kwargs["json"]
        assert call_json == {"location": None}

    @pytest.mark.asyncio
    async def test_location_set_when_not_clearing(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, location="Shelf A")
        call_json = mock_http.request.call_args.kwargs["json"]
        assert call_json == {"location": "Shelf A"}

    @pytest.mark.asyncio
    async def test_empty_comment_sent_as_none(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(SAMPLE_SPOOL))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            await client.update_spool_full(42, comment="")
        call_json = mock_http.request.call_args.kwargs["json"]
        assert call_json == {"comment": None}

    @pytest.mark.asyncio
    async def test_raises_unavailable_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=Exception("network"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.update_spool_full(42, remaining_weight=500.0)


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
    async def test_raises_when_create_fails(self, client):
        with (
            patch.object(client, "get_vendors", AsyncMock(return_value=[])),
            patch.object(client, "create_vendor", AsyncMock(side_effect=SpoolmanUnavailableError("unreachable"))),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.find_or_create_vendor("Ghost Brand")


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
    async def test_color_name_does_not_trigger_filament_patch(self, client):
        """#1357: Spoolman 0.23.1 has no `color_name` field on Filament
        (verified against FilamentUpdateParameters schema). find_or_create_filament
        must NOT attempt to PATCH it — the route now persists the user's
        color_name to spool.extra.bambu_color_name instead. Any patch call
        from this layer would be a silent no-op (Spoolman ignores unknown
        keys) and was the original symptom of "edits never save".
        """
        existing = {**SAMPLE_FILAMENT}
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=3)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[existing])),
            patch.object(client, "patch_filament", AsyncMock()) as mock_patch,
        ):
            result = await client.find_or_create_filament(
                "PLA", "Basic", "Bambu Lab", "FF0000", 1000, color_name="Sunny Yellow"
            )
        assert result == 7
        mock_patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_matches_filament_named_with_just_subtype(self, client):
        """#1357: AMS-sync auto-create saves the filament with name set to just
        ``tray.tray_sub_brands`` (e.g. ``"Glow"`` without the material prefix),
        but the user-driven edit path composes ``"<material> <subtype>"``
        (``"PLA Glow"``). Before this fix the literal `f_name == name` check
        failed to bridge the two shapes, so every edit fell through to
        ``create_filament`` and left a trail of duplicate filaments. Now the
        name match strips the material prefix on both sides, so the two
        shapes resolve to the same subtype key."""
        existing = {
            **SAMPLE_FILAMENT,
            "id": 11,
            "name": "Glow",  # AMS-sync shape: just subtype
            "material": "PLA",
            "color_hex": "AAF3C6",
            "color_name": None,
            "vendor": {"id": 3, "name": "Amazon Basics"},
        }
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=3)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[existing])),
            patch.object(client, "patch_filament", AsyncMock()) as mock_patch,
            patch.object(client, "create_filament", AsyncMock()) as mock_create,
        ):
            result = await client.find_or_create_filament(
                "PLA", "Glow", "Amazon Basics", "AAF3C6", 1000, color_name="Bright Glow"
            )
        assert result == 11
        # color_name is no longer written via the filament — see #1357 — and
        # the function must not create a duplicate filament.
        mock_patch.assert_not_called()
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_still_matches_filament_named_material_plus_subtype(self, client):
        """The composed-name shape (``"PLA Basic"`` matching a Spoolman filament
        also named ``"PLA Basic"``) must keep working — the normalisation strips
        the prefix on both sides, so the comparison is on the subtype part."""
        existing = {
            **SAMPLE_FILAMENT,
            "id": 7,
            "name": "PLA Basic",
            "material": "PLA",
            "color_hex": "FF0000",
            "color_name": "Sunset",
        }
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=3)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[existing])),
            patch.object(client, "patch_filament", AsyncMock(return_value={"id": 7})),
            patch.object(client, "create_filament", AsyncMock()) as mock_create,
        ):
            result = await client.find_or_create_filament(
                "PLA", "Basic", "Bambu Lab", "FF0000", 1000, color_name="Sunset"
            )
        assert result == 7
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_name_match_does_not_cross_materials(self, client):
        """Sanity check: a filament with name=subtype must NOT match a request
        with a different material that happens to share the subtype string.
        material_match runs first and fails, so the iteration moves on and
        ``create_filament`` is called."""
        existing = {
            **SAMPLE_FILAMENT,
            "id": 7,
            "name": "Basic",
            "material": "PETG",  # different material
            "color_hex": "FF0000",
        }
        new_filament = {"id": 99, "name": "PLA Basic"}
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=3)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[existing])),
            patch.object(client, "create_filament", AsyncMock(return_value=new_filament)) as mock_create,
        ):
            result = await client.find_or_create_filament(
                "PLA", "Basic", "Bambu Lab", "FF0000", 1000, color_name="Sunset"
            )
        assert result == 99
        mock_create.assert_called_once()

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
        # color_name is intentionally not forwarded to create_filament (#1357):
        # Spoolman has no such field on Filament, so passing it would be a
        # no-op. The route persists color_name to spool.extra.bambu_color_name
        # after this returns.
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
    async def test_raises_when_create_fails(self, client):
        with (
            patch.object(client, "find_or_create_vendor", AsyncMock(return_value=1)),
            patch.object(client, "get_filaments", AsyncMock(return_value=[])),
            patch.object(client, "create_filament", AsyncMock(side_effect=SpoolmanUnavailableError("unreachable"))),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.find_or_create_filament("TPU", "Flex", "Generic", "000000", 500)


# ---------------------------------------------------------------------------
# get_filaments / get_vendors / get_external_filaments error propagation (H11)
# ---------------------------------------------------------------------------


class TestGetFilamentsRaisesOnError:
    @pytest.mark.asyncio
    async def test_raises_unavailable_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("timeout"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.get_filaments()


class TestGetVendorsRaisesOnError:
    @pytest.mark.asyncio
    async def test_raises_unavailable_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("timeout"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.get_vendors()


class TestGetExternalFilamentsRaisesOnError:
    @pytest.mark.asyncio
    async def test_raises_unavailable_on_error(self, client):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("timeout"))
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(SpoolmanUnavailableError),
        ):
            await client.get_external_filaments()


# ---------------------------------------------------------------------------
# get_distinct_locations — shape normalisation (#1505 review BLOCKER 3)
# ---------------------------------------------------------------------------


class TestGetDistinctLocationsShape:
    @pytest.mark.asyncio
    async def test_passes_through_list_of_strings(self, client):
        with patch.object(client, "_get_with_retry", AsyncMock(return_value=["Drybox 1", "Shelf"])):
            result = await client.get_distinct_locations()
        assert result == ["Drybox 1", "Shelf"]

    @pytest.mark.asyncio
    async def test_extracts_name_from_list_of_dicts(self, client):
        with patch.object(
            client,
            "_get_with_retry",
            AsyncMock(return_value=[{"id": 1, "name": "Drybox 1"}, {"id": 2, "name": "Shelf"}]),
        ):
            result = await client.get_distinct_locations()
        assert result == ["Drybox 1", "Shelf"]

    @pytest.mark.asyncio
    async def test_drops_non_string_and_dict_without_name(self, client):
        with patch.object(
            client,
            "_get_with_retry",
            AsyncMock(return_value=[{"id": 1}, None, 42, "Shelf"]),
        ):
            result = await client.get_distinct_locations()
        assert result == ["Shelf"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_non_list_payload(self, client):
        # A misconfigured proxy or auth-redirect can serve HTML; the old shape
        # would TypeError on iteration. We coerce to [].
        with patch.object(client, "_get_with_retry", AsyncMock(return_value={"error": "unauthorized"})):
            result = await client.get_distinct_locations()
        assert result == []


# ---------------------------------------------------------------------------
# rename_location — bulk endpoint + per-spool fallback (#1505 review BLOCKER 2)
# ---------------------------------------------------------------------------


class TestRenameLocationBulkAndFallback:
    @pytest.mark.asyncio
    async def test_bulk_endpoint_success_returns_zero(self, client):
        """Modern Spoolman PATCH /location/{name} succeeds — fallback not used."""
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=_make_response(None))
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.rename_location("Drybox 1", "Drybox 2")
        assert result == 0
        # Confirm the bulk path was used (no per-spool PATCH).
        mock_http.patch.assert_called_once()
        assert "/location/" in mock_http.patch.call_args.args[0]

    @pytest.mark.asyncio
    async def test_bulk_endpoint_404_falls_back_to_per_spool_patch(self, client):
        """Older Spoolman versions return 404 on the bulk endpoint — the
        fallback iterates every spool currently at the old name."""
        bulk_response = MagicMock()
        bulk_response.status_code = 404
        bulk_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=MagicMock(status_code=404))
        )
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=bulk_response)

        spools_at_old = [
            {"id": 11, "location": "Drybox 1"},
            {"id": 12, "location": "Drybox 1"},
            {"id": 13, "location": "Shelf A"},  # different location — must be skipped
        ]
        patch_response = MagicMock()
        patch_response.status_code = 200
        patch_response.raise_for_status = MagicMock()
        patch_response.json.return_value = {"id": 0, "location": "Drybox 2"}

        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            patch.object(client, "get_all_spools", AsyncMock(return_value=spools_at_old)),
            patch.object(client, "_request_spool", AsyncMock(return_value=patch_response)) as request_spool_mock,
        ):
            result = await client.rename_location("Drybox 1", "Drybox 2")

        assert result == 2
        # Only the two matching spools should be PATCHed.
        assert request_spool_mock.await_count == 2
        called_ids = sorted(call.args[1] for call in request_spool_mock.await_args_list)
        assert called_ids == [11, 12]
        # And each call should set the new location string.
        for call in request_spool_mock.await_args_list:
            assert call.kwargs["json_body"] == {"location": "Drybox 2"}

    @pytest.mark.asyncio
    async def test_bulk_endpoint_405_also_falls_back(self, client):
        """Some Spoolman versions return 405 Method Not Allowed instead of 404
        when the bulk endpoint is missing — same fallback."""
        bulk_response = MagicMock()
        bulk_response.status_code = 405
        bulk_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "405 Method Not Allowed", request=MagicMock(), response=MagicMock(status_code=405)
            )
        )
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=bulk_response)

        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            patch.object(client, "get_all_spools", AsyncMock(return_value=[])),
        ):
            result = await client.rename_location("Drybox 1", "Drybox 2")
        # No spools at the old name → nothing to do, fallback returns 0.
        assert result == 0

    @pytest.mark.asyncio
    async def test_bulk_endpoint_non_404_5xx_propagates(self, client):
        """A genuine server error must NOT silently fall back."""
        bulk_response = MagicMock()
        bulk_response.status_code = 500
        bulk_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock(status_code=500))
        )
        mock_http = AsyncMock()
        mock_http.patch = AsyncMock(return_value=bulk_response)
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client.rename_location("Drybox 1", "Drybox 2")
