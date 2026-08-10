"""Regression tests: queue items created outside POST /queue/ lost their owner.

`PrintQueueItem.created_by_id` is what the `queue:read_own` / `queue:update_own` /
`queue:delete_own` permissions filter on (`api/routes/print_queue.py`). Two
creation paths never set it, so the rows they produced were ownerless:

  - `POST /library/files/add-to-queue` — the bulk "Add to queue" action on the
    Library page. The route already required `Permission.QUEUE_CREATE` but bound
    the dependency to `_` and threw the user away, so a non-admin who queued
    files from the Library could not then see them in their own queue.
  - `POST /webhook/queue/add` — API-key inbound. `APIKey.user_id` records the
    key's owner, which is the acting identity for everything else the key does.

Ownerless rows are still legitimate for callers with no user behind them (auth
disabled, virtual-printer FTP uploads, legacy keys minted before per-user
ownership), so the tests pin those cases too — the fix must not invent a
placeholder user id. Compare `test_queue_start_user_attribution.py`, which pins
the same NULL-is-meaningful contract on the `/start` path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.auth import generate_api_key
from backend.app.core.config import settings as app_settings
from backend.app.models.api_key import APIKey
from backend.app.models.group import Group
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.user import User


async def _read_item(test_engine, item_id: int) -> PrintQueueItem:
    """Fresh-session DB read — the `db_session` fixture's connection can look
    stale after a route call dispatches through its own `Depends(get_db)`
    session. Same helper shape as test_queue_start_user_attribution.py."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        return (await fresh.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


async def _enable_auth_with_admin(client: AsyncClient, username: str) -> tuple[str, dict]:
    """Boot auth setup and return (bearer_token, user_dict)."""
    await client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": username,
            "admin_password": "AdminPass1!",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "AdminPass1!"},
    )
    body = login.json()
    return body["access_token"], body["user"]


@pytest.fixture
async def sliced_library_file(db_session):
    """A library file that passes both of add-to-queue's gates: the filename
    must look sliced, and the bytes must actually exist under `base_dir` (the
    route rejects rows whose file is missing from disk)."""
    from backend.app.models.library import LibraryFile

    rel_path = "archive/library/files/attribution_probe.gcode.3mf"
    abs_path = Path(app_settings.base_dir) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"probe")

    lib_file = LibraryFile(
        filename="attribution_probe.gcode.3mf",
        file_path=rel_path,
        file_size=5,
        file_type="3mf",
    )
    db_session.add(lib_file)
    await db_session.commit()
    await db_session.refresh(lib_file)

    yield lib_file

    abs_path.unlink(missing_ok=True)


class TestLibraryAddToQueueAttribution:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_credits_the_authenticated_user(self, async_client: AsyncClient, test_engine, sliced_library_file):
        """The Library bulk-add is the path a user reaches for when queueing
        many files at once — exactly the case where losing attribution hurts."""
        token, user = await _enable_auth_with_admin(async_client, "libqueueadmin")

        response = await async_client.post(
            "/api/v1/library/files/add-to-queue",
            json={"file_ids": [sliced_library_file.id]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        added = response.json()["added"]
        assert len(added) == 1

        item = await _read_item(test_engine, added[0]["queue_item_id"])
        assert item.created_by_id == user["id"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auth_disabled_leaves_item_ownerless(
        self, async_client: AsyncClient, test_engine, sliced_library_file
    ):
        """With auth off the permission dep yields None. The row must stay
        NULL rather than gaining a synthetic owner."""
        response = await async_client.post(
            "/api/v1/library/files/add-to-queue",
            json={"file_ids": [sliced_library_file.id]},
        )
        assert response.status_code == 200
        added = response.json()["added"]
        assert len(added) == 1

        item = await _read_item(test_engine, added[0]["queue_item_id"])
        assert item.created_by_id is None


class TestWebhookQueueAddAttribution:
    @pytest.fixture
    async def printer_and_archive(self, db_session):
        from backend.app.models.archive import PrintArchive
        from backend.app.models.printer import Printer

        printer = Printer(
            name="Webhook Target",
            ip_address="192.168.2.202",
            serial_number="00M00A9876543210",
            access_code="12345678",
            model="P1S",
        )
        archive = PrintArchive(
            filename="Plate_1.gcode.3mf",
            print_name="Plate 1",
            file_path="/tmp/webhook_attribution.3mf",  # nosec B108
            file_size=1024,
            content_hash="webhookattributionhash",
            status="completed",
        )
        db_session.add_all([printer, archive])
        await db_session.commit()
        await db_session.refresh(printer)
        await db_session.refresh(archive)
        return printer, archive

    async def _mint_key(self, db_session, owner_id: int | None) -> str:
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(
                name="attribution probe",
                key_hash=key_hash,
                key_prefix=key_prefix,
                user_id=owner_id,
                can_queue=True,
            )
        )
        await db_session.commit()
        return full_key

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_credits_the_key_owner(self, async_client: AsyncClient, db_session, test_engine, printer_and_archive):
        printer, archive = printer_and_archive

        # The owner needs queue:create in their own right: a key is capped by
        # its owner's permissions (#1894), so a bare account with no groups
        # cannot queue through a key however its scope flags are set. This test
        # is about who the row is credited to, not about the gate.
        group = Group(name="queue-writers", description="t", permissions=["queue:create"], is_system=False)
        db_session.add(group)
        await db_session.flush()
        owner = User(username="keyowner", password_hash="x", is_active=True, groups=[group])
        db_session.add(owner)
        await db_session.commit()
        await db_session.refresh(owner)

        key = await self._mint_key(db_session, owner.id)

        response = await async_client.post(
            "/api/v1/webhook/queue/add",
            json={"printer_id": printer.id, "archive_id": archive.id},
            headers={"X-API-Key": key},
        )
        assert response.status_code == 200

        item = await _read_item(test_engine, response.json()["id"])
        assert item.created_by_id == owner.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_legacy_ownerless_key_leaves_item_ownerless(
        self, async_client: AsyncClient, db_session, test_engine, printer_and_archive
    ):
        """`APIKey.user_id` is nullable only for keys minted before per-user
        ownership existed. Those must produce an ownerless row, not a crash."""
        printer, archive = printer_and_archive
        key = await self._mint_key(db_session, None)

        response = await async_client.post(
            "/api/v1/webhook/queue/add",
            json={"printer_id": printer.id, "archive_id": archive.id},
            headers={"X-API-Key": key},
        )
        assert response.status_code == 200

        item = await _read_item(test_engine, response.json()["id"])
        assert item.created_by_id is None
