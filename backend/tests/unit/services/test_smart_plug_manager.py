"""Unit tests for SmartPlugManager service.

These tests specifically target the auto-off behavior and toggle functionality
that were identified as common regression points.
"""

import asyncio
from datetime import datetime, timezone
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
        # #1349: drying defaults match the new schema — both off until the
        # user opts in, so existing tests don't accidentally activate the
        # post-drying path.
        plug.plug_type = "tasmota"
        plug.ha_entity_id = None
        plug.auto_off_after_drying = False
        plug.off_delay_after_drying_minutes = 10
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
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = [mock_plug]
            mock_tasmota.turn_on = AsyncMock(return_value=True)

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_tasmota.turn_on.assert_called_once_with(mock_plug)

    @pytest.mark.asyncio
    async def test_on_print_start_skipped_when_auto_on_disabled(self, manager, mock_plug, mock_db):
        """Verify plug is NOT turned on when auto_on is disabled."""
        mock_plug.auto_on = False

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = [mock_plug]
            mock_tasmota.turn_on = AsyncMock()

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_tasmota.turn_on.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_start_skipped_when_plug_disabled(self, manager, mock_plug, mock_db):
        """Verify plug is NOT turned on when plug.enabled is False."""
        mock_plug.enabled = False

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = [mock_plug]
            mock_tasmota.turn_on = AsyncMock()

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_tasmota.turn_on.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_start_skipped_when_no_plug_found(self, manager, mock_db):
        """Verify graceful handling when no plug is linked to printer."""
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = []
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
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_mark_auto_off_pending", new_callable=AsyncMock),
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = [mock_plug]
            mock_tasmota.turn_on = AsyncMock(return_value=True)

            await manager.on_print_start(printer_id=1, db=mock_db)

            mock_task.cancel.assert_called_once()
            assert mock_plug.id not in manager._pending_off

    @pytest.mark.asyncio
    async def test_on_print_start_resets_auto_off_executed_flag(self, manager, mock_plug, mock_db):
        """Verify auto_off_executed flag is reset when turning on."""
        mock_plug.auto_off_executed = True

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_get_plug.return_value = [mock_plug]
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
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_called_once_with(mock_plug, 1, 300)  # 5 min * 60 sec

    @pytest.mark.asyncio
    async def test_on_print_complete_schedules_temp_based_off(self, manager, mock_plug, mock_db):
        """Verify temperature-based auto-off is scheduled when print completes."""
        mock_plug.off_delay_mode = "temperature"
        mock_plug.off_temp_threshold = 70

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_temp_based_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_called_once_with(mock_plug, 1, 70)

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_when_auto_off_disabled(self, manager, mock_plug, mock_db):
        """CRITICAL: Verify auto-off does NOT trigger when auto_off is False.

        This is a key regression test - the toggle must respect the setting.
        """
        mock_plug.auto_off = False

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
            patch.object(manager, "_schedule_temp_based_off") as mock_temp,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_not_called()
            mock_temp.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_when_plug_disabled(self, manager, mock_plug, mock_db):
        """Verify auto-off does NOT trigger when plug is disabled."""
        mock_plug.enabled = False

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_on_failed_print(self, manager, mock_plug, mock_db):
        """Verify auto-off does NOT trigger on failed prints for investigation."""
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_print_complete(printer_id=1, status="failed", db=mock_db)

            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_complete_skipped_on_aborted_print(self, manager, mock_plug, mock_db):
        """Verify auto-off does NOT trigger on aborted prints."""
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_print_complete(printer_id=1, status="aborted", db=mock_db)

            mock_schedule.assert_not_called()

    # ========================================================================
    # Tests for on_drying_complete (#1349)
    # ========================================================================

    @pytest.mark.asyncio
    async def test_on_drying_complete_schedules_delayed_off_when_enabled(self, manager, mock_plug, mock_db):
        """Plug with ``auto_off_after_drying=True`` gets a delayed-off scheduled
        using its drying-specific delay (independent of print-finish delay)."""
        mock_plug.auto_off_after_drying = True
        mock_plug.off_delay_after_drying_minutes = 15

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_drying_complete(printer_id=1, db=mock_db)

            mock_schedule.assert_called_once_with(mock_plug, 1, 15 * 60)

    @pytest.mark.asyncio
    async def test_on_drying_complete_skipped_when_toggle_off(self, manager, mock_plug, mock_db):
        """Default state — toggle off → nothing scheduled. This is the regression
        guard for users who only enable the print-finish auto-off and don't
        want the AMS-drying path silently running on the same plug."""
        mock_plug.auto_off_after_drying = False
        # auto_off itself is True (existing print-finish behaviour) — the
        # drying path must still be a no-op without its own toggle.
        mock_plug.auto_off = True

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_drying_complete(printer_id=1, db=mock_db)

            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_drying_complete_skipped_when_plug_disabled(self, manager, mock_plug, mock_db):
        """Drying auto-off honours the master ``enabled`` flag."""
        mock_plug.auto_off_after_drying = True
        mock_plug.enabled = False

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_drying_complete(printer_id=1, db=mock_db)

            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_drying_complete_skipped_for_ha_script_entity(self, manager, mock_plug, mock_db):
        """HA script entities can be triggered but not turned off — same
        guard the print-finish path has."""
        mock_plug.auto_off_after_drying = True
        mock_plug.plug_type = "homeassistant"
        mock_plug.ha_entity_id = "script.lights_off"

        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = [mock_plug]

            await manager.on_drying_complete(printer_id=1, db=mock_db)

            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_drying_complete_no_op_when_no_plugs(self, manager, mock_db):
        """Printer without any linked plugs is a silent no-op (not an error)."""
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get_plug,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get_plug.return_value = []

            await manager.on_drying_complete(printer_id=1, db=mock_db)

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
        mock_schedule_task = MagicMock()
        mock_snapshot_task = MagicMock()
        manager._scheduler_task = mock_schedule_task
        manager._snapshot_task = mock_snapshot_task

        # Mock the loop coroutines to avoid unawaited coroutine warnings
        with (
            patch.object(manager, "_schedule_loop") as mock_loop,
            patch.object(manager, "_snapshot_loop") as mock_snapshot,
            patch("asyncio.create_task") as mock_create,
        ):
            manager.start_scheduler()

            mock_create.assert_not_called()  # Should not create new tasks
            mock_loop.assert_not_called()
            mock_snapshot.assert_not_called()

    def test_stop_scheduler_cancels_snapshot_task(self, manager):
        """Verify stopping scheduler also cancels the snapshot loop (#941)."""
        mock_schedule_task = MagicMock()
        mock_snapshot_task = MagicMock()
        manager._scheduler_task = mock_schedule_task
        manager._snapshot_task = mock_snapshot_task

        manager.stop_scheduler()

        mock_schedule_task.cancel.assert_called_once()
        mock_snapshot_task.cancel.assert_called_once()
        assert manager._scheduler_task is None
        assert manager._snapshot_task is None


class TestGetPlugsForPrinter:
    """Tests for _get_plugs_for_printer — returns all plugs for a printer (#903)."""

    @pytest.fixture
    def manager(self):
        return SmartPlugManager()

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_plugs(self, manager):
        """Verify empty list is returned when no plugs are linked to printer."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await manager._get_plugs_for_printer(1, mock_db)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_single_plug_as_list(self, manager):
        """Verify single plug is returned in a list."""
        plug = MagicMock()
        plug.plug_type = "tasmota"

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [plug]
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await manager._get_plugs_for_printer(1, mock_db)
        assert result == [plug]

    @pytest.mark.asyncio
    async def test_returns_all_plugs(self, manager):
        """Verify all plugs are returned when multiple exist (#903)."""
        plug1 = MagicMock()
        plug1.plug_type = "homeassistant"
        plug1.ha_entity_id = "switch.printer"

        plug2 = MagicMock()
        plug2.plug_type = "homeassistant"
        plug2.ha_entity_id = "switch.filter"

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [plug1, plug2]
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await manager._get_plugs_for_printer(1, mock_db)
        assert result == [plug1, plug2]


class TestAutoOffPersistent:
    """Tests for persistent auto-off behavior (Issue #826).

    When auto_off_persistent is True, auto_off should remain enabled after
    execution instead of being disabled (one-shot default).
    """

    @pytest.fixture
    def manager(self):
        return SmartPlugManager()

    @pytest.mark.asyncio
    async def test_mark_auto_off_executed_one_shot_disables_auto_off(self, manager):
        """Default one-shot: auto_off should be set to False after execution."""
        mock_plug = MagicMock()
        mock_plug.id = 1
        mock_plug.auto_off = True
        mock_plug.auto_off_persistent = False
        mock_plug.auto_off_executed = False
        mock_plug.auto_off_pending = True
        mock_plug.auto_off_pending_since = datetime.now(timezone.utc)

        with patch("backend.app.core.database.async_session") as mock_session_ctx:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_plug
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.commit = AsyncMock()

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            await manager._mark_auto_off_executed(1)

            assert mock_plug.auto_off is False, "One-shot: auto_off should be disabled"
            assert mock_plug.auto_off_pending is False
            assert mock_plug.auto_off_pending_since is None
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_auto_off_executed_persistent_keeps_auto_off_enabled(self, manager):
        """Persistent mode: auto_off should remain True after execution."""
        mock_plug = MagicMock()
        mock_plug.id = 2
        mock_plug.auto_off = True
        mock_plug.auto_off_persistent = True
        mock_plug.auto_off_executed = False
        mock_plug.auto_off_pending = True
        mock_plug.auto_off_pending_since = datetime.now(timezone.utc)

        with patch("backend.app.core.database.async_session") as mock_session_ctx:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_plug
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.commit = AsyncMock()

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            await manager._mark_auto_off_executed(2)

            assert mock_plug.auto_off is True, "Persistent: auto_off should stay enabled"
            assert mock_plug.auto_off_pending is False
            assert mock_plug.auto_off_pending_since is None
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_persistent_auto_off_full_cycle(self, manager):
        """Verify persistent auto-off survives a full print cycle.

        Simulates: print start → print complete → auto-off executes → next print start.
        auto_off should remain True throughout for persistent plugs.
        """
        mock_plug = MagicMock()
        mock_plug.id = 3
        mock_plug.name = "HA BentoBox Filter"
        mock_plug.plug_type = "homeassistant"
        mock_plug.ha_entity_id = "switch.bentobox_filter"
        mock_plug.ip_address = None
        mock_plug.username = None
        mock_plug.password = None
        mock_plug.enabled = True
        mock_plug.auto_on = True
        mock_plug.auto_off = True
        mock_plug.auto_off_persistent = True
        mock_plug.off_delay_mode = "time"
        mock_plug.off_delay_minutes = 1
        mock_plug.off_temp_threshold = 70
        mock_plug.printer_id = 1
        mock_plug.auto_off_executed = False
        mock_plug.auto_off_pending = False
        mock_plug.last_state = "OFF"
        mock_plug.last_checked = None

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()

        # Step 1: Print starts — plug turns on
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get,
            patch.object(manager, "get_service_for_plug", new_callable=AsyncMock) as mock_svc,
        ):
            mock_get.return_value = [mock_plug]
            mock_service = AsyncMock()
            mock_service.turn_on = AsyncMock(return_value=True)
            mock_svc.return_value = mock_service

            await manager.on_print_start(printer_id=1, db=mock_db)

            assert mock_plug.auto_off_executed is False
            assert mock_plug.auto_off is True  # Still enabled

        # Step 2: Print completes — auto-off is scheduled
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock) as mock_get,
            patch.object(manager, "_schedule_delayed_off") as mock_schedule,
        ):
            mock_get.return_value = [mock_plug]

            await manager.on_print_complete(printer_id=1, status="completed", db=mock_db)

            mock_schedule.assert_called_once()
            assert mock_plug.auto_off is True  # Still enabled after scheduling

        # Step 3: Auto-off executes via _mark_auto_off_executed
        with patch("backend.app.core.database.async_session") as mock_session_ctx:
            mock_db2 = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_plug
            mock_db2.execute = AsyncMock(return_value=mock_result)
            mock_db2.commit = AsyncMock()

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db2)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            await manager._mark_auto_off_executed(3)

            # KEY ASSERTION: auto_off stays True for persistent mode
            assert mock_plug.auto_off is True, "Persistent auto_off must survive execution"
            assert mock_plug.auto_off_pending is False


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
            mock_datetime.utcnow.return_value = datetime.now(timezone.utc)

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
            mock_datetime.utcnow.return_value = datetime.now(timezone.utc)

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
        mock_plug.auto_off_pending_since = datetime.now(timezone.utc)
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
        mock_plug.auto_off_pending_since = datetime.now(timezone.utc)
        mock_plug.off_delay_mode = "time"

        with (
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
            patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock) as mock_mark,
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
        ):
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_off = AsyncMock(return_value=True)
            mock_pm.is_print_active.return_value = False  # printer idle on restart

            await manager.resume_pending_auto_offs()

            mock_tasmota.turn_off.assert_called_once()
            mock_mark.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_resume_pending_auto_off_skipped_when_printing(self, manager):
        """#1890: on restart, a stale pending off must NOT power off a live print;
        the pending flag is cleared instead."""
        mock_plug = MagicMock()
        mock_plug.id = 1
        mock_plug.name = "Test Plug"
        mock_plug.printer_id = 1
        mock_plug.auto_off_pending = True
        mock_plug.auto_off_pending_since = datetime.now(timezone.utc)
        mock_plug.off_delay_mode = "time"

        with (
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
            patch.object(manager, "_schedule_temp_based_off") as mock_temp,
        ):
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_off = AsyncMock(return_value=True)
            mock_pm.is_print_active.return_value = True  # printer printing again on restart
            mock_pm.get_status.return_value = MagicMock(state="RUNNING")

            await manager.resume_pending_auto_offs()

            mock_tasmota.turn_off.assert_not_called()  # never cut power on the live print
            mock_temp.assert_not_called()
            assert mock_plug.auto_off_pending is False  # stale pending cleared


class TestActivePrintGuard:
    """#1890 — auto-off must never cut power while a print is loaded/running.

    Covers the two off-executors (`_delayed_off`, `_temp_based_off`), the new
    queue-override scheduler that honours per-plug settings, and the
    on_print_start cancellation gap.
    """

    @pytest.fixture
    def manager(self):
        return SmartPlugManager()

    @pytest.fixture
    def mock_plug(self):
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
        plug.plug_type = "tasmota"
        plug.ha_entity_id = None
        return plug

    # ---- _delayed_off (time mode) ----------------------------------------

    @pytest.mark.asyncio
    async def test_delayed_off_skips_when_printer_printing_again(self, manager):
        """Time-delay fires after N min; if a reprint is running, skip the off."""
        with (
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
            patch.object(manager, "get_service_for_plug", new_callable=AsyncMock) as mock_get_svc,
            patch.object(manager, "_mark_auto_off_pending", new_callable=AsyncMock) as mock_mark_pending,
            patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock) as mock_mark_exec,
        ):
            mock_pm.is_print_active.return_value = True
            mock_pm.get_status.return_value = MagicMock(state="RUNNING")

            await manager._delayed_off(1, "tasmota", "1.2.3.4", None, None, None, printer_id=1, delay_seconds=0)

            mock_get_svc.assert_not_called()  # never even resolved a service to turn off
            mock_mark_exec.assert_not_called()
            mock_mark_pending.assert_awaited_with(1, False)  # pending flag cleared

    @pytest.mark.asyncio
    async def test_delayed_off_powers_off_when_idle(self, manager):
        """When the printer is genuinely idle, the delayed off still fires."""
        mock_service = AsyncMock()
        mock_service.turn_off = AsyncMock(return_value=True)
        with (
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
            patch.object(manager, "get_service_for_plug", new_callable=AsyncMock, return_value=mock_service),
            patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock),
        ):
            mock_pm.is_print_active.return_value = False

            await manager._delayed_off(1, "tasmota", "1.2.3.4", None, None, None, printer_id=1, delay_seconds=0)

            mock_service.turn_off.assert_awaited_once()
            mock_pm.mark_printer_offline.assert_called_once_with(1)

    # ---- _temp_based_off (temperature mode) ------------------------------

    @pytest.mark.asyncio
    async def test_temp_based_off_defers_while_printing_even_if_cool(self, manager):
        """Nozzle can dip below threshold during a reprint's PREPARE/heat phase;
        the guard must defer rather than cut power on the loaded print."""
        with (
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
            patch("backend.app.services.smart_plug_manager.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch.object(manager, "get_service_for_plug", new_callable=AsyncMock) as mock_get_svc,
        ):
            # Cool enough to trip the threshold, but a print is active.
            mock_pm.get_status.return_value = MagicMock(state="PREPARE", temperatures={"nozzle": 30})
            mock_pm.is_print_active.return_value = True
            # Break the poll loop after the first deferral so the test terminates.
            mock_sleep.side_effect = asyncio.CancelledError()

            await manager._temp_based_off(1, "tasmota", "1.2.3.4", None, None, None, printer_id=1, temp_threshold=70)

            mock_get_svc.assert_not_called()  # never turned off despite temp < threshold

    @pytest.mark.asyncio
    async def test_temp_based_off_powers_off_when_cool_and_idle(self, manager):
        """Cool nozzle + idle printer → turn off using the plug's threshold."""
        mock_service = AsyncMock()
        mock_service.turn_off = AsyncMock(return_value=True)
        with (
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
            patch("backend.app.services.smart_plug_manager.asyncio.sleep", new_callable=AsyncMock),
            patch.object(manager, "get_service_for_plug", new_callable=AsyncMock, return_value=mock_service),
            patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock),
        ):
            mock_pm.get_status.return_value = MagicMock(state="FINISH", temperatures={"nozzle": 40})
            mock_pm.is_print_active.return_value = False

            await manager._temp_based_off(1, "tasmota", "1.2.3.4", None, None, None, printer_id=1, temp_threshold=55)

            mock_service.turn_off.assert_awaited_once()

    # ---- schedule_off_after_queue_job (uses plug settings, not hardcoded 50/600)

    @pytest.mark.asyncio
    async def test_queue_off_uses_time_mode_regardless_of_global_auto_off(self, manager, mock_plug):
        """Queue 'auto off after this job' is a per-job override — it schedules
        even when the plug's global auto_off is disabled, and honours the plug's
        configured time-delay mode."""
        mock_plug.auto_off = False
        mock_plug.off_delay_mode = "time"
        mock_plug.off_delay_minutes = 8
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock, return_value=[mock_plug]),
            patch.object(manager, "_schedule_delayed_off") as mock_delayed,
            patch.object(manager, "_schedule_temp_based_off") as mock_temp,
        ):
            await manager.schedule_off_after_queue_job(printer_id=1, db=AsyncMock())

            mock_delayed.assert_called_once_with(mock_plug, 1, 8 * 60)  # plug's minutes, not hardcoded
            mock_temp.assert_not_called()

    @pytest.mark.asyncio
    async def test_queue_off_uses_configured_temp_threshold(self, manager, mock_plug):
        """Temperature mode passes the plug's off_temp_threshold, not a hardcoded 50."""
        mock_plug.off_delay_mode = "temperature"
        mock_plug.off_temp_threshold = 65
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock, return_value=[mock_plug]),
            patch.object(manager, "_schedule_delayed_off") as mock_delayed,
            patch.object(manager, "_schedule_temp_based_off") as mock_temp,
        ):
            await manager.schedule_off_after_queue_job(printer_id=1, db=AsyncMock())

            mock_temp.assert_called_once_with(mock_plug, 1, 65)
            mock_delayed.assert_not_called()

    @pytest.mark.asyncio
    async def test_queue_off_skips_disabled_and_ha_script_plugs(self, manager, mock_plug):
        """Disabled plugs and HA-script entities are never scheduled."""
        disabled = MagicMock(id=2, name="disabled", enabled=False, plug_type="tasmota", ha_entity_id=None)
        ha_script = MagicMock(
            id=3, name="ha", enabled=True, plug_type="homeassistant", ha_entity_id="script.printer_off"
        )
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock, return_value=[disabled, ha_script]),
            patch.object(manager, "_schedule_off_per_mode") as mock_sched,
        ):
            await manager.schedule_off_after_queue_job(printer_id=1, db=AsyncMock())

            mock_sched.assert_not_called()

    # ---- on_print_start cancellation gap ---------------------------------

    @pytest.mark.asyncio
    async def test_reprint_cancels_pending_off_even_when_auto_on_disabled(self, manager, mock_plug):
        """A reprint must abort a scheduled auto-off regardless of auto_on (#1890).

        Previously the cancel lived behind the auto_on gate, so a plug with
        auto_on disabled kept its pending off and cut power mid-reprint.
        """
        mock_plug.auto_on = False
        mock_task = MagicMock()
        manager._pending_off[mock_plug.id] = mock_task
        with (
            patch.object(manager, "_get_plugs_for_printer", new_callable=AsyncMock, return_value=[mock_plug]),
            patch.object(manager, "_mark_auto_off_pending", new_callable=AsyncMock),
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
        ):
            mock_tasmota.turn_on = AsyncMock()

            await manager.on_print_start(printer_id=1, db=AsyncMock())

            mock_task.cancel.assert_called_once()  # cancelled despite auto_on=False
            assert mock_plug.id not in manager._pending_off
            mock_tasmota.turn_on.assert_not_called()  # but not powered on


class TestAccessoryPlugDoesNotMarkPrinterOffline:
    """#2629 — a plug linked to a printer is not necessarily its power supply.

    Filter fans, chamber lights and enclosure heaters are linked so they follow
    the print cycle. Marking the printer offline when one of those switches off
    blanks the printer state and stalls the queue until a manual Force Refresh.
    """

    @pytest.fixture
    def manager(self):
        return SmartPlugManager()

    @pytest.fixture
    def accessory_plug(self):
        plug = MagicMock()
        plug.id = 1
        plug.name = "BentoBox Filter"
        plug.ip_address = "192.168.1.100"
        plug.username = None
        plug.password = None
        plug.enabled = True
        plug.auto_off = True
        plug.off_delay_mode = "time"
        plug.off_delay_minutes = 1
        plug.off_temp_threshold = 70
        plug.printer_id = 1
        plug.plug_type = "tasmota"
        plug.ha_entity_id = None
        plug.controls_printer_power = False
        return plug

    @pytest.mark.asyncio
    async def test_delayed_off_skips_offline_mark_for_accessory(self, manager):
        mock_service = AsyncMock()
        mock_service.turn_off = AsyncMock(return_value=True)
        with (
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
            patch.object(manager, "get_service_for_plug", new_callable=AsyncMock, return_value=mock_service),
            patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock),
        ):
            mock_pm.is_print_active.return_value = False

            await manager._delayed_off(
                1, "tasmota", "1.2.3.4", None, None, None, printer_id=1, delay_seconds=0, controls_printer_power=False
            )

            mock_service.turn_off.assert_awaited_once()  # the plug still switches off
            mock_pm.mark_printer_offline.assert_not_called()  # but the printer is untouched

    @pytest.mark.asyncio
    async def test_temp_based_off_skips_offline_mark_for_accessory(self, manager):
        mock_service = AsyncMock()
        mock_service.turn_off = AsyncMock(return_value=True)
        with (
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
            patch("backend.app.services.smart_plug_manager.asyncio.sleep", new_callable=AsyncMock),
            patch.object(manager, "get_service_for_plug", new_callable=AsyncMock, return_value=mock_service),
            patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock),
        ):
            mock_pm.get_status.return_value = MagicMock(state="FINISH", temperatures={"nozzle": 40})
            mock_pm.is_print_active.return_value = False

            await manager._temp_based_off(
                1,
                "tasmota",
                "1.2.3.4",
                None,
                None,
                None,
                printer_id=1,
                temp_threshold=55,
                controls_printer_power=False,
            )

            mock_service.turn_off.assert_awaited_once()
            mock_pm.mark_printer_offline.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedulers_forward_the_flag(self, manager, accessory_plug):
        """The flag lives on the plug row; both schedulers must pass it into the
        detached task, which only receives primitives."""
        with (
            patch.object(manager, "_mark_auto_off_pending", new_callable=AsyncMock),
            patch.object(manager, "_delayed_off", new_callable=AsyncMock) as mock_delayed,
            patch.object(manager, "_temp_based_off", new_callable=AsyncMock) as mock_temp,
        ):
            manager._schedule_delayed_off(accessory_plug, 1, 60)
            manager._schedule_temp_based_off(accessory_plug, 1, 70)

            assert mock_delayed.call_args.kwargs["controls_printer_power"] is False
            assert mock_temp.call_args.kwargs["controls_printer_power"] is False

    @pytest.mark.asyncio
    async def test_scheduled_off_skips_offline_mark_for_accessory(self, manager, accessory_plug):
        """The time-of-day schedule path has its own turn-off + offline mark."""
        accessory_plug.schedule_enabled = True
        accessory_plug.schedule_on_time = None
        accessory_plug.schedule_off_time = "22:00"
        with (
            patch("backend.app.services.smart_plug_manager.datetime") as mock_datetime,
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
        ):
            mock_now = MagicMock()
            mock_now.strftime.return_value = "22:00"
            mock_datetime.now.return_value = mock_now

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [accessory_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.commit = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_off = AsyncMock(return_value=True)

            await manager._check_schedules()

            mock_tasmota.turn_off.assert_awaited_once_with(accessory_plug)
            mock_pm.mark_printer_offline.assert_not_called()

    @pytest.mark.asyncio
    async def test_scheduled_off_still_marks_offline_for_power_plug(self, manager, accessory_plug):
        """Default (a plug that really feeds the printer) keeps the old behaviour."""
        accessory_plug.controls_printer_power = True
        accessory_plug.schedule_enabled = True
        accessory_plug.schedule_on_time = None
        accessory_plug.schedule_off_time = "22:00"
        with (
            patch("backend.app.services.smart_plug_manager.datetime") as mock_datetime,
            patch("backend.app.core.database.async_session") as mock_session_ctx,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as mock_tasmota,
            patch("backend.app.services.smart_plug_manager.printer_manager") as mock_pm,
        ):
            mock_now = MagicMock()
            mock_now.strftime.return_value = "22:00"
            mock_datetime.now.return_value = mock_now

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [accessory_plug]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.commit = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock()

            mock_tasmota.turn_off = AsyncMock(return_value=True)

            await manager._check_schedules()

            mock_pm.mark_printer_offline.assert_called_once_with(1)
