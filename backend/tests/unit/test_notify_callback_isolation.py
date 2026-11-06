"""Optional Notify hooks must never interrupt essential printer callbacks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app import main
from backend.app.services.bambu_mqtt import PrinterState


class ReachedNextStep(Exception):
    """Stop a large callback once its essential post-hook action was reached."""


async def test_status_broadcast_survives_notify_observer_failure(monkeypatch):
    monkeypatch.setattr(main, "_last_status_broadcast", {})
    monkeypatch.setattr(main, "_printer_last_connected", {})
    monkeypatch.setattr(main, "_printer_reconciled_since_connect", {})
    printer_manager = MagicMock()
    printer_manager.get_printer.return_value = None
    printer_manager.get_model.return_value = ""
    broadcast = AsyncMock()
    with (
        patch.object(main.notify_live_activities, "observe", side_effect=RuntimeError("Notify failed")),
        patch.object(main, "printer_manager", printer_manager),
        patch.object(main.ws_manager, "send_printer_status", broadcast),
        patch.object(main, "printer_state_to_dict", return_value={"state": "IDLE"}),
        patch.object(main, "spawn_background_task", side_effect=lambda coro, **kwargs: coro.close()),
    ):
        await main.on_printer_status_change(42, PrinterState())
    broadcast.assert_awaited_once()


async def test_print_start_still_resets_milestones_when_notify_fails(monkeypatch):
    milestones = {42: 75}
    stopped = {42}
    monkeypatch.setattr(main, "_last_progress_milestone", milestones)
    monkeypatch.setattr(main, "_user_stopped_printers", stopped)
    with (
        patch.object(main.notify_live_activities, "print_started", side_effect=RuntimeError("Notify failed")),
        patch.object(main.printer_manager, "get_status", return_value=PrinterState()),
        patch.object(
            main, "_kill_switch_notification_tasks", SimpleNamespace(pop=MagicMock(side_effect=ReachedNextStep))
        ),
        pytest.raises(ReachedNextStep),
    ):
        await main.on_print_start(42, {})
    assert milestones[42] == 0
    assert 42 not in stopped


async def test_print_complete_still_requires_plate_clear_when_notify_fails(monkeypatch):
    monkeypatch.setattr(main, "_kill_switch_notification_tasks", {})
    monkeypatch.setattr(main, "_fallback_3mf_retry_tasks", {})
    monkeypatch.setattr(main, "_user_stopped_printers", {42})
    printer_manager = MagicMock()
    printer_manager.set_awaiting_plate_clear.side_effect = ReachedNextStep
    with (
        patch.object(
            main.notify_live_activities, "print_finished", side_effect=RuntimeError("Notify failed")
        ) as notify,
        patch.object(main, "printer_manager", printer_manager),
        patch.object(main, "_recover_fallback_from_cache_before_eviction", AsyncMock()),
        patch.object(main, "clear_3mf_cache"),
        patch.object(main.ws_manager, "send_print_complete", AsyncMock()) as broadcast,
        pytest.raises(ReachedNextStep),
    ):
        await main.on_print_complete(42, {"status": "failed"})
    printer_manager.set_awaiting_plate_clear.assert_called_once_with(42, True)
    assert notify.call_args.args[1]["status"] == "cancelled"
    broadcast.assert_awaited_once()
