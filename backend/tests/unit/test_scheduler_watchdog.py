"""Regression tests for ``_watchdog_print_start``.

The watchdog reverts queue items to ``pending`` when a dispatched print never
lands on the printer (half-broken MQTT session — #887/#936/#967). H2D firmware
can sit at ``FINISH`` for 50+ seconds after accepting a ``project_file``
command before flipping ``gcode_state`` to ``PREPARE``, which used to trip the
state-only watchdog and cause the scheduler to revert the item; the subsequent
successful dispatch then looked like a reprint of the just-finished job (#1078).

The fix: treat ``subtask_id`` advancing past the pre-dispatch value as an
equivalent "command landed" signal, and raise the timeout from 45 s to 90 s as
belt-and-braces for slow transitions that also don't emit an early subtask_id
tick.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def db_session():
    """In-memory SQLite with one ``printing`` queue item at id=1."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import backend.app.models  # noqa: F401  — populate Base.metadata
    from backend.app.core.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add(PrintQueueItem(id=1, printer_id=42, archive_id=99, status="printing"))
        await db.commit()

    try:
        yield session_maker
    finally:
        await engine.dispose()


def _status(state: str, subtask_id: str | None = None):
    """Minimal stand-in for PrinterState — only the two fields the watchdog reads."""
    return SimpleNamespace(state=state, subtask_id=subtask_id)


class TestWatchdogExitsEarlyOnPickup:
    """The watchdog must NOT revert when the printer has clearly picked up the job."""

    @pytest.mark.asyncio
    async def test_exits_on_state_change(self, db_session):
        """State transitioning away from pre_state is the primary "accepted" signal."""
        get_status = MagicMock(return_value=_status("RUNNING", "OLD_SUBTASK"))
        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.3,
                poll_interval=0.05,
            )

        # Item should remain "printing" — watchdog recognised the pickup.
        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "printing"

    @pytest.mark.asyncio
    async def test_exits_on_subtask_id_change_even_if_state_still_finish(self, db_session):
        """Regression for #1078: H2D keeps state=FINISH for ~50 s after accepting
        project_file, but subtask_id flips to our new submission_id almost
        immediately. That must short-circuit the revert."""
        get_status = MagicMock(return_value=_status("FINISH", "NEW_SUBTASK_12345"))
        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK_99999",
                timeout=0.3,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "printing", (
                "subtask_id advanced past pre_subtask_id — the printer accepted our "
                "project_file and the watchdog must not revert the queue item even "
                "though state is still FINISH (#1078)"
            )


class TestWatchdogRevertsWhenStuck:
    """Genuine half-broken sessions still need the revert + reconnect recovery."""

    @pytest.mark.asyncio
    async def test_reverts_when_neither_state_nor_subtask_id_changes(self, db_session):
        """Both signals unchanged across the full timeout → revert to pending
        and force MQTT reconnect (the #967 recovery path)."""
        get_status = MagicMock(return_value=_status("FINISH", "OLD_SUBTASK"))
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "pending"
            assert item.started_at is None

        client.force_reconnect_stale_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_timeout_is_90_seconds(self):
        """The default timeout must cover slow H2D FINISH→PREPARE transitions
        (~50 s observed). A 45 s default would trip on the exact scenario the
        subtask_id check is guarding against, leaving no fallback for printers
        that don't echo subtask_id."""
        import inspect

        sig = inspect.signature(PrintScheduler._watchdog_print_start)
        assert sig.parameters["timeout"].default == 90.0


class TestWatchdogFallbackBehaviour:
    """Backwards-compat and defensive behaviour around missing data."""

    @pytest.mark.asyncio
    async def test_pre_subtask_id_none_falls_back_to_state_only(self, db_session):
        """When we never captured a pre-dispatch subtask_id (e.g. printer just
        connected), the watchdog must still work on the state signal alone —
        and still revert when state stays unchanged, so half-broken sessions
        are still recovered."""
        get_status = MagicMock(return_value=_status("FINISH", "SOMETHING"))
        get_client = MagicMock(return_value=None)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id=None,
                timeout=0.2,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "pending"

    @pytest.mark.asyncio
    async def test_current_subtask_id_none_does_not_trigger_early_exit(self, db_session):
        """If the printer transiently reports subtask_id=None (e.g. during
        reconnect), that must not be treated as "changed" — otherwise the
        watchdog would exit early without a real pickup signal and leave the
        item stuck in "printing" after a genuinely broken session."""
        get_status = MagicMock(return_value=_status("FINISH", None))
        get_client = MagicMock(return_value=None)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "pending"

    @pytest.mark.asyncio
    async def test_printer_disconnected_returns_without_reverting(self, db_session):
        """If the printer drops during the watchdog window, don't touch the DB —
        the reconnect path will sort the queue state out."""
        get_status = MagicMock(return_value=None)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "printing"

    @pytest.mark.asyncio
    async def test_no_revert_if_item_already_completed(self, db_session):
        """If the print completed between watchdog arm-time and timeout (item is
        no longer "printing"), the watchdog must not clobber whatever status it
        ended up in — #967 race guard."""
        # Move item on to "completed" before the watchdog fires.
        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            item.status = "completed"
            await db.commit()

        get_status = MagicMock(return_value=_status("FINISH", "OLD_SUBTASK"))
        get_client = MagicMock(return_value=None)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "completed"  # untouched
