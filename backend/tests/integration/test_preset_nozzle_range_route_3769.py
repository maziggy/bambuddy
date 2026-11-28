"""GET /inventory/preset-nozzle-range answers with the range of a spool's preset (#3769).

The resolver has its own branch tests; these cover the route around it: the
answer's shape, a preset with no range, the permission, and whose cloud sign-in
a custom cloud preset is looked up with (the caller's, or an API key's owner,
the #3519 rule).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.local_preset import LocalPreset

URL = "/api/v1/inventory/preset-nozzle-range"
PFUS = "PFUSdfae86e31579cb"

# Orca's profiles the cloud preset below inherits, from a table instead of GitHub.
ORCA = {
    "Bambu PLA Basic @BBL X1C": {"inherits": "fdm_filament_common"},
    "fdm_filament_common": {
        "nozzle_temperature_range_low": ["190"],
        "nozzle_temperature_range_high": ["240"],
    },
}


@pytest.fixture
def orca():
    async def fetch(name, _profile_type, _db):
        return ORCA.get(name)

    with (
        patch("backend.app.services.preset_nozzle_range.fetch_and_cache_base_profile", new=fetch),
        patch("backend.app.services.orca_profiles.fetch_and_cache_base_profile", new=fetch),
    ):
        yield


def _fake_cloud(seen: list):
    """build_authenticated_cloud stand-in: records whose sign-in was asked for.

    Only a user's own sign-in finds the preset, as on a per-user install where
    the shared one is empty.
    """
    cloud = MagicMock()
    cloud.is_authenticated = True
    cloud.get_setting_detail = AsyncMock(
        return_value={"setting": {"name": "SUNLU PLA META", "inherits": "Bambu PLA Basic @BBL X1C"}}
    )
    cloud.close = AsyncMock()

    async def build(_db, user):
        seen.append(user.id if user is not None else None)
        return cloud if user is not None else None

    return build


@pytest.fixture
async def auth_on(db_session: AsyncSession):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="auth_enabled", value="true"))
    await db_session.commit()


@pytest.fixture
async def owner(db_session: AsyncSession):
    from backend.app.models.user import User

    user = User(username="spoolowner", password_hash="x", role="admin", is_active=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _key(db_session: AsyncSession, user_id: int | None, *, can_read_status: bool = True) -> str:
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(
            name="espoolbuddy",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=user_id,
            can_read_status=can_read_status,
            enabled=True,
        )
    )
    await db_session.commit()
    return full_key


async def test_local_preset_answers_with_its_range_and_where_it_came_from(async_client: AsyncClient, db_session):
    preset = LocalPreset(name="My PLA", preset_type="filament", setting="{}", nozzle_temp_min=195, nozzle_temp_max=235)
    db_session.add(preset)
    await db_session.commit()
    await db_session.refresh(preset)

    response = await async_client.get(URL, params={"slicer_filament": str(preset.id)})

    assert response.status_code == 200
    assert response.json() == {"nozzle_temp_min": 195, "nozzle_temp_max": 235, "source": "local"}


async def test_system_filament_answers_with_the_range_of_its_base_profile(async_client: AsyncClient):
    async def fetch(name, _profile_type, _db):
        return {"inherits": "fdm_filament_common"} if name == "Bambu PLA Basic @base" else ORCA.get(name)

    with (
        patch("backend.app.services.preset_nozzle_range.fetch_and_cache_base_profile", new=fetch),
        patch("backend.app.services.orca_profiles.fetch_and_cache_base_profile", new=fetch),
    ):
        response = await async_client.get(URL, params={"slicer_filament": "GFA00"})

    assert response.status_code == 200
    assert response.json() == {"nozzle_temp_min": 190, "nozzle_temp_max": 240, "source": "system"}


async def test_a_preset_with_no_range_is_an_empty_answer_not_an_error(async_client: AsyncClient):
    response = await async_client.get(URL, params={"slicer_filament": "9999"})

    assert response.status_code == 200
    assert response.json() == {"nozzle_temp_min": None, "nozzle_temp_max": None, "source": None}


async def test_the_preset_is_required(async_client: AsyncClient):
    assert (await async_client.get(URL)).status_code == 422
    assert (await async_client.get(URL, params={"slicer_filament": ""})).status_code == 422


async def test_nothing_is_stored_on_any_spool(async_client: AsyncClient, db_session):
    """The dialog saves the range when the user touches the field; looking it up writes nothing."""
    from sqlalchemy import select

    from backend.app.models.spool import Spool

    preset = LocalPreset(name="My PLA", preset_type="filament", setting="{}", nozzle_temp_min=195, nozzle_temp_max=235)
    spool = Spool(material="PLA", label_weight=1000, weight_used=0, slicer_filament="1")
    db_session.add_all([preset, spool])
    await db_session.commit()

    await async_client.get(URL, params={"slicer_filament": "1"})

    stored = (await db_session.execute(select(Spool.nozzle_temp_min, Spool.nozzle_temp_max))).one()
    assert tuple(stored) == (None, None)


async def test_login_on_without_a_token_is_refused(async_client: AsyncClient, auth_on):
    response = await async_client.get(URL, params={"slicer_filament": "1"})

    assert response.status_code == 401


async def test_api_key_without_read_scope_is_refused(async_client: AsyncClient, auth_on, owner, db_session):
    key = await _key(db_session, owner.id, can_read_status=False)

    response = await async_client.get(URL, params={"slicer_filament": "1"}, headers={"X-API-Key": key})

    assert response.status_code == 403


async def test_api_key_resolves_a_cloud_preset_with_its_owners_sign_in(
    async_client: AsyncClient, auth_on, owner, db_session, orca
):
    key = await _key(db_session, owner.id)
    seen: list = []

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=_fake_cloud(seen)):
        response = await async_client.get(URL, params={"slicer_filament": PFUS}, headers={"X-API-Key": key})

    assert response.status_code == 200
    assert response.json() == {"nozzle_temp_min": 190, "nozzle_temp_max": 240, "source": "cloud"}
    assert seen == [owner.id]


async def test_api_key_without_an_owner_has_no_cloud_sign_in_to_use(
    async_client: AsyncClient, auth_on, db_session, orca
):
    key = await _key(db_session, None)
    seen: list = []

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=_fake_cloud(seen)):
        response = await async_client.get(URL, params={"slicer_filament": PFUS}, headers={"X-API-Key": key})

    assert response.status_code == 200
    assert response.json() == {"nozzle_temp_min": None, "nozzle_temp_max": None, "source": None}
    assert seen == []
