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
    """Fake DB session that returns legacy vs. Spoolman assignment rows based
    on which table the SELECT targets, so tests can exercise either mode."""

    def __init__(
        self,
        printer_name: str,
        legacy: list[SimpleNamespace] | None = None,
        spoolman: list[SimpleNamespace] | None = None,
    ):
        self._printer = SimpleNamespace(name=printer_name)
        self._legacy = legacy or []
        self._spoolman = spoolman or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, key):
        return self._printer

    async def execute(self, statement):
        table = statement.get_final_froms()[0].name
        if table == "spoolman_slot_assignments":
            return _FakeAssignmentsResult(self._spoolman)
        return _FakeAssignmentsResult(self._legacy)


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


def _patches(session):
    """Common patch set: the fake session + stubbed printer state / emitters."""
    return (
        patch(
            "backend.app.services.spool_assignment_notifications.async_session",
            return_value=session,
        ),
        patch("backend.app.services.spool_assignment_notifications.printer_manager.get_status", return_value=None),
        patch(
            "backend.app.services.spool_assignment_notifications.ws_manager.send_missing_spool_assignment",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.app.services.spool_assignment_notifications.notification_service.on_print_missing_spool_assignment",
            new_callable=AsyncMock,
        ),
    )


@pytest.mark.asyncio
async def test_spoolman_only_assignment_suppresses_notification():
    """#1473 — trays bound only via spoolman_slot_assignments must NOT be
    flagged missing (the legacy spool_assignment table is empty in Spoolman
    mode, so checking it alone fired a false positive on every print)."""
    logger = logging.getLogger(__name__)
    data = {"ams_mapping": [0, 1], "raw_data": {}}  # print uses A1 + A2

    # Both used trays bound via Spoolman; legacy table empty.
    session = _FakeSession(
        "Printer A",
        legacy=[],
        spoolman=[SimpleNamespace(ams_id=0, tray_id=0), SimpleNamespace(ams_id=0, tray_id=1)],
    )
    p_session, p_status, p_ws, p_notify = _patches(session)
    with p_session, p_status, p_ws as mock_ws, p_notify as mock_notify:
        await notify_missing_spool_assignments_on_print_start(1, data, logger)

    mock_ws.assert_not_awaited()
    mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_spoolman_partial_coverage_flags_only_uncovered_tray():
    """A Spoolman assignment for A1 only, with a print using A1 + A2, flags
    A2 alone."""
    logger = logging.getLogger(__name__)
    data = {"ams_mapping": [0, 1], "raw_data": {}}

    session = _FakeSession(
        "Printer A",
        legacy=[],
        spoolman=[SimpleNamespace(ams_id=0, tray_id=0)],  # A1 only
    )
    p_session, p_status, p_ws, p_notify = _patches(session)
    with p_session, p_status, p_ws as mock_ws, p_notify as mock_notify:
        await notify_missing_spool_assignments_on_print_start(1, data, logger)

    mock_ws.assert_awaited_once()
    assert mock_ws.await_args.kwargs["missing_slots"] == [{"slot": "A2", "profile": "Unknown", "color": "Unknown"}]
    mock_notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_mixed_mode_union_covers_all_used_trays():
    """A1 bound in the legacy table, A2 bound in spoolman_slot_assignments —
    the union covers both used trays, so no notification fires."""
    logger = logging.getLogger(__name__)
    data = {"ams_mapping": [0, 1], "raw_data": {}}

    session = _FakeSession(
        "Printer A",
        legacy=[SimpleNamespace(ams_id=0, tray_id=0)],  # A1
        spoolman=[SimpleNamespace(ams_id=0, tray_id=1)],  # A2
    )
    p_session, p_status, p_ws, p_notify = _patches(session)
    with p_session, p_status, p_ws as mock_ws, p_notify as mock_notify:
        await notify_missing_spool_assignments_on_print_start(1, data, logger)

    mock_ws.assert_not_awaited()
    mock_notify.assert_not_awaited()
