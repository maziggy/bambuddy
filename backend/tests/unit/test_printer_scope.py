"""PrinterScope semantics and WebSocket fan-out filtering (#1727)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.app.core.printer_scope import ALL_PRINTERS, PrinterScope
from backend.app.core.websocket import ConnectionManager


class TestPrinterScope:
    def test_unrestricted_allows_everything(self):
        assert ALL_PRINTERS.is_unrestricted
        assert ALL_PRINTERS.allows(1)
        assert ALL_PRINTERS.where(None) is None

    def test_restricted_allows_only_its_printers(self):
        scope = PrinterScope(frozenset({1, 2}))
        assert scope.allows(1)
        assert not scope.allows(3)
        assert scope.filter_ids([3, 2, 1]) == [2, 1]

    def test_no_printer_is_always_in_scope(self):
        # Rows not bound to a printer (orphaned archives, model-based jobs)
        assert PrinterScope(frozenset()).allows(None)

    def test_ensure_reports_missing_not_forbidden(self):
        with pytest.raises(HTTPException) as exc:
            PrinterScope(frozenset({1})).ensure(2)
        assert exc.value.status_code == 404
        assert exc.value.detail == "Printer not found"

    def test_intersect(self):
        team = PrinterScope(frozenset({1, 2}))
        assert ALL_PRINTERS.intersect(team) == team
        assert team.intersect(ALL_PRINTERS) == team
        assert team.intersect(PrinterScope(frozenset({2, 3}))) == PrinterScope(frozenset({2}))


def _socket(scope: PrinterScope | None):
    state = SimpleNamespace()
    if scope is not None:
        state.bambuddy_printer_scope = scope
    return SimpleNamespace(state=state, send_text=AsyncMock())


class TestBroadcastFiltering:
    @pytest.mark.asyncio
    async def test_printer_events_reach_only_sockets_that_may_see_the_printer(self):
        mgr = ConnectionManager()
        team = _socket(PrinterScope(frozenset({1})))
        everyone = _socket(ALL_PRINTERS)
        mgr.active_connections = [team, everyone]

        await mgr.send_printer_status(2, {})

        team.send_text.assert_not_awaited()
        everyone.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_printer_id_inside_data_is_honoured(self):
        mgr = ConnectionManager()
        team = _socket(PrinterScope(frozenset({1})))
        mgr.active_connections = [team]

        await mgr.send_archive_created({"id": 9, "printer_id": 2})
        team.send_text.assert_not_awaited()
        await mgr.send_archive_created({"id": 10, "printer_id": 1})
        team.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_messages_about_no_printer_reach_everyone(self):
        mgr = ConnectionManager()
        team = _socket(PrinterScope(frozenset()))
        mgr.active_connections = [team]

        await mgr.broadcast({"type": "inventory_changed"})

        team.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_socket_without_a_scope_gets_no_printer_events(self):
        """Fail closed: a socket that missed the connect-time stamp hears nothing printer-bound."""
        mgr = ConnectionManager()
        unstamped = _socket(None)
        mgr.active_connections = [unstamped]

        await mgr.send_printer_status(1, {})
        unstamped.send_text.assert_not_awaited()
        await mgr.broadcast({"type": "inventory_changed"})
        unstamped.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_targeted_broadcast_is_filtered_too(self):
        mgr = ConnectionManager()
        own = _socket(PrinterScope(frozenset({1})))
        own.state.bambuddy_principal_user_id = 7
        mgr.active_connections = [own]

        await mgr.send_queue_item_acked(7, queue_item_id=1, printer_id=2)

        own.send_text.assert_not_awaited()


class TestScopeRefresh:
    @pytest.mark.asyncio
    async def test_a_failed_refresh_fails_closed(self):
        """Old scopes may be wider than what was just granted, so they can't be kept."""
        from unittest.mock import patch

        mgr = ConnectionManager()
        socket = _socket(ALL_PRINTERS)
        socket.state.bambuddy_scope_principal = ("member", None)
        socket.close = AsyncMock()
        mgr.active_connections = [socket]

        with (
            patch("backend.app.core.auth.is_auth_enabled", AsyncMock(return_value=True)),
            patch("backend.app.core.auth.principal_printer_scope", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            await mgr.refresh_printer_scopes()

        assert socket.state.bambuddy_printer_scope == PrinterScope(frozenset())
        socket.close.assert_awaited_once_with(code=4401)
