"""Unit tests for spool assignment notification service."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.spool_assignment_notifications import notify_missing_spool_assignments_on_print_start


class _FakeAssignmentsResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, printer_name: str, assignments: list[SimpleNamespace]):
        self._printer = SimpleNamespace(name=printer_name)
        self._assignments = assignments

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, key):
        return self._printer

    async def execute(self, statement):
        return _FakeAssignmentsResult(self._assignments)


@pytest.mark.asyncio
async def test_missing_assignment_broadcasts_websocket_event_and_push_notification():
    """When a mapped tray is unassigned, service emits websocket and notification events."""
    logger = logging.getLogger(__name__)
    data = {
        "ams_mapping": [1],
        "raw_data": {},
    }

    # Assignment exists for A1 (global tray 0), but print uses A2 (global tray 1).
    assignments = [SimpleNamespace(ams_id=0, tray_id=0)]

    with (
        patch(
            "backend.app.services.spool_assignment_notifications.async_session",
            return_value=_FakeSession("Printer A", assignments),
        ),
        patch("backend.app.services.spool_assignment_notifications.printer_manager.get_status", return_value=None),
        patch(
            "backend.app.services.spool_assignment_notifications.ws_manager.send_missing_spool_assignment",
            new_callable=AsyncMock,
        ) as mock_ws,
        patch(
            "backend.app.services.spool_assignment_notifications.notification_service.on_print_missing_spool_assignment",
            new_callable=AsyncMock,
        ) as mock_notify,
    ):
        await notify_missing_spool_assignments_on_print_start(1, data, logger)

    mock_ws.assert_awaited_once()
    ws_kwargs = mock_ws.await_args.kwargs
    assert ws_kwargs["printer_id"] == 1
    assert ws_kwargs["printer_name"] == "Printer A"
    assert ws_kwargs["missing_slots"] == [{"slot": "A2", "profile": "Unknown", "color": "Unknown"}]

    mock_notify.assert_awaited_once()
    notify_kwargs = mock_notify.await_args.kwargs
    assert notify_kwargs["printer_id"] == 1
    assert notify_kwargs["printer_name"] == "Printer A"
    assert notify_kwargs["missing_slots"] == [{"slot": "A2", "profile": "Unknown", "color": "Unknown"}]
