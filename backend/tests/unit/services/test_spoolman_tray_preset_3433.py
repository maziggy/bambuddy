"""The AMS sync keeps the tray's filament preset on the Spoolman spool (#3433).

An internal RFID spool stores the tray's ``tray_info_idx`` as its
``slicer_filament``. A Spoolman spool the sync created from the same tray got
only its tag, so assigning it to the external slot later -- no reader there --
wrote the generic preset for its material: a Bambu PLA Basic became Generic PLA.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.spoolman import AMSTray, SpoolmanClient, SpoolmanUnavailableError

UUID = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"


def _tray(*, preset="GFA00", uuid=UUID, tag="", sub_brands="PLA Basic"):
    return AMSTray(
        ams_id=0,
        tray_id=0,
        tray_type="PLA",
        tray_sub_brands=sub_brands,
        tray_color="000000FF",
        remain=100,
        tag_uid=tag,
        tray_uuid=uuid,
        tray_info_idx=preset,
        tray_weight=1000,
    )


@pytest.fixture
def client():
    return SpoolmanClient("http://localhost:7912")


async def _create(client, tray):
    """Run the sync's auto-create for an unknown tag; returns (create_spool mock, profile-default fill mock)."""
    with (
        patch.object(client, "find_spool_by_tag", AsyncMock(return_value=None)),
        patch.object(client, "_find_or_create_filament", AsyncMock(return_value={"id": 5})),
        patch.object(client, "find_or_create_filament", AsyncMock(return_value=5)),
        patch.object(client, "create_spool", AsyncMock(return_value={"id": 9, "extra": {}})) as create,
        patch.object(client, "add_native_tags", AsyncMock()),
        patch("backend.app.services.spoolman.resolve_bambu_color", AsyncMock(return_value=None)),
        patch("backend.app.services.filament_profile_default.fill_auto_added_spoolman_spool", AsyncMock()) as fill,
    ):
        await client.sync_ams_tray(tray, "X2D", None, auto_add_unknown_rfid=True)
    return create, fill


class TestAutoCreate:
    @pytest.mark.asyncio
    async def test_a_bambu_tray_s_preset_is_stored_with_its_name(self, client):
        create, fill = await _create(client, _tray())
        extra = create.call_args.kwargs["extra"]
        assert extra["bambu_slicer_filament"] == json.dumps("GFA00")
        assert extra["bambu_slicer_filament_name"] == json.dumps("Bambu PLA Basic")
        # The tag's preset is the spool's, so a profile default's is not applied.
        assert fill.call_args.kwargs["include_slicer"] is False

    @pytest.mark.asyncio
    async def test_an_unlisted_preset_is_named_by_the_sub_brand(self, client):
        create, _ = await _create(client, _tray(preset="GFZ77", sub_brands="PLA Something"))
        extra = create.call_args.kwargs["extra"]
        assert extra["bambu_slicer_filament"] == json.dumps("GFZ77")
        assert extra["bambu_slicer_filament_name"] == json.dumps("PLA Something")

    @pytest.mark.asyncio
    async def test_a_third_party_tag_gets_no_preset(self, client):
        create, fill = await _create(client, _tray(preset="", uuid="0" * 32, tag="0A1B2C3D4E5F6071"))
        extra = create.call_args.kwargs["extra"]
        assert "bambu_slicer_filament" not in extra
        assert fill.call_args.kwargs["include_slicer"] is True


class TestExistingSpool:
    async def _sync(self, client, spool, *, merge=None):
        merge = merge or AsyncMock(return_value={})
        with (
            patch.object(client, "find_spool_by_tag", AsyncMock(return_value=spool)),
            patch.object(client, "add_native_tags", AsyncMock()),
            patch.object(client, "update_spool", AsyncMock(return_value={"id": spool["id"]})) as update,
            patch.object(client, "merge_spool_extra", merge),
        ):
            result = await client.sync_ams_tray(_tray(), "X2D", None)
        return merge, update, result

    @pytest.mark.asyncio
    async def test_a_spool_without_a_preset_gets_the_tray_s_once(self, client):
        merge, _, _ = await self._sync(client, {"id": 6, "extra": {"tag": json.dumps(UUID)}})
        merge.assert_awaited_once_with(
            6,
            {"bambu_slicer_filament": json.dumps("GFA00"), "bambu_slicer_filament_name": json.dumps("Bambu PLA Basic")},
        )

    @pytest.mark.asyncio
    async def test_an_empty_preset_counts_as_none(self, client):
        merge, _, _ = await self._sync(client, {"id": 6, "extra": {"bambu_slicer_filament": json.dumps("")}})
        merge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_preset_set_by_hand_stays(self, client):
        merge, _, _ = await self._sync(client, {"id": 6, "extra": {"bambu_slicer_filament": json.dumps("GFA01")}})
        merge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spoolman_refusing_the_write_does_not_stop_the_sync(self, client):
        merge = AsyncMock(side_effect=SpoolmanUnavailableError("down"))
        _, update, result = await self._sync(client, {"id": 6, "extra": {}}, merge=merge)
        update.assert_awaited_once()
        assert result == {"id": 6}
