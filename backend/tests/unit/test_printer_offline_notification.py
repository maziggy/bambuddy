"""Tests for the connected → disconnected edge that fires the
`on_printer_offline` notification (#1752).

The provider toggle, schema, and dispatcher already existed; what was missing
was a caller that fires `notification_service.on_printer_offline` when a
printer goes offline. These tests pin both layers:

  * `_maybe_notify_printer_offline` — the debounced background task. Must
    fire when the printer is still offline at the end of the window, and
    must NOT fire if the printer reconnected during the window.

  * Edge detection inside `on_printer_status_change` — schedules the task
    only on the True → False transition, cancels any pending task on
    reconnect, and stays silent on startup (no prior connected state).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app import main as main_module


def _state(connected: bool, state: str = "IDLE") -> SimpleNamespace:
    """Minimal PrinterState stub. `state="IDLE"` keeps the reconcile-edge
    branch quiescent (it only fires on `connected=True` with a non-unknown
    state-string, which we exercise separately) but otherwise lets the
    handler thread through without doing extra DB / WS work."""
    return SimpleNamespace(
        connected=connected,
        state=state,
        progress=0,
        layer_num=0,
        temperatures={},
        raw_data={},
        stg_cur=0,
        cooling_fan_speed=0,
        big_fan1_speed=0,
        big_fan2_speed=0,
        chamber_light="",
        active_extruder=0,
        tray_now=0,
        door_open=False,
        subtask_name="",
        ams_filament_backup=None,
    )


@pytest.fixture(autouse=True)
def _reset_edge_state():
    """Clear the module-level edge dicts between tests so one test's
    True-edge doesn't leak into the next."""
    main_module._printer_last_connected.clear()
    for task in list(main_module._printer_offline_notify_tasks.values()):
        if not task.done():
            task.cancel()
    main_module._printer_offline_notify_tasks.clear()
    main_module._printer_reconciled_since_connect.clear()
    main_module._last_status_broadcast.clear()
    yield
    main_module._printer_last_connected.clear()
    for task in list(main_module._printer_offline_notify_tasks.values()):
        if not task.done():
            task.cancel()
    main_module._printer_offline_notify_tasks.clear()


class TestMaybeNotifyPrinterOffline:
    """The debounced background task — fires notification at the end of the
    window only if the printer is still offline."""

    @pytest.mark.asyncio
    async def test_fires_notification_when_still_offline_after_debounce(self):
        printer = SimpleNamespace(id=1, name="Workshop")
        scalar = MagicMock()
        scalar.scalar_one_or_none.return_value = printer
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=db)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.main.asyncio.sleep", new=AsyncMock()),
            patch("backend.app.main.printer_manager") as mock_pm,
            patch("backend.app.main.async_session", return_value=session_cm),
            patch("backend.app.main.notification_service") as mock_notif,
        ):
            mock_pm.is_connected.return_value = False
            mock_notif.on_printer_offline = AsyncMock()

            await main_module._maybe_notify_printer_offline(printer_id=1)

            mock_notif.on_printer_offline.assert_awaited_once_with(1, "Workshop", db)

    @pytest.mark.asyncio
    async def test_does_not_fire_when_printer_reconnected_during_debounce(self):
        with (
            patch("backend.app.main.asyncio.sleep", new=AsyncMock()),
            patch("backend.app.main.printer_manager") as mock_pm,
            patch("backend.app.main.notification_service") as mock_notif,
        ):
            mock_pm.is_connected.return_value = True
            mock_notif.on_printer_offline = AsyncMock()

            await main_module._maybe_notify_printer_offline(printer_id=1)

            mock_notif.on_printer_offline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_fire_when_printer_missing_from_db(self):
        scalar = MagicMock()
        scalar.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=db)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.main.asyncio.sleep", new=AsyncMock()),
            patch("backend.app.main.printer_manager") as mock_pm,
            patch("backend.app.main.async_session", return_value=session_cm),
            patch("backend.app.main.notification_service") as mock_notif,
        ):
            mock_pm.is_connected.return_value = False
            mock_notif.on_printer_offline = AsyncMock()

            await main_module._maybe_notify_printer_offline(printer_id=1)

            mock_notif.on_printer_offline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clears_task_entry_after_run(self):
        with (
            patch("backend.app.main.asyncio.sleep", new=AsyncMock()),
            patch("backend.app.main.printer_manager") as mock_pm,
        ):
            mock_pm.is_connected.return_value = True  # No notification path
            main_module._printer_offline_notify_tasks[1] = MagicMock()
            await main_module._maybe_notify_printer_offline(printer_id=1)
            assert 1 not in main_module._printer_offline_notify_tasks


class TestOfflineEdgeDetection:
    """Edge detection inside `on_printer_status_change` — only the
    True → False transition schedules a task. Reconnects cancel pending
    tasks. Startup-with-disconnected does not fire."""

    @staticmethod
    def _patch_handler_deps():
        """Patch out the heavy side-effects of `on_printer_status_change`
        (MQTT relay, WebSocket broadcast, state serializer) so we can focus
        on edge state."""
        ws_mgr = MagicMock()
        ws_mgr.send_printer_status = AsyncMock()
        relay = MagicMock()
        relay.on_printer_status = AsyncMock()
        pm = MagicMock()
        pm.get_printer.return_value = None  # Skip the relay payload branch.
        pm.get_model.return_value = ""
        return ws_mgr, relay, pm

    @pytest.mark.asyncio
    async def test_first_call_connected_does_not_schedule(self):
        ws_mgr, relay, pm = self._patch_handler_deps()
        with (
            patch("backend.app.main.ws_manager", ws_mgr),
            patch("backend.app.main.mqtt_relay", relay),
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.spawn_background_task"),
            patch("backend.app.main.printer_state_to_dict", return_value={}),
        ):
            await main_module.on_printer_status_change(1, _state(connected=True))
        assert 1 not in main_module._printer_offline_notify_tasks
        assert main_module._printer_last_connected[1] is True

    @pytest.mark.asyncio
    async def test_first_call_disconnected_does_not_schedule(self):
        """Startup with an already-offline printer must not fire — there's
        no prior True observation, so we have no edge to trigger on."""
        ws_mgr, relay, pm = self._patch_handler_deps()
        with (
            patch("backend.app.main.ws_manager", ws_mgr),
            patch("backend.app.main.mqtt_relay", relay),
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.spawn_background_task"),
            patch("backend.app.main.printer_state_to_dict", return_value={}),
        ):
            await main_module.on_printer_status_change(1, _state(connected=False))
        assert 1 not in main_module._printer_offline_notify_tasks
        assert main_module._printer_last_connected[1] is False

    @pytest.mark.asyncio
    async def test_connected_to_disconnected_schedules_task(self):
        ws_mgr, relay, pm = self._patch_handler_deps()
        with (
            patch("backend.app.main.ws_manager", ws_mgr),
            patch("backend.app.main.mqtt_relay", relay),
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.spawn_background_task"),
            patch("backend.app.main._maybe_notify_printer_offline", new=AsyncMock()),
            patch("backend.app.main.printer_state_to_dict", return_value={}),
        ):
            await main_module.on_printer_status_change(1, _state(connected=True))
            await main_module.on_printer_status_change(1, _state(connected=False))

        task = main_module._printer_offline_notify_tasks.get(1)
        assert task is not None
        task.cancel()

    @pytest.mark.asyncio
    async def test_reconnect_cancels_pending_task(self):
        ws_mgr, relay, pm = self._patch_handler_deps()
        with (
            patch("backend.app.main.ws_manager", ws_mgr),
            patch("backend.app.main.mqtt_relay", relay),
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.spawn_background_task"),
            patch("backend.app.main._maybe_notify_printer_offline", new=AsyncMock()),
            patch("backend.app.main.printer_state_to_dict", return_value={}),
        ):
            await main_module.on_printer_status_change(1, _state(connected=True))
            await main_module.on_printer_status_change(1, _state(connected=False))
            scheduled = main_module._printer_offline_notify_tasks.get(1)
            assert scheduled is not None
            await main_module.on_printer_status_change(1, _state(connected=True))
            # Yield so the cancellation propagates through the event loop.
            await asyncio.sleep(0)

        assert 1 not in main_module._printer_offline_notify_tasks
        assert scheduled.cancelled() or scheduled.done()

    @pytest.mark.asyncio
    async def test_repeated_disconnected_does_not_reschedule(self):
        """A second False observation while a task is already pending must
        not replace the in-flight task — otherwise the debounce clock
        resets on every status callback and the notification never fires."""
        ws_mgr, relay, pm = self._patch_handler_deps()
        with (
            patch("backend.app.main.ws_manager", ws_mgr),
            patch("backend.app.main.mqtt_relay", relay),
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.spawn_background_task"),
            patch("backend.app.main._maybe_notify_printer_offline", new=AsyncMock()),
            patch("backend.app.main.printer_state_to_dict", return_value={}),
        ):
            await main_module.on_printer_status_change(1, _state(connected=True))
            await main_module.on_printer_status_change(1, _state(connected=False))
            first_task = main_module._printer_offline_notify_tasks.get(1)
            await main_module.on_printer_status_change(1, _state(connected=False))
            second_task = main_module._printer_offline_notify_tasks.get(1)

        assert first_task is second_task
        if first_task is not None:
            first_task.cancel()


class TestProgressMilestoneSessionHygiene:
    """The progress-milestone notification path must capture the camera
    snapshot WITHOUT holding a DB session (issue #2572): a ~15s RTSP grab
    across an open session pinned a pooled connection per milestone, per
    printer. The read (printer name) and the send (provider lookups) each get
    their own short session; the snapshot happens in between with none held."""

    @staticmethod
    def _printing_state(progress: int):
        st = _state(connected=True, state="RUNNING")
        st.progress = progress
        st.remaining_time = 30
        st.gcode_file = "benchy.gcode"
        return st

    @pytest.mark.asyncio
    async def test_milestone_captures_snapshot_outside_session_and_notifies(self):
        main_module._last_progress_milestone.clear()

        printer = SimpleNamespace(id=1, name="Workshop")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=printer)))

        # Stateful session that tracks how many sessions are currently open.
        open_sessions = {"count": 0}

        class _SessionCM:
            async def __aenter__(self):
                open_sessions["count"] += 1
                return db

            async def __aexit__(self, *exc):
                open_sessions["count"] -= 1
                return False

        snap_calls = []

        async def _snap(printer_id, prn, _logger):
            # The whole point of the fix (#2572): the ~15s camera grab must NOT
            # run while a DB session is held. On the old code the snapshot sat
            # inside the milestone session, so this would be 1.
            assert open_sessions["count"] == 0, "camera snapshot ran while a DB session was held"
            snap_calls.append((printer_id, prn))
            return b"jpeg-bytes"

        ws_mgr = MagicMock()
        ws_mgr.send_printer_status = AsyncMock()
        relay = MagicMock()
        relay.on_printer_status = AsyncMock()
        pm = MagicMock()
        pm.get_printer.return_value = None
        pm.get_model.return_value = ""

        with (
            patch("backend.app.main.ws_manager", ws_mgr),
            patch("backend.app.main.mqtt_relay", relay),
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.spawn_background_task"),
            patch("backend.app.main.printer_state_to_dict", return_value={}),
            patch("backend.app.main.async_session", side_effect=lambda: _SessionCM()),
            patch("backend.app.main._capture_snapshot_for_notification", new=_snap),
            patch("backend.app.main.notification_service") as mock_notif,
        ):
            mock_notif.on_print_progress = AsyncMock()

            await main_module.on_printer_status_change(1, self._printing_state(25))

            # Snapshot ran (with the detached printer) and outside any session.
            assert snap_calls == [(1, printer)]
            # The notification fired carrying that image (send legitimately holds a session).
            mock_notif.on_print_progress.assert_awaited_once()
            assert mock_notif.on_print_progress.await_args.kwargs["image_data"] == b"jpeg-bytes"
            # Every session opened was also closed — none leaked past the handler.
            assert open_sessions["count"] == 0

        main_module._last_progress_milestone.clear()
