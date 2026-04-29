"""Regression tests for ``PrinterManager._broadcast_status_change`` and
its wiring from ``set_awaiting_plate_clear`` (#1128).

The bug: ``awaiting_plate_clear`` is a Bambuddy-side flag, so toggling it
doesn't produce an MQTT push from the printer. Before the fix,
``set_awaiting_plate_clear()`` mutated state and persisted to DB but never
notified WebSocket subscribers. The plate-clear button on the printer card
disappeared "immediately" only because of an optimistic React Query cache
update on the click path; any other caller (admin script, second tab, an
automation that hits ``POST /printers/{id}/clear-plate``) silently left
the UI stale until the next coincidental status refresh.

These tests pin the contract: every flip of the flag must schedule a
``printer_status`` broadcast, and the broadcast must carry the new flag
value so subscribers see the right state without polling.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.printer_manager import PrinterManager


@pytest.fixture
def manager():
    """Fresh manager per test; the awaiting-plate-clear set is per-instance."""
    return PrinterManager()


def _close_unawaited(coro):
    """Side effect for mocked ``_schedule_async``.

    ``set_awaiting_plate_clear`` evaluates the coroutine expressions
    ``self._persist_awaiting_plate_clear(...)`` and
    ``self._broadcast_status_change(...)`` before passing them to
    ``_schedule_async``. When that target is patched, the coroutine objects
    leak — Python's ``__del__`` then emits ``coroutine was never awaited``
    during GC, and when GC runs late enough that warning hits the interpreter
    shutdown path with ``KeyError: '__import__'``. Closing the coroutine here
    prevents both. Returns ``None`` so the mock's call signature is unchanged.
    """
    if asyncio.iscoroutine(coro):
        coro.close()
    return None


def _fake_state(**overrides):
    """Minimal stand-in for a ``PrinterState`` — only the attributes
    ``printer_state_to_dict`` reads. We use a SimpleNamespace rather than
    constructing a real PrinterState so this test stays fast and doesn't
    couple to the (large, evolving) PrinterState dataclass shape."""
    base = {
        "connected": True,
        "state": "FINISH",
        "raw_data": {},
        "progress": 100.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSchedulingFromSetAwaitingPlateClear:
    """The hook from the public flag-mutation method into the broadcast."""

    def test_schedules_broadcast_when_loop_running(self, manager):
        """When a real event loop is attached, every call to
        ``set_awaiting_plate_clear`` must enqueue both the persistence
        coroutine and the broadcast coroutine. Both are needed: persist
        survives restarts, broadcast notifies live subscribers."""
        manager._loop = MagicMock()
        manager._loop.is_running.return_value = True

        with patch.object(manager, "_schedule_async", side_effect=_close_unawaited) as scheduled:
            manager.set_awaiting_plate_clear(7, True)

        # Two coroutines: persist + broadcast. Order doesn't matter.
        assert scheduled.call_count == 2

    def test_does_not_schedule_when_no_loop_attached(self, manager):
        """Sync unit-test path (no loop attached): nothing must be
        scheduled, otherwise Python emits 'coroutine was never awaited'
        runtime warnings and the test suite goes red on harmless flag
        twiddling."""
        manager._loop = None

        with patch.object(manager, "_schedule_async") as scheduled:
            manager.set_awaiting_plate_clear(7, True)

        scheduled.assert_not_called()

    def test_does_not_schedule_when_loop_not_running(self, manager):
        """A loop attached-but-stopped is the same situation as no loop —
        scheduling onto a dead loop would never fire."""
        manager._loop = MagicMock()
        manager._loop.is_running.return_value = False

        with patch.object(manager, "_schedule_async") as scheduled:
            manager.set_awaiting_plate_clear(7, True)

        scheduled.assert_not_called()

    def test_both_true_and_false_flips_schedule_broadcast(self, manager):
        """The bug only became visible on ``False`` flips (clear), but a
        regression that broadcasts only on ``True`` would re-introduce
        the original symptom for any future flag mutation that goes
        ``False → True`` outside the printer-card optimistic-update
        path. Make both directions a contract."""
        manager._loop = MagicMock()
        manager._loop.is_running.return_value = True

        with patch.object(manager, "_schedule_async", side_effect=_close_unawaited) as scheduled:
            manager.set_awaiting_plate_clear(7, True)
            scheduled.reset_mock()
            manager.set_awaiting_plate_clear(7, False)

        # Each flip = persist + broadcast = 2 calls.
        assert scheduled.call_count == 2


class TestBroadcastStatusChange:
    """The broadcast coroutine itself."""

    @pytest.mark.asyncio
    async def test_emits_ws_update_when_state_present(self, manager):
        """Happy path: printer has a known status, broadcast goes out
        with the dict produced by ``printer_state_to_dict``."""
        state = _fake_state()
        with (
            patch.object(manager, "get_status", return_value=state),
            patch.object(manager, "get_model", return_value="P1S"),
            patch(
                "backend.app.core.websocket.ws_manager.send_printer_status",
                new_callable=AsyncMock,
            ) as send_status,
            patch(
                "backend.app.services.printer_manager.printer_state_to_dict",
                return_value={"id": 7, "awaiting_plate_clear": False},
            ) as to_dict,
        ):
            await manager._broadcast_status_change(7)

        send_status.assert_awaited_once()
        # First positional arg is the printer ID, second is the status dict.
        printer_id_arg, payload_arg = send_status.await_args.args
        assert printer_id_arg == 7
        assert payload_arg == {"id": 7, "awaiting_plate_clear": False}
        # Verify the dict was built from the right inputs (state + id + model).
        to_dict.assert_called_once_with(state, 7, "P1S")

    @pytest.mark.asyncio
    async def test_skips_when_status_unknown(self, manager):
        """Printer not connected / unknown ID → no point broadcasting a
        snapshot we don't have. A future reconnect will produce a fresh
        status push anyway, so we'd only be forcing a stale or bogus
        payload onto subscribers right now."""
        with (
            patch.object(manager, "get_status", return_value=None),
            patch(
                "backend.app.core.websocket.ws_manager.send_printer_status",
                new_callable=AsyncMock,
            ) as send_status,
        ):
            await manager._broadcast_status_change(999)

        send_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_websocket_errors(self, manager):
        """The broadcast is a courtesy, not a correctness path — if the
        WS layer is down, the flag is already mutated in-memory and
        persisted. Letting an exception bubble out of
        ``_broadcast_status_change`` would surface as an
        ``Exception in scheduled callback`` traceback in the log AND
        prevent the persistence coroutine from completing if both were
        gathered together. Swallow + warn instead."""
        with (
            patch.object(manager, "get_status", return_value=_fake_state()),
            patch.object(manager, "get_model", return_value="P1S"),
            patch(
                "backend.app.services.printer_manager.printer_state_to_dict",
                return_value={"id": 7},
            ),
            patch(
                "backend.app.core.websocket.ws_manager.send_printer_status",
                new_callable=AsyncMock,
                side_effect=RuntimeError("websocket layer unavailable"),
            ),
        ):
            # Must not raise.
            await manager._broadcast_status_change(7)


class TestEndToEndUnderRunningLoop:
    """Verify the full flow under a real running event loop — schedule
    → broadcast → ws_manager.send_printer_status — without mocking
    ``_schedule_async``. Catches regressions where individual pieces
    pass but the wiring breaks (e.g. ``_schedule_async`` swallowing the
    broadcast coroutine)."""

    @pytest.mark.asyncio
    async def test_set_false_eventually_emits_broadcast(self, manager):
        """Reproduces the #1128 fix path end-to-end: set the flag to
        False under a live loop, give the scheduler a tick, the
        ws broadcast must have fired with the new payload."""
        loop = asyncio.get_running_loop()
        manager._loop = loop
        # Pretend the printer has been seen — without a state present
        # the broadcast short-circuits before reaching ws_manager.
        manager._awaiting_plate_clear.add(7)

        with (
            patch.object(manager, "get_status", return_value=_fake_state()),
            patch.object(manager, "get_model", return_value="P1S"),
            patch(
                "backend.app.services.printer_manager.printer_state_to_dict",
                return_value={"id": 7, "awaiting_plate_clear": False},
            ),
            patch(
                "backend.app.core.websocket.ws_manager.send_printer_status",
                new_callable=AsyncMock,
            ) as send_status,
            # Persistence path opens a DB session; stub it out so this
            # stays a pure unit test.
            patch.object(manager, "_persist_awaiting_plate_clear", new_callable=AsyncMock),
        ):
            manager.set_awaiting_plate_clear(7, False)
            # Yield repeatedly so run_coroutine_threadsafe has a chance
            # to land its scheduled coroutine on this loop.
            for _ in range(10):
                await asyncio.sleep(0)

        send_status.assert_awaited()
        printer_id_arg, payload_arg = send_status.await_args.args
        assert printer_id_arg == 7
        assert payload_arg["awaiting_plate_clear"] is False
