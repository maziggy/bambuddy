"""A Bambu Cloud preset listing with null types or lists still lists presets (#3593).

The cloud sends ``null`` for a preset type, or for its private or public list,
when the account has nothing there. The Slice dialog's preset list and the
Profiles page's cloud list read the listing as nested dicts and failed on it,
so one empty type cost every preset, local ones included.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import cloud as cloud_routes, slicer_presets as sp
from backend.app.services.bambu_cloud import BambuCloudService, _slicer_settings_listing

# Shaped like an account with no filament presets, only stock printers and one
# process preset of its own.
LISTING = {
    "filament": None,
    "printer": {"private": None, "public": [{"setting_id": "GP1", "name": "Bambu Lab X1C 0.4 nozzle"}]},
    "print": {"private": [{"setting_id": "PP1", "name": "My 0.2mm"}], "public": None},
}


def _cloud_answering(body) -> BambuCloudService:
    service = BambuCloudService()
    service.access_token = "test-token"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = body
    service._client = MagicMock()
    service._client.get = AsyncMock(return_value=response)
    service.close = AsyncMock()
    return service


def _user() -> MagicMock:
    user = MagicMock(id=1)
    user.has_permission = MagicMock(return_value=True)
    return user


async def test_the_slice_dialog_lists_the_presets_the_account_has():
    sp._cloud_cache.clear()
    with (
        patch.object(sp, "get_stored_token", AsyncMock(return_value=("tok", None, None))),
        patch.object(sp, "BambuCloudService", return_value=_cloud_answering(LISTING)),
    ):
        slots, status = await sp._fetch_cloud_presets(MagicMock(), _user())
    sp._cloud_cache.clear()

    assert status == "ok"
    assert [p.name for p in slots["printer"]] == ["Bambu Lab X1C 0.4 nozzle"]
    assert [p.name for p in slots["process"]] == ["My 0.2mm"]
    assert slots["filament"] == []


async def test_the_profiles_page_lists_the_presets_the_account_has():
    service = _cloud_answering(LISTING)
    with patch.object(cloud_routes, "build_authenticated_cloud", AsyncMock(return_value=service)):
        result = await cloud_routes.get_slicer_settings(db=MagicMock(), current_user=None)

    assert [s.name for s in result.printer] == ["Bambu Lab X1C 0.4 nozzle"]
    assert [s.name for s in result.process] == ["My 0.2mm"]
    assert result.filament == []


@pytest.mark.parametrize(
    ("body", "expected_printer_private"),
    [
        (None, []),
        ([], []),
        ({"printer": "unexpected"}, []),
        ({"printer": {"private": "unexpected"}}, []),
        ({"printer": {"private": [None, "x", {"setting_id": "P1"}]}}, [{"setting_id": "P1"}]),
    ],
)
def test_every_type_reads_as_lists_of_presets(body, expected_printer_private):
    listing = _slicer_settings_listing(body)

    for key in ("filament", "printer", "print"):
        assert isinstance(listing[key]["private"], list)
        assert isinstance(listing[key]["public"], list)
    assert listing["printer"]["private"] == expected_printer_private


def test_a_full_listing_is_kept_as_it_is():
    body = {
        "filament": {"private": [{"setting_id": "F1"}], "public": [{"setting_id": "F2"}]},
        "printer": {"private": [], "public": []},
        "print": {"private": [], "public": []},
        "message": "success",
    }

    assert _slicer_settings_listing(body) == body
