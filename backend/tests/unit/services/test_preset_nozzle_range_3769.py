"""A spool's slicer preset resolves to its nozzle temperature range (#3769).

One test group per branch of ``resolve_preset_nozzle_range``: a local preset
(read from the database), a custom Bambu Cloud preset (the caller's own
sign-in, then the ``inherits`` chain), and a Bambu system filament (Orca's base
profile for its name). Anything else, a failed lookup and a preset without a
range come back as ``None``: no range is an answer, not an error.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.permissions import Permission
from backend.app.models.local_preset import LocalPreset
from backend.app.models.settings import Settings
from backend.app.services.bambu_cloud import BambuCloudError
from backend.app.services.preset_nozzle_range import PresetNozzleRange, resolve_preset_nozzle_range

# What Orca's BBL profiles hold for Bambu PLA Basic: the range is two parents up,
# the print temperature one up, and the profile named by the cloud preset's
# ``inherits`` carries neither.
ORCA_PROFILES = {
    "Bambu PLA Basic @base": {"name": "Bambu PLA Basic @base", "inherits": "fdm_filament_pla"},
    "Bambu PLA Basic @BBL X1C": {"name": "Bambu PLA Basic @BBL X1C", "inherits": "Bambu PLA Basic @base"},
    "fdm_filament_pla": {"name": "fdm_filament_pla", "inherits": "fdm_filament_common", "nozzle_temperature": ["220"]},
    "fdm_filament_common": {
        "name": "fdm_filament_common",
        "nozzle_temperature": ["200"],
        "nozzle_temperature_range_low": ["190"],
        "nozzle_temperature_range_high": ["240"],
    },
    # A profile that states print temperatures (one per extruder, as a dual-nozzle
    # printer's do) and no range anywhere up its chain.
    "Plain @base": {"name": "Plain @base", "nozzle_temperature": ["215", "230"]},
    # A filament Orca only lists under its plain name.
    "Bambu PA6-CF": {
        "name": "Bambu PA6-CF",
        "nozzle_temperature_range_low": ["260"],
        "nozzle_temperature_range_high": ["300"],
    },
}


@pytest.fixture
def orca():
    """Orca's base profiles from a table instead of GitHub; records the names asked for."""
    asked: list[str] = []

    async def fetch(name, _profile_type, _db):
        asked.append(name)
        return ORCA_PROFILES.get(name)

    with (
        patch("backend.app.services.preset_nozzle_range.fetch_and_cache_base_profile", new=fetch),
        patch("backend.app.services.orca_profiles.fetch_and_cache_base_profile", new=fetch),
    ):
        yield asked


def _user(*, cloud_auth: bool = True):
    user = MagicMock()
    user.id = 7
    user.has_permission = lambda perm: cloud_auth or perm != Permission.CLOUD_AUTH.value
    return user


def _cloud(detail):
    cloud = MagicMock()
    cloud.is_authenticated = True
    cloud.get_setting_detail = (
        AsyncMock(side_effect=detail) if isinstance(detail, Exception) else AsyncMock(return_value=detail)
    )
    cloud.close = AsyncMock()
    return cloud


def _signed_in(cloud, seen: list | None = None):
    async def build(_db, user):
        if seen is not None:
            seen.append(user.id if user is not None else None)
        return cloud

    return patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=build)


async def _local(db: AsyncSession, **fields) -> LocalPreset:
    preset = LocalPreset(name=fields.pop("name", "My PLA"), preset_type="filament", setting="{}", **fields)
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return preset


async def _resolve(db, slicer_filament, user=None):
    return await resolve_preset_nozzle_range(db=db, user=user, slicer_filament=slicer_filament)


# ── local preset ─────────────────────────────────────────────────────────────


async def test_local_preset_gives_its_stored_range(db_session):
    preset = await _local(db_session, nozzle_temp_min=190, nozzle_temp_max=240)

    assert await _resolve(db_session, str(preset.id)) == PresetNozzleRange(min=190, max=240, source="local")


async def test_local_preset_without_a_range_gives_none(db_session):
    preset = await _local(db_session)

    assert await _resolve(db_session, str(preset.id)) is None


async def test_local_preset_with_half_a_range_gives_none(db_session):
    preset = await _local(db_session, nozzle_temp_min=190)

    assert await _resolve(db_session, str(preset.id)) is None


async def test_local_preset_with_one_temperature_is_not_a_range(db_session):
    """An import that only found a print temperature stores it as min and max alike."""
    preset = await _local(db_session, nozzle_temp_min=220, nozzle_temp_max=220)

    assert await _resolve(db_session, str(preset.id)) is None


async def test_unknown_local_preset_gives_none(db_session):
    assert await _resolve(db_session, "9999") is None


async def test_local_printer_preset_is_not_a_filament(db_session):
    """The id is only a filament preset's: a machine preset with the same id does not answer."""
    row = LocalPreset(name="X1C", preset_type="printer", setting="{}", nozzle_temp_min=190, nozzle_temp_max=240)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    assert await _resolve(db_session, str(row.id)) is None


# ── custom Bambu Cloud preset ────────────────────────────────────────────────


async def test_cloud_preset_resolves_its_inherits_chain(db_session, orca):
    detail = {"setting": {"name": "My PLA @BBL X1C", "inherits": "Bambu PLA Basic @BBL X1C"}}

    with _signed_in(_cloud(detail)):
        found = await _resolve(db_session, "PFUS990b6e19965353", _user())

    assert found == PresetNozzleRange(min=190, max=240, source="cloud")
    assert orca == ["Bambu PLA Basic @BBL X1C", "Bambu PLA Basic @base", "fdm_filament_pla", "fdm_filament_common"]


async def test_cloud_preset_own_range_wins_over_the_parents(db_session, orca):
    detail = {
        "setting": {
            "inherits": "Bambu PLA Basic @BBL X1C",
            "nozzle_temperature_range_low": ["200"],
            "nozzle_temperature_range_high": ["230"],
        }
    }

    with _signed_in(_cloud(detail)):
        found = await _resolve(db_session, "PFCN1234567890", _user())

    assert found == PresetNozzleRange(min=200, max=230, source="cloud")


async def test_cloud_preset_read_from_the_top_level_of_the_response(db_session, orca):
    """Some responses carry the preset JSON without the ``setting`` envelope."""
    detail = {"name": "My PLA", "inherits": "Bambu PLA Basic @BBL X1C"}

    with _signed_in(_cloud(detail)):
        found = await _resolve(db_session, "PFUS990b6e19965353", _user())

    assert found == PresetNozzleRange(min=190, max=240, source="cloud")


async def test_cloud_preset_is_asked_for_with_the_callers_own_sign_in(db_session, orca):
    seen: list = []

    with _signed_in(_cloud({"setting": {"inherits": "Bambu PLA Basic @BBL X1C"}}), seen):
        await _resolve(db_session, "PFUS990b6e19965353", _user())

    assert seen == [7]


async def test_cloud_preset_without_a_sign_in_gives_none(db_session, orca):
    async def build(_db, _user):
        return None

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=build):
        assert await _resolve(db_session, "PFUS990b6e19965353", _user()) is None

    assert orca == []


async def test_cloud_preset_with_an_unauthenticated_service_gives_none(db_session, orca):
    cloud = _cloud({"setting": {"inherits": "Bambu PLA Basic @BBL X1C"}})
    cloud.is_authenticated = False

    with _signed_in(cloud):
        assert await _resolve(db_session, "PFUS990b6e19965353", _user()) is None

    cloud.get_setting_detail.assert_not_called()
    cloud.close.assert_awaited_once()


async def test_cloud_preset_without_the_cloud_permission_is_not_looked_up(db_session, orca):
    cloud = _cloud({"setting": {"inherits": "Bambu PLA Basic @BBL X1C"}})

    with _signed_in(cloud):
        assert await _resolve(db_session, "PFUS990b6e19965353", _user(cloud_auth=False)) is None

    cloud.get_setting_detail.assert_not_called()


async def test_cloud_preset_with_login_on_and_no_user_does_not_borrow_the_shared_sign_in(db_session, orca):
    """With login on the sign-in stored without a user belongs to nobody who is asking."""
    db_session.add(Settings(key="auth_enabled", value="true"))
    await db_session.commit()
    seen: list = []

    with _signed_in(_cloud({"setting": {"inherits": "Bambu PLA Basic @BBL X1C"}}), seen):
        assert await _resolve(db_session, "PFUS990b6e19965353", None) is None

    assert seen == []


async def test_cloud_preset_with_login_off_and_no_user_uses_the_shared_sign_in(db_session, orca):
    seen: list = []

    with _signed_in(_cloud({"setting": {"inherits": "Bambu PLA Basic @BBL X1C"}}), seen):
        found = await _resolve(db_session, "PFUS990b6e19965353", None)

    assert found == PresetNozzleRange(min=190, max=240, source="cloud")
    assert seen == [None]


async def test_cloud_failure_gives_none_and_closes_the_service(db_session, orca):
    cloud = _cloud(BambuCloudError("unreachable"))

    with _signed_in(cloud):
        assert await _resolve(db_session, "PFUS990b6e19965353", _user()) is None

    cloud.close.assert_awaited_once()


async def test_cloud_preset_whose_parent_is_unknown_gives_none(db_session, orca):
    with _signed_in(_cloud({"setting": {"inherits": "Not On GitHub"}})):
        assert await _resolve(db_session, "PFUS990b6e19965353", _user()) is None


# ── Bambu system filament ────────────────────────────────────────────────────


async def test_system_filament_resolves_through_its_base_profile(db_session, orca):
    found = await _resolve(db_session, "GFA00")

    assert found == PresetNozzleRange(min=190, max=240, source="system")
    assert orca[0] == "Bambu PLA Basic @base"


async def test_system_preset_id_with_a_printer_suffix_names_the_same_filament(db_session, orca):
    found = await _resolve(db_session, "GFSA00_07")

    assert found == PresetNozzleRange(min=190, max=240, source="system")
    assert orca[0] == "Bambu PLA Basic @base"


async def test_system_filament_listed_only_under_its_plain_name(db_session, orca):
    found = await _resolve(db_session, "GFN05")

    assert found == PresetNozzleRange(min=260, max=300, source="system")
    assert orca == ["Bambu PA6-CF @base", "Bambu PA6-CF"]


async def test_system_filament_without_a_range_in_its_chain_gives_none(db_session, orca):
    """Print temperatures alone are not the filament's range: 215 to 230 would be made up."""
    with patch.dict("backend.app.api.routes.cloud._BUILTIN_FILAMENT_NAMES", {"GFX01": "Plain"}):
        assert await _resolve(db_session, "GFX01") is None


async def test_system_filament_missing_on_github_gives_none(db_session, orca):
    assert await _resolve(db_session, "GFU03") is None  # Bambu TPU 90A, not in the table above
    assert orca == ["Bambu TPU 90A @base", "Bambu TPU 90A"]


async def test_system_filament_unknown_to_bambuddy_gives_none_without_a_lookup(db_session, orca):
    assert await _resolve(db_session, "GFZ99") is None
    assert orca == []


async def test_system_filament_lookup_failure_gives_none(db_session):
    async def broken(*_args):
        raise RuntimeError("github down")

    with patch("backend.app.services.preset_nozzle_range.fetch_and_cache_base_profile", new=broken):
        assert await _resolve(db_session, "GFA00") is None


# ── references that name no preset with a range ──────────────────────────────


@pytest.mark.parametrize(
    "slicer_filament",
    [
        None,
        "",
        "   ",
        "PLA",  # a material name some older spools hold
        "3f2b8c1e-5d4a-4b7e-9c1d-0a1b2c3d4e5f",  # an Orca Cloud profile
        "Generic PLA",
    ],
)
async def test_references_that_name_no_known_preset_give_none(db_session, orca, slicer_filament):
    assert await _resolve(db_session, slicer_filament, _user()) is None
    assert orca == []
