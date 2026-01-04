"""Unit tests for SmartPlugManager service.

These tests specifically target the auto-off behavior and toggle functionality
that were identified as common regression points.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.smart_plug_manager import SmartPlugManager


class TestSmartPlugManager:
    """Tests for SmartPlugManager class."""

    @pytest.fixture
    def manager(self):
        """Create a fresh SmartPlugManager instance."""
        return SmartPlugManager()

    @pytest.fixture
    def mock_plug(self):
        """Create a mock SmartPlug object."""
        plug = MagicMock()
        plug.id = 1
        plug.name = "Test Plug"
        plug.ip_address = "192.168.1.100"
        plug.username = None
        plug.password = None
        plug.enabled = True
        plug.auto_on = True
        plug.auto_off = True
        plug.off_delay_mode = "time"
        plug.off_delay_minutes = 5
        plug.off_temp_threshold = 70
        plug.printer_id = 1
        plug.auto_off_executed = False
        plug.auto_off_pending = False
        plug.last_state = "ON"
        plug.last_checked = None
        return plug

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    # ========================================================================
    # Tests for on_print_start
    # ========================================================================

    @pytest.mark.asyncio
    async def test_on_print_start_turns_on_plug(self, manager, mock_plug, mock_db):
        """Verify plug is turned ON when print starts with auto_on enabled."""
        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = mock_plug
            mock_tasmota.turn_on = AsyncMock(return_value=True)

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_tasmota.turn_on.assert_called_once_with(mock_plug)

    @pytest.mark.asyncio
    async def test_on_print_start_skipped_when_auto_on_disabled(self, manager, mock_plug, mock_db):
        """Verify plug is NOT turned on when auto_on is disabled."""
        mock_plug.auto_on = False

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = mock_plug
            mock_tasmota.turn_on = AsyncMock()

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_tasmota.turn_on.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_start_skipped_when_plug_disabled(self, manager, mock_plug, mock_db):
        """Verify plug is NOT turned on when plug.enabled is False."""
        mock_plug.enabled = False

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = mock_plug
            mock_tasmota.turn_on = AsyncMock()

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_tasmota.turn_on.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_start_skipped_when_no_plug_found(self, manager, mock_db):
        """Verify graceful handling when no plug is linked to printer."""
        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = None
            mock_tasmota.turn_on = AsyncMock()

            # Should not raise any exception
            await manager.on_print_start(printer_id=999, db=mock_db)

            mock_tasmota.turn_on.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_start_cancels_pending_off(self, manager, mock_plug, mock_db):
        """Verify starting a new print cancels any pending auto-off."""
        # Set up a pending task
        mock_task = MagicMock()
        manager._pending_off[mock_plug.id] = mock_task

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_mark_auto_off_pending", new_callable=AsyncMock),
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = mock_plug
            mock_tasmota.turn_on = AsyncMock(return_value=True)

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_task.cancel.assert_called_once()
            assert mock_plug.id not in manager._pending_off

    @pytest.mark.asyncio
    async def test_on_print_start_resets_auto_off_executed_flag(self, manager, mock_plug, mock_db):
        """Verify auto_off_executed flag is reset when turning on."""
        mock_plug.auto_off_executed = True

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = mock_plug
            mock_tasmota.turn_on = AsyncMock(return_value=True)

            await manager.on_print_start(printer_id=1, db=mock_db)

            assert mock_plug.auto_off_executed is False

    # ========================================================================
    # Tests for on_print_complete
    # ========================================================================

    @pytest.mark.asyncio
    async def test_on_print_complete_schedules_time_based_off(self, manager, mock_plug, mock_db):
        """Verify time-based auto-off is scheduled when print completes."""
        mock_plug.off_delay_mode = "time"
        mock_plug.off_delay_minutes = 5

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = mock_plug

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_called_once_with(mock_plug, 1, 300)  # 5 min * 60 sec

    @pytest.mark.asyncio
    async def test_on_print_complete_schedules_temp_based_off(self, manager, mock_plug, mock_db):
        """Verify temperature-based auto-off is scheduled when print completes."""
        mock_plug.off_delay_mode = "temperature"
        mock_plug.off_temp_threshold = 70

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_temp_based_off") as mock_schedule,
        ):
            mock_get_plug.return_value = mock_plug

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_called_once_with(mock_plug, 1, 70)

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_when_auto_off_disabled(self, manager, mock_plug, mock_db):
        """CRITICAL: Verify auto-off does NOT trigger when auto_off is False.

        This is a key regression test - the toggle must respect the setting.
        """
        mock_plug.auto_off = False

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
            patch.object(manager, "_schedule_temp_based_off") as mock_temp,
        ):
            mock_get_plug.return_value = mock_plug

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_not_called()
            mock_temp.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_when_plug_disabled(self, manager, mock_plug, mock_db):
        """Verify auto-off does NOT trigger when plug is disabled."""
        mock_plug.enabled = False

        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = mock_plug

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_on_failed_print(self, manager, mock_plug, mock_db):
        """Verify auto-off does NOT trigger on failed prints for investigation."""
        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = mock_plug

            await manager.on_print_complete(printer_id=1, status="failed", db=mock_db)

            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_on_aborted_print(self, manager, mock_plug, mock_db):
        """Verify auto-off does NOT trigger on aborted prints."""
        with (
            patch.object(manager, "_get_plug_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = mock_plug

            await manager.on_print_complete(printer_id=1, status="aborted", db=mock_db)

            mock_schedule.assert_not_called()

    # ========================================================================
    # Tests for _cancel_pending_off
    # ========================================================================

    @pytest.mark.asyncio
    async def test_cancel_pending_off_removes_task(self, manager, mock_plug):
        """Verify pending off tasks can be cancelled."""
        mock_task = MagicMock()
        manager._pending_off[mock_plug.id] = mock_task

        with patch.object(manager, "_mark_auto_off_pending", new_callable=AsyncMock):
            manager._cancel_pending_off(mock_plug.id)

        assert mock_plug.id not in manager._pending_off
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_pending_off_handles_missing_task(self, manager):
        """Verify no error when cancelling non-existent task."""
        # Should not raise any exception
        with patch.object(manager, "_mark_auto_off_pending", new_callable=AsyncMock):
            manager._cancel_pending_off(999)  # Non-existent plug ID

    @pytest.mark.asyncio
    async def test_cancel_all_pending(self, manager, mock_plug):
        """Verify all pending tasks can be cancelled."""
        mock_task1 = MagicMock()
        mock_task2 = MagicMock()
        manager._pending_off[1] = mock_task1
        manager._pending_off[2] = mock_task2

        with patch("asyncio.create_task"):
            manager.cancel_all_pending()

        assert len(manager._pending_off) == 0
        mock_task1.cancel.assert_called_once()
        mock_task2.cancel.assert_called_once()

    # ========================================================================
    # Tests for scheduler
    # ========================================================================

    def test_start_scheduler(self, manager):
        """Verify scheduler can be started."""
        assert manager._scheduler_task is None

        # Mock _schedule_loop to return a mock coroutine to avoid unawaited coroutine warning
        with patch.object(manager, "_schedule_loop") as mock_loop, patch("asyncio.create_task") as mock_create:
            mock_create.return_value = MagicMock()
            manager.start_scheduler()

            assert manager._scheduler_task is not None
            mock_loop.assert_called_once()

    def test_stop_scheduler(self, manager):
        """Verify scheduler can be stopped."""
        mock_task = MagicMock()
        manager._scheduler_task = mock_task

        manager.stop_scheduler()

        mock_task.cancel.assert_called_once()
        assert manager._scheduler_task is None

    def test_start_scheduler_idempotent(self, manager):
        """Verify starting scheduler twice doesn't create multiple tasks."""
        mock_task = MagicMock()
        manager._scheduler_task = mock_task

        # Mock _schedule_loop to avoid unawaited coroutine warning (in case it's called)
        with patch.object(manager, "_schedule_loop") as mock_loop, patch("asyncio.create_task") as mock_create:
            manager.start_scheduler()

            mock_create.assert_not_called()  # Should not create new task
            mock_loop.assert_not_called()  # Should not call _schedule_loop


class TestScheduleLoop:
    """Tests for the schedule-based plug control."""

    @pytest.fixture
    def manager(self):
        return SmartPlugManager()

    @pytest.mark.asyncio
    async def test_check_schedules_turns_on_at_scheduled_time(self, manager):
        """Verify scheduled on-time turns plug on."""
        mock_plug = MagicMock()
        mock_plug.id = 1
        mock_plug.name = "Test Plug"
        mock_plug.enabled = True
        mock_plug.schedule_enabled = True
        mock_plug.schedule_on_time = "08:00"
        mock_plug.schedule_off_time = "22:00"
        mock_plug.printer_id = None
        mock_plug.last_state = "OFF"

        with (
            patch("backend.app.services.smart_plug_manager.datetime") as mock_datetime,
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            # Set current time to 08:00
            mock_now = MagicMock()
            mock_now.strftime.return_value = "08:00"
            mock_datetime.now.return_value = mock_now
            mock_datetime.utcnow.return_value = datetime.utcnow()

            # Set up async session mock
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.commit = AsyncMock()

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_on = AsyncMock(return_value=True)

            await manager._check_schedules()

            mock_tasmota.turn_on.assert_called_once_with(mock_plug)

    @pytest.mark.asyncio
    async def test_check_schedules_turns_off_at_scheduled_time(self, manager):
        """Verify scheduled off-time turns plug off."""
        mock_plug = MagicMock()
        mock_plug.id = 1
        mock_plug.name = "Test Plug"
        mock_plug.enabled = True
        mock_plug.schedule_enabled = True
        mock_plug.schedule_on_time = "08:00"
        mock_plug.schedule_off_time = "22:00"
        mock_plug.printer_id = 1
        mock_plug.last_state = "ON"

        with (
            patch("backend.app.services.smart_plug_manager.datetime") as mock_datetime,
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
        ):
            # Set current time to 22:00
            mock_now = MagicMock()
            mock_now.strftime.return_value = "22:00"
            mock_datetime.now.return_value = mock_now
            mock_datetime.utcnow.return_value = datetime.utcnow()

            # Set up async session mock
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.commit = AsyncMock()

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_off = AsyncMock(return_value=True)
            mock_pm.mark_printer_offline = MagicMock()

            await manager._check_schedules()

            mock_tasmota.turn_off.assert_called_once_with(mock_plug)

    @pytest.mark.asyncio
    async def test_check_schedules_skipped_when_disabled(self, manager):
        """Verify schedule is skipped when schedule_enabled is False."""
        mock_plug = MagicMock()
        mock_plug.id = 1
        mock_plug.enabled = True
        mock_plug.schedule_enabled = False  # Disabled

        with (
            patch("backend.app.services.smart_plug_manager.datetime") as mock_datetime,
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_now = MagicMock()
            mock_now.strftime.return_value = "08:00"
            mock_datetime.now.return_value = mock_now

            # Set up async session mock - returns no plugs (filtered by schedule_enabled)
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.commit = AsyncMock()

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_on = AsyncMock()

            await manager._check_schedules()

            mock_tasmota.turn_on.assert_not_called()


class TestPendingAutoOffPersistence:
    """Tests for auto-off pending state persistence (restart recovery)."""

    @pytest.fixture
    def manager(self):
        return SmartPlugManager()

    @pytest.mark.asyncio
    async def test_resume_pending_auto_offs_temperature_mode(self, manager):
        """Verify temperature-based pending auto-offs are resumed on startup."""
        mock_plug = MagicMock()
        mock_plug.id = 1
        mock_plug.name = "Test Plug"
        mock_plug.ip_address = "192.168.1.100"
        mock_plug.username = None
        mock_plug.password = None
        mock_plug.printer_id = 1
        mock_plug.auto_off_pending = True
        mock_plug.auto_off_pending_since = datetime.utcnow()
        mock_plug.off_delay_mode = "temperature"
        mock_plug.off_temp_threshold = 70

        with (
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch.object(manager, "_schedule_temp_based_off") as mock_schedule,
        ):
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            await manager.resume_pending_auto_offs()

            mock_schedule.assert_called_once_with(mock_plug, 1, 70)

    @pytest.mark.asyncio
    async def test_resume_pending_auto_offs_time_mode_immediate_off(self, manager):
        """Verify time-based pending auto-offs turn off immediately on resume."""
        mock_plug = MagicMock()
        mock_plug.id = 1
        mock_plug.name = "Test Plug"
        mock_plug.ip_address = "192.168.1.100"
        mock_plug.username = None
        mock_plug.password = None
        mock_plug.printer_id = 1
        mock_plug.auto_off_pending = True
        mock_plug.auto_off_pending_since = datetime.utcnow()
        mock_plug.off_delay_mode = "time"

        with (
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
            patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock) as mock_mark,
            patch("backend.app.services.smart_plug_manager.printer_manager"),
        ):
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_off = AsyncMock(return_value=True)

            await manager.resume_pending_auto_offs()

            mock_tasmota.turn_off.assert_called_once()
            mock_mark.assert_called_once_with(1)
