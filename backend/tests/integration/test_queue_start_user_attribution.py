"""Regression tests for #1670: queue manual-start path lost user attribution.

Before the fix, a VP-uploaded queue item (created over FTP, so unattributed)
that was then started by an authenticated user via the `/start` button would
land in the PrintLogEntry table with `created_by_username = NULL` because
the scheduler dispatch path never set `current_print_user` and the `/start`
route didn't record the clicker.

The fix is two-sided:
  - `POST /queue/{id}/start` credits the clicker as `created_by_id` when
    no prior owner is set (does NOT overwrite an existing owner — a
    UI-added queue item's original uploader keeps attribution).
  - `PrintScheduler._start_print` propagates `item.created_by_id` into
    `printer_manager.set_current_print_user` so the print-complete callback
    can write the username into the PrintLogEntry row.

These tests pin both halves so a future refactor can't silently regress
either one back to "blank User column."
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.print_queue import PrintQueueItem


async def _read_item(test_engine, item_id: int) -> PrintQueueItem:
    """Fresh-session DB read. The `db_session` fixture's connection can
    look stale after a route call dispatches through its own session via
    `Depends(get_db)`, so verification reads use a new session against the
    same engine."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        return (await fresh.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


async def _enable_auth_with_admin(async_client: AsyncClient) -> tuple[str, dict]:
    """Boot the app's auth setup and return (admin_token, admin_user)."""
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "queue1670admin",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "queue1670admin", "password": "AdminPass1!"},
    )
    body = login.json()
    return body["access_token"], body["user"]


@pytest.fixture
async def queue_item(db_session):
    """A pending, manual-start, UNATTRIBUTED queue item — mirrors what the
    VP-queue path produces (FTP upload has no user, manual_start is the
    Queue-mode default)."""
    from backend.app.models.archive import PrintArchive
    from backend.app.models.printer import Printer

    printer = Printer(
        name="P2S Test",
        ip_address="192.168.2.201",
        serial_number="00M00A1234567890",
        access_code="12345678",
        model="P2S",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)

    archive = PrintArchive(
        filename="Plate_1.gcode.3mf",
        print_name="Plate 1",
        file_path="/tmp/queue1670_plate.3mf",  # nosec B108
        file_size=1024,
        content_hash="queue1670hash",
        status="completed",
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)

    item = PrintQueueItem(
        printer_id=printer.id,
        archive_id=archive.id,
        status="pending",
        position=1,
        manual_start=True,
        created_by_id=None,  # unattributed — VP-queue shape
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


class TestStartCreditsTheClicker:
    """`/start` writes the clicker's id to `created_by_id` when none was set."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_writes_created_by_id_when_unattributed(
        self, async_client: AsyncClient, test_engine, queue_item
    ):
        admin_token, admin_user = await _enable_auth_with_admin(async_client)

        response = await async_client.post(
            f"/api/v1/queue/{queue_item.id}/start",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

        refreshed = await _read_item(test_engine, queue_item.id)
        assert refreshed.created_by_id == admin_user["id"]
        assert refreshed.manual_start is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_preserves_existing_owner(self, async_client: AsyncClient, db_session, test_engine, queue_item):
        """A queue item that was created by user A and then started by user B
        keeps user A's attribution — the original uploader's claim is stronger
        than the dispatcher's. (Matches the standard ownership semantics in
        `auth.py::require_ownership_permission`.)"""
        admin_token, admin_user = await _enable_auth_with_admin(async_client)

        # Pre-set a different owner on the queue item using a fresh session
        # (the test's `db_session` is detached from the route's session pool).
        prior_owner_id = admin_user["id"] + 9999
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as fresh:
            item = (await fresh.execute(select(PrintQueueItem).where(PrintQueueItem.id == queue_item.id))).scalar_one()
            item.created_by_id = prior_owner_id
            await fresh.commit()

        response = await async_client.post(
            f"/api/v1/queue/{queue_item.id}/start",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

        refreshed = await _read_item(test_engine, queue_item.id)
        # Prior owner survives — `/start` did not promote the clicker.
        assert refreshed.created_by_id == prior_owner_id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_with_auth_disabled_leaves_created_by_id_null(
        self, async_client: AsyncClient, test_engine, queue_item
    ):
        """When auth is off the route's user dep returns None — the item stays
        unattributed (no synthetic 'system' user invented). Regression guard
        in case a future refactor accidentally invents a placeholder user id."""
        response = await async_client.post(f"/api/v1/queue/{queue_item.id}/start")
        assert response.status_code == 200

        refreshed = await _read_item(test_engine, queue_item.id)
        assert refreshed.created_by_id is None


class TestSchedulerPropagatesOwnerToPrinterManager:
    """`PrintScheduler._propagate_owner_to_printer_manager` looks up the
    user row by `created_by_id` and forwards it into
    `printer_manager.set_current_print_user` so the print-complete callback
    can write the username into PrintLogEntry."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_propagates_when_created_by_id_resolves_to_user(self, db_session, queue_item, monkeypatch):
        from backend.app.models.user import User
        from backend.app.services import print_scheduler as scheduler_module
        from backend.app.services.print_scheduler import PrintScheduler

        user = User(username="clickeruser", password_hash="x", is_active=True)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        queue_item.created_by_id = user.id
        db_session.add(queue_item)
        await db_session.commit()
        await db_session.refresh(queue_item)

        captured: list[tuple[int, int, str]] = []
        monkeypatch.setattr(
            scheduler_module.printer_manager,
            "set_current_print_user",
            lambda printer_id, uid, username: captured.append((printer_id, uid, username)),
        )

        await PrintScheduler()._propagate_owner_to_printer_manager(db_session, queue_item)

        assert captured == [(queue_item.printer_id, user.id, "clickeruser")]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_noop_when_created_by_id_is_none(self, db_session, queue_item, monkeypatch):
        """VP-uploaded queue items that never got manual-started (e.g.
        auto-dispatch) carry no owner — the helper must stay silent rather
        than synthesise a placeholder user."""
        from backend.app.services import print_scheduler as scheduler_module
        from backend.app.services.print_scheduler import PrintScheduler

        assert queue_item.created_by_id is None

        captured: list = []
        monkeypatch.setattr(
            scheduler_module.printer_manager,
            "set_current_print_user",
            lambda *args: captured.append(args),
        )

        await PrintScheduler()._propagate_owner_to_printer_manager(db_session, queue_item)
        assert captured == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_noop_when_user_row_missing(self, db_session, queue_item, monkeypatch):
        """`created_by_id` points at a user that's since been deleted —
        helper must not crash the dispatch. The print log row will just be
        un-credited for this run, same as auth-disabled."""
        from backend.app.services import print_scheduler as scheduler_module
        from backend.app.services.print_scheduler import PrintScheduler

        queue_item.created_by_id = 999_999  # no such user row
        db_session.add(queue_item)
        await db_session.commit()
        await db_session.refresh(queue_item)

        captured: list = []
        monkeypatch.setattr(
            scheduler_module.printer_manager,
            "set_current_print_user",
            lambda *args: captured.append(args),
        )

        # Must not raise — the dispatch loop would otherwise lose the whole
        # queue item to an exception trace for what's effectively a missing
        # foreign key.
        await PrintScheduler()._propagate_owner_to_printer_manager(db_session, queue_item)
        assert captured == []
