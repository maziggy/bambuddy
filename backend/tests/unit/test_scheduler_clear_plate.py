"""Tests for the clear plate queue flow in the print scheduler."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.printer_manager import PrinterManager


class TestPrinterManagerPlateCleared:
    """Test the plate-cleared flag management in PrinterManager."""

    @pytest.fixture
    def manager(self):
        return PrinterManager()

    def test_plate_cleared_initially_false(self, manager):
        """No printers should have plate cleared by default."""
        assert not manager.is_awaiting_plate_clear(1)
        assert not manager.is_awaiting_plate_clear(999)

    def test_set_plate_cleared(self, manager):
        """Setting plate cleared should make is_awaiting_plate_clear return True."""
        manager.set_awaiting_plate_clear(1, True)
        assert manager.is_awaiting_plate_clear(1)
        assert not manager.is_awaiting_plate_clear(2)

    def test_consume_plate_cleared(self, manager):
        """Consuming plate cleared should reset the flag."""
        manager.set_awaiting_plate_clear(1, True)
        assert manager.is_awaiting_plate_clear(1)
        manager.set_awaiting_plate_clear(1, False)
        assert not manager.is_awaiting_plate_clear(1)

    def test_consume_plate_cleared_idempotent(self, manager):
        """Consuming when not set should not raise."""
        manager.set_awaiting_plate_clear(1, False)  # Should not raise
        assert not manager.is_awaiting_plate_clear(1)

    def test_set_plate_cleared_multiple_printers(self, manager):
        """Plate cleared should be tracked per printer."""
        manager.set_awaiting_plate_clear(1, True)
        manager.set_awaiting_plate_clear(3, True)
        assert manager.is_awaiting_plate_clear(1)
        assert not manager.is_awaiting_plate_clear(2)
        assert manager.is_awaiting_plate_clear(3)

    def test_consume_only_affects_target_printer(self, manager):
        """Consuming plate cleared for one printer should not affect others."""
        manager.set_awaiting_plate_clear(1, True)
        manager.set_awaiting_plate_clear(2, True)
        manager.set_awaiting_plate_clear(1, False)
        assert not manager.is_awaiting_plate_clear(1)
        assert manager.is_awaiting_plate_clear(2)


class TestAwaitingPlateClearPersistence:
    """Verify the awaiting-plate-clear flag round-trips through the DB (#961)."""

    @pytest.mark.asyncio
    async def test_load_rehydrates_in_memory_set_from_db(self):
        """Printers flagged in DB must re-appear in the in-memory set on startup."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        # Ensure all models are imported so Base.metadata includes them
        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base
        from backend.app.models.printer import Printer

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        # Seed: two printers, one flagged awaiting, one not
        async with session_maker() as db:
            db.add_all(
                [
                    Printer(
                        id=1,
                        name="P1",
                        serial_number="S1",
                        ip_address="1.1.1.1",
                        access_code="x",
                        awaiting_plate_clear=True,
                    ),
                    Printer(
                        id=2,
                        name="P2",
                        serial_number="S2",
                        ip_address="2.2.2.2",
                        access_code="y",
                        awaiting_plate_clear=False,
                    ),
                ]
            )
            await db.commit()

        # Point the manager's session factory at our in-memory DB and load
        manager = PrinterManager()
        with patch("backend.app.core.database.async_session", session_maker):
            await manager.load_awaiting_plate_clear_from_db()

        assert manager.is_awaiting_plate_clear(1) is True
        assert manager.is_awaiting_plate_clear(2) is False
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_persist_writes_flag_to_db(self):
        """set_awaiting_plate_clear + _persist writes the flag to the DB row."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base
        from backend.app.models.printer import Printer

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            db.add(
                Printer(
                    id=1,
                    name="P1",
                    serial_number="S1",
                    ip_address="1.1.1.1",
                    access_code="x",
                    awaiting_plate_clear=False,
                )
            )
            await db.commit()

        manager = PrinterManager()
        with patch("backend.app.core.database.async_session", session_maker):
            await manager._persist_awaiting_plate_clear(1, True)

        async with session_maker() as db:
            row = (await db.execute(select(Printer).where(Printer.id == 1))).scalar_one()
            assert row.awaiting_plate_clear is True

        with patch("backend.app.core.database.async_session", session_maker):
            await manager._persist_awaiting_plate_clear(1, False)

        async with session_maker() as db:
            row = (await db.execute(select(Printer).where(Printer.id == 1))).scalar_one()
            assert row.awaiting_plate_clear is False

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_persist_missing_printer_does_not_raise(self):
        """Persisting for a non-existent printer should be a silent no-op."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        manager = PrinterManager()
        with patch("backend.app.core.database.async_session", session_maker):
            # Should not raise even though printer 999 does not exist
            await manager._persist_awaiting_plate_clear(999, True)

        await engine.dispose()


class TestSchedulerIdleCheckWithPlateCleared:
    """Test _is_printer_idle interactions with the awaiting-plate-clear flag (#961)."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_idle_state_is_idle(self, mock_pm, scheduler):
        """IDLE state with no awaiting flag → idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="IDLE")
        mock_pm.is_awaiting_plate_clear.return_value = False
        assert scheduler._is_printer_idle(1) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_running_state_not_idle(self, mock_pm, scheduler):
        """RUNNING state is never idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="RUNNING")
        mock_pm.is_awaiting_plate_clear.return_value = False
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_finish_state_not_idle_when_awaiting(self, mock_pm, scheduler):
        """FINISH + awaiting plate-clear ack → NOT idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_awaiting_plate_clear.return_value = True
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_finish_state_idle_when_acknowledged(self, mock_pm, scheduler):
        """FINISH with flag cleared → idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_awaiting_plate_clear.return_value = False
        assert scheduler._is_printer_idle(1) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_failed_state_not_idle_when_awaiting(self, mock_pm, scheduler):
        """FAILED + awaiting → NOT idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FAILED")
        mock_pm.is_awaiting_plate_clear.return_value = True
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_failed_state_idle_when_acknowledged(self, mock_pm, scheduler):
        """FAILED with flag cleared → idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FAILED")
        mock_pm.is_awaiting_plate_clear.return_value = False
        assert scheduler._is_printer_idle(1) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_idle_state_not_idle_when_awaiting_survives_power_cycle(self, mock_pm, scheduler):
        """Regression for #961: after Auto Off power-cycles the printer it boots into IDLE
        with no memory of the previous finish. The persisted awaiting flag must still gate
        the queue — IDLE + awaiting → NOT idle.
        """
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="IDLE")
        mock_pm.is_awaiting_plate_clear.return_value = True
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_disconnected_printer_not_idle(self, mock_pm, scheduler):
        mock_pm.is_connected.return_value = False
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_no_status_not_idle(self, mock_pm, scheduler):
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = None
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_finish_state_idle_when_require_plate_clear_disabled(self, mock_pm, scheduler):
        """FINISH is idle when require_plate_clear=False, regardless of awaiting flag."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_awaiting_plate_clear.return_value = True
        assert scheduler._is_printer_idle(1, require_plate_clear=False) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_failed_state_idle_when_require_plate_clear_disabled(self, mock_pm, scheduler):
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FAILED")
        mock_pm.is_awaiting_plate_clear.return_value = True
        assert scheduler._is_printer_idle(1, require_plate_clear=False) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_running_state_not_idle_even_when_require_plate_clear_disabled(self, mock_pm, scheduler):
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="RUNNING")
        mock_pm.is_awaiting_plate_clear.return_value = False
        assert scheduler._is_printer_idle(1, require_plate_clear=False) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_idle_state_unaffected_by_require_plate_clear(self, mock_pm, scheduler):
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="IDLE")
        mock_pm.is_awaiting_plate_clear.return_value = False
        assert scheduler._is_printer_idle(1, require_plate_clear=False) is True


class TestPlateGateDefaultsOffWhenUnset:
    """#1865: with no require_plate_clear row in the settings table, the plate-clear
    gate must default OFF — matching SettingsSchema.require_plate_clear (default False)
    and the frontend (toggle + card badge both treat a missing value as off). The
    scheduler previously read this setting with default=True, so on installs that had
    never saved the setting the gate stayed enforced while the UI showed it disabled —
    FINISH-state printers never dispatched and there was no UI control to clear the flag.
    """

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    async def test_get_bool_setting_honors_false_default_when_row_absent(self, scheduler):
        """_get_bool_setting must return the caller's default when the key has no row."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as db:
            # No Settings row seeded → the caller's default decides the value.
            assert await scheduler._get_bool_setting(db, "require_plate_clear", default=False) is False
        await engine.dispose()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_check_queue_reads_plate_clear_setting_with_default_false(self, mock_pm, scheduler):
        """The per-check read of require_plate_clear must pass default=False (#1865).

        Guards the exact regression: a True default here re-enabled the gate the
        schema/UI treat as off when no settings row exists.
        """
        scheduler._get_bool_setting = AsyncMock(return_value=False)
        scheduler._check_auto_drying = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []  # empty queue → early return

        with patch("backend.app.services.print_scheduler.async_session") as mock_session_ctx:
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await scheduler.check_queue()

        plate_calls = [
            c
            for c in scheduler._get_bool_setting.call_args_list
            if len(c.args) >= 2 and c.args[1] == "require_plate_clear"
        ]
        assert plate_calls, "check_queue did not read the require_plate_clear setting"
        assert plate_calls[0].kwargs.get("default") is False, (
            "require_plate_clear must be read with default=False to match the schema/UI (#1865)"
        )


class TestPlateGateEndToEnd:
    """#1865 end-to-end: chain the REAL settings DB read (_get_bool_setting) into the
    REAL idle/dispatch gate (_is_printer_idle) for every setting state, so the wiring
    the bug lived in (settings row/absence -> require_plate_clear -> dispatch gate) is
    verified without mocking the value under test.
    """

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    async def _read_setting(self, scheduler, row_value):
        """Build a real in-memory settings DB (optionally with a require_plate_clear
        row) and return what the scheduler's call site actually reads."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base
        from backend.app.models.settings import Settings

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as db:
            if row_value is not None:
                db.add(Settings(key="require_plate_clear", value=row_value))
                await db.commit()
            # Mirror the exact call site in check_queue (print_scheduler.py).
            value = await scheduler._get_bool_setting(db, "require_plate_clear", default=False)
        await engine.dispose()
        return value

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "row_value, expected_gate",
        [
            (None, False),  # fresh install / never saved -> #1865 case -> gate OFF
            ("false", False),  # explicitly disabled -> gate OFF
            ("true", True),  # explicitly enabled -> gate ON
            ("True", True),  # case-insensitive parse
        ],
    )
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_finish_awaiting_dispatch_eligibility_matches_setting(
        self, mock_pm, scheduler, row_value, expected_gate
    ):
        """A FINISH printer with the awaiting flag raised is dispatch-eligible IFF the
        gate is off. Reads the setting from a real DB, then feeds it to the real gate."""
        require_plate_clear = await self._read_setting(scheduler, row_value)
        assert require_plate_clear is expected_gate

        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_awaiting_plate_clear.return_value = True  # bed potentially fouled

        is_idle = scheduler._is_printer_idle(1, require_plate_clear)
        # Gate OFF -> idle (dispatches). Gate ON -> not idle (waits for ack).
        assert is_idle is (not expected_gate)

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_enabled_gate_releases_after_plate_cleared(self, mock_pm, scheduler):
        """With the setting explicitly ON, clearing the plate (awaiting -> False) must
        flip the FINISH printer to dispatch-eligible — the full block-then-release cycle."""
        require_plate_clear = await self._read_setting(scheduler, "true")
        assert require_plate_clear is True

        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")

        # Before ack: awaiting -> blocked.
        mock_pm.is_awaiting_plate_clear.return_value = True
        assert scheduler._is_printer_idle(1, require_plate_clear) is False

        # After "Mark plate as cleared" (route sets flag False): dispatch-eligible.
        mock_pm.is_awaiting_plate_clear.return_value = False
        assert scheduler._is_printer_idle(1, require_plate_clear) is True

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_running_never_idle_regardless_of_gate(self, mock_pm, scheduler):
        """Sanity: a RUNNING printer is never dispatch-eligible, gate on or off."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="RUNNING")
        mock_pm.is_awaiting_plate_clear.return_value = False
        for gate in (True, False):
            assert scheduler._is_printer_idle(1, gate) is False


class TestSchedulerQueueCheckLogging:
    """Test queue check logging when pending items are found (#374)."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_check_queue_logs_pending_items(self, mock_pm, scheduler, caplog):
        """Verify pending items are logged when found in check_queue."""
        mock_item = MagicMock()
        mock_item.id = 42
        mock_item.printer_id = 1
        mock_item.archive_id = 100
        mock_item.library_file_id = None
        mock_item.scheduled_time = None
        mock_item.manual_start = False
        mock_item.target_model = None

        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="RUNNING")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_item]

        with (
            patch("backend.app.services.print_scheduler.async_session") as mock_session_ctx,
            caplog.at_level(logging.INFO, logger="backend.app.services.print_scheduler"),
        ):
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler.check_queue()

        queue_logs = [r for r in caplog.records if "Queue check" in r.message]
        assert len(queue_logs) == 1
        assert "1 pending items" in queue_logs[0].message
        assert "42" in queue_logs[0].message  # item ID

    @pytest.mark.asyncio
    async def test_check_queue_no_log_when_empty(self, scheduler, caplog):
        """Verify no queue log when no pending items found."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        with (
            patch("backend.app.services.print_scheduler.async_session") as mock_session_ctx,
            caplog.at_level(logging.INFO, logger="backend.app.services.print_scheduler"),
        ):
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler.check_queue()

        queue_logs = [r for r in caplog.records if "Queue check" in r.message]
        assert len(queue_logs) == 0
