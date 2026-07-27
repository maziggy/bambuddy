"""WebSocket dispatch-toast routing (#1625 follow-up).

Two contracts pinned here:

1. ``broadcast_to_user(uid, msg)`` only delivers to connections whose
   ``websocket.state.bambuddy_principal_user_id`` matches the target,
   and fans out to all when the target is None (auth-disabled path).
2. The six ``send_queue_item_*`` helpers serialize the right payload
   shape — the frontend toast reads exact field names + types.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.core.websocket import ConnectionManager


def _mock_conn(user_id: int | None):
    """Build a stand-in WebSocket-shaped object with the principal stamp."""
    conn = SimpleNamespace()
    conn.state = SimpleNamespace()
    conn.state.bambuddy_principal_user_id = user_id
    conn.send_text = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_broadcast_to_user_filters_by_principal_user_id():
    """A targeted broadcast only reaches the principal's connections."""
    mgr = ConnectionManager()
    alice = _mock_conn(7)
    bob = _mock_conn(8)
    anon = _mock_conn(None)  # auth-disabled session — skipped on targeted path
    mgr.active_connections = [alice, bob, anon]

    await mgr.broadcast_to_user(7, {"type": "queue_item_uploading", "queue_item_id": 1})

    alice.send_text.assert_awaited_once()
    bob.send_text.assert_not_awaited()
    anon.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_to_user_none_fans_out_to_all():
    """Auth-disabled installs route ``user_id=None`` to every connection
    via the regular broadcast — matches the legacy single-user toast
    behaviour where there was no per-user routing at all."""
    mgr = ConnectionManager()
    a = _mock_conn(None)
    b = _mock_conn(None)
    mgr.active_connections = [a, b]

    await mgr.broadcast_to_user(None, {"type": "queue_item_uploading", "queue_item_id": 1})

    a.send_text.assert_awaited_once()
    b.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_queue_item_uploading_carries_total_bytes():
    mgr = ConnectionManager()
    target = _mock_conn(42)
    mgr.active_connections = [target]

    await mgr.send_queue_item_uploading(
        user_id=42,
        queue_item_id=11,
        printer_id=1,
        printer_name="H2D-1",
        file_name="cube.3mf",
        total_bytes=12345,
    )

    payload = json.loads(target.send_text.await_args.args[0])
    assert payload == {
        "type": "queue_item_uploading",
        "queue_item_id": 11,
        "printer_id": 1,
        "printer_name": "H2D-1",
        "file_name": "cube.3mf",
        "total_bytes": 12345,
    }


@pytest.mark.asyncio
async def test_send_queue_item_upload_progress_computes_pct_server_side():
    """The toast renders the pct field verbatim — the backend has to
    compute it. Avoid divide-by-zero on a zero-byte upload."""
    mgr = ConnectionManager()
    target = _mock_conn(5)
    mgr.active_connections = [target]

    await mgr.send_queue_item_upload_progress(
        user_id=5,
        queue_item_id=3,
        bytes_transferred=50,
        total_bytes=200,
    )
    payload = json.loads(target.send_text.await_args.args[0])
    assert payload["pct"] == 25

    target.send_text.reset_mock()
    await mgr.send_queue_item_upload_progress(
        user_id=5,
        queue_item_id=3,
        bytes_transferred=0,
        total_bytes=0,
    )
    payload = json.loads(target.send_text.await_args.args[0])
    assert payload["pct"] == 0


@pytest.mark.asyncio
async def test_send_queue_item_failed_carries_reason_key():
    """The frontend looks up ``dispatchToast.failed.{reason}`` — so the
    backend must hand the toast a reason string the i18n can match."""
    mgr = ConnectionManager()
    target = _mock_conn(99)
    mgr.active_connections = [target]

    await mgr.send_queue_item_failed(
        user_id=99,
        queue_item_id=8,
        printer_id=2,
        reason="upload_failed",
    )
    payload = json.loads(target.send_text.await_args.args[0])
    assert payload == {
        "type": "queue_item_failed",
        "queue_item_id": 8,
        "printer_id": 2,
        "reason": "upload_failed",
    }
