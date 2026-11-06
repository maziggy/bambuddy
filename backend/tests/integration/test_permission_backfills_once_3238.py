"""Group permission backfills run once, not on every start (#3238).

They used to add their permission to every matching group on each start, so
an admin who took MakerWorld (or clear plate, forecasting, pipelines) away
from a group got it back after a restart.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core import database as _database_module
from backend.app.core.database import seed_default_groups
from backend.app.models.group import Group
from backend.app.models.settings import Settings

# (flag, permission the Administrators group has once a version with the
# backfill started, group permissions that earn it, permission it adds)
BACKFILLS = [
    pytest.param(
        "_backfill_446_clear_plate_permission_done",
        "printers:clear_plate",
        ["printers:control"],
        "printers:clear_plate",
        id="clear_plate",
    ),
    pytest.param(
        "_backfill_1099_makerworld_permissions_done",
        "makerworld:view",
        ["library:upload"],
        "makerworld:import",
        id="makerworld",
    ),
    pytest.param(
        "_backfill_1184_forecast_permissions_done",
        "inventory:forecast_read",
        ["inventory:read"],
        "inventory:forecast_read",
        id="forecast",
    ),
    pytest.param(
        "_backfill_1425_pipeline_permissions_done",
        "pipelines:read",
        ["settings:read"],
        "pipelines:read",
        id="pipelines",
    ),
]


async def _set_group(name: str, permissions: list[str]) -> None:
    async with _database_module.async_session() as session:
        group = (await session.execute(select(Group).where(Group.name == name))).scalar_one_or_none()
        if group is None:
            session.add(Group(name=name, permissions=permissions, is_system=False))
        else:
            group.permissions = permissions
        await session.commit()


async def _perms(name: str) -> set[str]:
    async with _database_module.async_session() as session:
        group = (await session.execute(select(Group).where(Group.name == name))).scalar_one()
        return set(group.permissions or [])


async def _upgrade_from(flag: str, marker: str, *, knew_it: bool) -> None:
    """Make the next start an upgrade from a version without the flag.

    ``knew_it`` is whether that version already had the permission, which
    leaves ``marker`` on Administrators.
    """
    async with _database_module.async_session() as session:
        row = (await session.execute(select(Settings).where(Settings.key == flag))).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
        admin = (await session.execute(select(Group).where(Group.name == "Administrators"))).scalar_one()
        perms = [p for p in admin.permissions if p != marker]
        admin.permissions = [*perms, marker] if knew_it else perms
        await session.commit()


async def _flag_set(flag: str) -> bool:
    async with _database_module.async_session() as session:
        return (await session.execute(select(Settings).where(Settings.key == flag))).scalar_one_or_none() is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_makerworld_stays_off_after_restart(async_client: AsyncClient):
    """The report: MakerWorld turned off for a group with library:upload."""
    await _set_group("student", ["library:read_own", "library:upload"])
    await seed_default_groups()
    await seed_default_groups()

    assert "makerworld:view" not in await _perms("student")
    assert "makerworld:import" not in await _perms("student")


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(("flag", "marker", "earns", "added"), BACKFILLS)
async def test_removed_permission_stays_removed(async_client: AsyncClient, flag, marker, earns, added):
    await _set_group("custom", earns)
    await seed_default_groups()
    await seed_default_groups()

    assert added not in await _perms("custom")
    assert await _flag_set(flag)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(("flag", "marker", "earns", "added"), BACKFILLS)
async def test_upgrade_from_a_version_that_already_ran_it(async_client: AsyncClient, flag, marker, earns, added):
    """Before #3238 the backfill ran on every start; the start that adds the
    flag must not hand back what an admin removed since."""
    await _set_group("custom", earns)
    await _upgrade_from(flag, marker, knew_it=True)

    await seed_default_groups()

    assert added not in await _perms("custom")
    assert await _flag_set(flag)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(("flag", "marker", "earns", "added"), BACKFILLS)
async def test_upgrade_from_before_the_permission(async_client: AsyncClient, flag, marker, earns, added):
    """The backfill still runs on an install that never had the permission,
    including those after the Administrators sync, which adds the permission
    to Administrators on that same start."""
    await _set_group("custom", earns)
    await _upgrade_from(flag, marker, knew_it=False)

    await seed_default_groups()
    assert added in await _perms("custom")

    # Taken away again, it stays away.
    await _set_group("custom", earns)
    await seed_default_groups()
    assert added not in await _perms("custom")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_administrators_still_get_every_permission(async_client: AsyncClient):
    """Administrators are synced on every start; that is not a backfill."""
    await seed_default_groups()
    await _set_group("Administrators", ["settings:read"])

    await seed_default_groups()

    assert {"makerworld:view", "makerworld:import", "printers:clear_plate"} <= await _perms("Administrators")
