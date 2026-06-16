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


def _status(state: str, subtask_id: str | None = None, gcode_file: str | None = None):
    """Minimal stand-in for PrinterState — only the fields the watchdog reads."""
    return SimpleNamespace(state=state, subtask_id=subtask_id, gcode_file=gcode_file)


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
    async def test_h2d_finish_to_running_via_subtask_id_then_active_state(self, db_session):
        """Regression for #1078 (preserved through the two-phase rewrite for #1678):

        H2D keeps state=FINISH for ~50 s after accepting project_file, but
        subtask_id flips to our new submission_id almost immediately. The
        watchdog must NOT revert on the basis of state staying at FINISH —
        Phase A exits on the subtask_id advance, Phase B then keeps watching
        and exits SUCCESS as soon as the printer transitions to PREPARE /
        RUNNING within the longer Phase B window.
        """
        # First poll: state still FINISH, subtask_id advanced (Phase A → B).
        # Second poll: state has flipped to RUNNING (Phase B success).
        get_status = MagicMock(
            side_effect=[
                _status("FINISH", "NEW_SUBTASK_12345"),
                _status("RUNNING", "NEW_SUBTASK_12345"),
            ]
            + [_status("RUNNING", "NEW_SUBTASK_12345")] * 10,
        )
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
                phase_b_timeout=0.3,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "printing", (
                "Phase A exit on subtask_id advance + Phase B observing the "
                "active-state transition is the H2D success path — watchdog "
                "must keep the item 'printing' (#1078)"
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
            patch("backend.app.core.database.async_session", db_session),
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
    async def test_reverts_on_finish_to_idle_user_dismissed_prompt(self, db_session):
        """Regression for #1370: when pre_state is FINISH and the printer
        transitions to IDLE during the watchdog window, that's the user
        dismissing a post-print prompt — NOT acceptance of our project_file.

        The bundle in #1370 showed exactly this: queue item dispatched while
        printer was in FINISH (residual from a previous print), command sent
        but silently rejected by firmware, then the user manually cleared
        the screen prompt so the printer moved to IDLE. The original
        ``state != pre_state`` check returned early on this transition and
        the queue row was left stuck in 'printing' indefinitely, blocking
        all future dispatches to that printer.

        The watchdog now only treats transitions into the active-print
        state set (PREPARE / SLICING / RUNNING / PAUSE) as a valid "command
        landed" signal.
        """
        get_status = MagicMock(return_value=_status("IDLE", "OLD_SUBTASK"))
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
            patch("backend.app.core.database.async_session", db_session),
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
            assert item.status == "pending", (
                "FINISH -> IDLE is the user dismissing a screen prompt, not "
                "the printer accepting project_file — item must be reverted "
                "to 'pending' so the scheduler can retry (#1370)"
            )
            assert item.started_at is None

    @pytest.mark.asyncio
    async def test_does_not_revert_on_pickup_via_active_state(self, db_session):
        """Counterpart to the #1370 fix: transitions into the active-print
        state set ARE a valid "command landed" signal. PREPARE / SLICING /
        RUNNING / PAUSE all keep the item in 'printing'.
        """
        for active_state in ("PREPARE", "SLICING", "RUNNING", "PAUSE"):
            async with db_session() as db:
                item = await db.get(PrintQueueItem, 1)
                item.status = "printing"
                item.started_at = None
                await db.commit()

            get_status = MagicMock(return_value=_status(active_state, "OLD_SUBTASK"))
            with (
                patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
                patch("backend.app.services.print_scheduler.async_session", db_session),
                patch("backend.app.core.database.async_session", db_session),
            ):
                await PrintScheduler._watchdog_print_start(
                    queue_item_id=1,
                    printer_id=42,
                    pre_state="IDLE",
                    pre_subtask_id="OLD_SUBTASK",
                    timeout=0.2,
                    poll_interval=0.05,
                )

            async with db_session() as db:
                item = await db.get(PrintQueueItem, 1)
                assert item.status == "printing", (
                    f"transition IDLE -> {active_state} must be treated as a "
                    f"valid 'command landed' signal — watchdog must not revert"
                )

    @pytest.mark.asyncio
    async def test_default_timeout_is_90_seconds(self):
        """The default timeout must cover slow H2D FINISH→PREPARE transitions
        (~50 s observed). A 45 s default would trip on the exact scenario the
        subtask_id check is guarding against, leaving no fallback for printers
        that don't echo subtask_id."""
        import inspect

        sig = inspect.signature(PrintScheduler._watchdog_print_start)
        assert sig.parameters["timeout"].default == 90.0

    @pytest.mark.asyncio
    async def test_default_phase_b_timeout_is_180_seconds(self):
        """Phase B (subtask_id advanced, waiting for active state) must
        comfortably exceed the H2D FINISH→PREPARE delay (~50 s observed)
        before declaring a printer-side wedge. 180 s gives ~3.5× headroom
        and reverts the queue item in well under the previous 2-hour
        expected_print TTL (#1678)."""
        import inspect

        sig = inspect.signature(PrintScheduler._watchdog_print_start)
        assert sig.parameters["phase_b_timeout"].default == 180.0

    @pytest.mark.asyncio
    async def test_reverts_when_subtask_advanced_but_state_never_active(self, db_session):
        """Regression for #1678: P1S on old firmware, power-cycled mid-print,
        cloud+LAN re-auth dance in flight. Printer accepts project_file
        (gcode_file updates, subtask_id advances to our submission id) but
        never transitions from IDLE/FINISH to PREPARE/RUNNING. The pre-fix
        watchdog returned SUCCESS as soon as subtask_id advanced and the
        queue item stayed in 'printing' until container restart. Phase B now
        keeps watching; if the active-state transition never arrives, the
        item reverts to 'pending' so the user can retry without restarting.
        """
        get_status = MagicMock(
            return_value=_status("IDLE", "NEW_SUBTASK_12345", gcode_file="/new.3mf"),
        )
        client = MagicMock()  # NOT None — must verify reconnect isn't called
        get_client = MagicMock(return_value=client)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
            patch("backend.app.core.database.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="IDLE",
                pre_subtask_id="OLD_SUBTASK_99999",
                pre_gcode_file="/old.3mf",
                timeout=0.2,
                phase_b_timeout=0.2,
                poll_interval=0.05,
            )

        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "pending", (
                "subtask_id advanced (Phase A → B) but state never reached an "
                "active value — printer-side wedge; the queue item must be "
                "reverted to 'pending' (#1678)"
            )
            assert item.started_at is None

        # File landed (subtask_id advance proves this), so a forced reconnect
        # would trigger 0500_4003 mid-parse (#1150) — skip.
        client.force_reconnect_stale_session.assert_not_called()


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
            patch("backend.app.core.database.async_session", db_session),
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
            patch("backend.app.core.database.async_session", db_session),
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
        ended up in — #967 race guard. Additionally it must NOT run the MQTT
        session-recovery path (forced reconnect): when on_print_complete has
        already moved the row, the print clearly landed on the printer and a
        forced reconnect on a healthy session would break ongoing prints on
        the same printer.
        """
        # Move item on to "completed" before the watchdog fires.
        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            item.status = "completed"
            await db.commit()

        get_status = MagicMock(return_value=_status("FINISH", "OLD_SUBTASK"))
        client = MagicMock()  # NOT None — must verify reconnect isn't called
        get_client = MagicMock(return_value=client)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
            patch("backend.app.core.database.async_session", db_session),
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

        client.force_reconnect_stale_session.assert_not_called()


class TestGcodeFileDiscriminator:
    """#1150 vs #887/#936: skip the forced reconnect when gcode_file changed
    (project_file landed, slow parse — reconnecting causes 0500_4003).
    Reconnect when gcode_file is unchanged (publish dropped — half-broken
    session needs the original recovery)."""

    @pytest.mark.asyncio
    async def test_skips_reconnect_when_gcode_file_changed(self, db_session):
        get_status = MagicMock(
            return_value=_status("FINISH", "OLD_SUBTASK", gcode_file="/new.3mf"),
        )
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
            patch("backend.app.core.database.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                pre_gcode_file="/old.3mf",
                timeout=0.2,
                poll_interval=0.05,
            )

        # Item still reverts (the user-facing failure stays correct), but the
        # MQTT session is left intact so the slow printer can finish parsing.
        async with db_session() as db:
            item = await db.get(PrintQueueItem, 1)
            assert item.status == "pending"
        client.force_reconnect_stale_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnects_when_gcode_file_unchanged(self, db_session):
        get_status = MagicMock(
            return_value=_status("FINISH", "OLD_SUBTASK", gcode_file="/old.3mf"),
        )
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
            patch("backend.app.services.print_scheduler.printer_manager.get_client", get_client),
            patch("backend.app.services.print_scheduler.async_session", db_session),
            patch("backend.app.core.database.async_session", db_session),
        ):
            await PrintScheduler._watchdog_print_start(
                queue_item_id=1,
                printer_id=42,
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                pre_gcode_file="/old.3mf",
                timeout=0.2,
                poll_interval=0.05,
            )

        client.force_reconnect_stale_session.assert_called_once()
