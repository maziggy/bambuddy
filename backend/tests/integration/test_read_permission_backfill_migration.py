"""Migration tests for maziggy/bambuddy-security #2 — read permission OWN/ALL backfill.

Pre-fix, ARCHIVES_READ / LIBRARY_READ / QUEUE_READ were flat "read all" flags.
Post-fix they split into OWN/ALL. The migration in seed_default_groups must:

  1. Rename legacy `archives:read` etc to `archives:read_all` on Administrators
     and to `archives:read_own` on every other role (fail-closed default).
  2. Backfill `_own` AND `_all` variants for the Administrators group on upgrade
     so an upgraded install matches a fresh install's permission set.
  3. Backfill `_own` variants for Operators and Viewers so they keep read access
     even if their stored row didn't carry the legacy flag.

These regressions are the failure shape Maziggy hit on a live upgrade — the
admin role ended up missing queue:read_own AND queue:read after migration.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core import database as _database_module
from backend.app.core.database import seed_default_groups
from backend.app.models.group import Group

_READ_FLAGS = frozenset(
    {
        "archives:read",
        "archives:read_own",
        "archives:read_all",
        "library:read",
        "library:read_own",
        "library:read_all",
        "queue:read",
        "queue:read_own",
        "queue:read_all",
    }
)


async def _strip_and_set(group_name: str, extra: list[str] | None = None) -> None:
    """Strip every read flag from ``group_name`` then add ``extra`` flags.

    Simulates a pre-migration state where the group either had only the
    legacy flat permission (set ``extra=['archives:read']``) or no read
    permission at all (set ``extra=None``).
    """
    async with _database_module.async_session() as session:
        grp = (await session.execute(select(Group).where(Group.name == group_name))).scalar_one_or_none()
        assert grp is not None, f"group {group_name} not pre-seeded"
        stripped = [p for p in (grp.permissions or []) if p not in _READ_FLAGS]
        stripped.extend(extra or [])
        grp.permissions = stripped
        await session.commit()


async def _get_perms(group_name: str) -> set[str]:
    async with _database_module.async_session() as session:
        grp = (await session.execute(select(Group).where(Group.name == group_name))).scalar_one_or_none()
        assert grp is not None
        return set(grp.permissions or [])


# Note: ``async_client`` is depended upon (even though unused) so pytest-asyncio
# uses the same event loop the conftest fixture uses for async_session(). Without
# it, calling ``async_session()`` twice in one test trips an asyncpg
# "got Future attached to a different loop" RuntimeError.


class TestReadPermissionMigration:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_legacy_archives_read_renamed_to_all_for_administrators(self, async_client: AsyncClient):
        """Existing Administrators group with legacy `archives:read` → gets
        `archives:read_all` after seed_default_groups runs, and gets the
        `_own` companion backfilled too."""
        await seed_default_groups()
        await _strip_and_set("Administrators", extra=["archives:read"])

        await seed_default_groups()

        perms = await _get_perms("Administrators")
        # Rename happened: legacy renamed to _all
        assert "archives:read_all" in perms
        # Backfill also added _own so fresh install and upgraded install match
        assert "archives:read_own" in perms

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_administrators_backfill_adds_all_six_read_flags(self, async_client: AsyncClient):
        """Even with NO legacy flags present, Administrators ends up with both
        OWN and ALL variants for archives / library / queue after the backfill
        pass. This is the case Maziggy hit — admin missing `queue:read_own`
        after upgrade."""
        await seed_default_groups()
        await _strip_and_set("Administrators")

        await seed_default_groups()

        perms = await _get_perms("Administrators")
        for needed in (
            "archives:read_own",
            "archives:read_all",
            "library:read_own",
            "library:read_all",
            "queue:read_own",
            "queue:read_all",
        ):
            assert needed in perms, f"{needed} must be backfilled for Administrators"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operators_backfill_adds_own_read_flags(self, async_client: AsyncClient):
        """Operators with no read flags get the _OWN variants backfilled
        (fail-closed — no _ALL)."""
        await seed_default_groups()
        await _strip_and_set("Operators")

        await seed_default_groups()

        perms = await _get_perms("Operators")
        assert "archives:read_own" in perms
        assert "library:read_own" in perms
        assert "queue:read_own" in perms
        assert "archives:read_all" not in perms
        assert "library:read_all" not in perms
        assert "queue:read_all" not in perms

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operators_legacy_archives_read_renamed_to_own(self, async_client: AsyncClient):
        """Pre-PR Operators with legacy `archives:read` get the _OWN rename
        (fail-closed — close the IDOR, the operator can re-request _ALL via
        admin if cross-user visibility is genuinely needed)."""
        await seed_default_groups()
        await _strip_and_set("Operators", extra=["archives:read"])

        await seed_default_groups()

        perms = await _get_perms("Operators")
        assert "archives:read_own" in perms
        assert "archives:read_all" not in perms

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_administrators_legacy_archives_read_retained(self, async_client: AsyncClient):
        """Admin keeps the LEGACY `archives:read` flag — the frontend gates
        download / preview UI on it (ArchivesPage / FileManagerPage), and
        removing it on rename was leaving admin with no visible download
        buttons after upgrade. The new API gates use the _ALL variant which
        the backfill also ensures is present."""
        await seed_default_groups()
        await _strip_and_set("Administrators", extra=["archives:read"])

        await seed_default_groups()

        perms = await _get_perms("Administrators")
        # Both the legacy flag (for the UI) and the _all variant (for the API)
        # must coexist on admin.
        assert "archives:read" in perms
        assert "archives:read_all" in perms
        assert "archives:read_own" in perms

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_administrators_backfill_adds_legacy_read_flags(self, async_client: AsyncClient):
        """Admin with NO read flags at all (hand-edited or stripped role) ends
        up with the legacy `archives:read` / `queue:read` / `library:read`
        backfilled — so the UI gates work — alongside the OWN/ALL split."""
        await seed_default_groups()
        await _strip_and_set("Administrators")

        await seed_default_groups()

        perms = await _get_perms("Administrators")
        for needed in (
            "archives:read",
            "library:read",
            "queue:read",
            "archives:read_own",
            "archives:read_all",
            "library:read_own",
            "library:read_all",
            "queue:read_own",
            "queue:read_all",
        ):
            assert needed in perms, f"{needed} must be backfilled for Administrators"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_administrators_orca_cloud_auth_backfilled(self, async_client: AsyncClient):
        """Admin without `orca_cloud:auth` (older custom edit) gets it
        backfilled — matches the fresh-install default."""
        await seed_default_groups()
        async with _database_module.async_session() as session:
            grp = (await session.execute(select(Group).where(Group.name == "Administrators"))).scalar_one()
            grp.permissions = [p for p in (grp.permissions or []) if p != "orca_cloud:auth"]
            await session.commit()

        await seed_default_groups()

        perms = await _get_perms("Administrators")
        assert "orca_cloud:auth" in perms

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operators_orca_cloud_auth_backfilled(self, async_client: AsyncClient):
        """Operators on upgraded installs get `orca_cloud:auth` backfilled
        (the new default — needed for the Slice modal's Orca Cloud preset
        picker)."""
        await seed_default_groups()
        async with _database_module.async_session() as session:
            grp = (await session.execute(select(Group).where(Group.name == "Operators"))).scalar_one()
            grp.permissions = [p for p in (grp.permissions or []) if p != "orca_cloud:auth"]
            await session.commit()

        await seed_default_groups()

        perms = await _get_perms("Operators")
        assert "orca_cloud:auth" in perms

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_viewers_do_not_get_orca_cloud_auth(self, async_client: AsyncClient):
        """Viewers stay read-only — orca_cloud:auth is not added by the
        backfill (matches the fresh-install Viewers bootstrap, which
        intentionally excludes cloud-auth permissions)."""
        await seed_default_groups()
        async with _database_module.async_session() as session:
            grp = (await session.execute(select(Group).where(Group.name == "Viewers"))).scalar_one()
            grp.permissions = [p for p in (grp.permissions or []) if p != "orca_cloud:auth"]
            await session.commit()

        await seed_default_groups()

        perms = await _get_perms("Viewers")
        assert "orca_cloud:auth" not in perms
