"""Live archive updates reach only sockets that may see the archive's printer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.core import websocket
from backend.app.core.printer_scope import PrinterScope

pytestmark = pytest.mark.asyncio


def _socket(printer_ids):
    scope = PrinterScope() if printer_ids is None else PrinterScope(frozenset(printer_ids))
    return SimpleNamespace(state=SimpleNamespace(bambuddy_printer_scope=scope), send_text=AsyncMock())


@pytest.mark.parametrize(
    ("archive_printer", "expected"),
    [
        (1, {"team_a": True, "team_b": False, "everyone": True}),
        (None, {"team_a": True, "team_b": True, "everyone": True}),  # printer-less: in every scope
        (websocket._UNKNOWN_PRINTER, {"team_a": False, "team_b": False, "everyone": True}),
    ],
)
async def test_an_update_follows_the_archive_printer(archive_printer, expected):
    manager = websocket.ConnectionManager()
    sockets = {"team_a": _socket({1}), "team_b": _socket({2}), "everyone": _socket(None)}
    manager.active_connections = list(sockets.values())

    with patch.object(websocket, "_archive_printer_id", new=AsyncMock(return_value=archive_printer)):
        await manager.send_archive_updated({"id": 7, "cost": 1.5})

    assert {name: s.send_text.await_count == 1 for name, s in sockets.items()} == expected


async def test_an_archive_that_cannot_be_looked_up_reaches_only_unrestricted_sockets():
    with patch("backend.app.core.database.async_session", side_effect=RuntimeError("db down")):
        assert await websocket._archive_printer_id(7) == websocket._UNKNOWN_PRINTER
    assert await websocket._archive_printer_id(None) == websocket._UNKNOWN_PRINTER
