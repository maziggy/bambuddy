import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


def _message_printer_id(message: dict[str, Any]) -> int | None:
    """The printer a broadcast is about, if any: top-level or inside ``data``."""
    printer_id = message.get("printer_id")
    if printer_id is None:
        data = message.get("data")
        if isinstance(data, dict):
            printer_id = data.get("printer_id")
    return printer_id if isinstance(printer_id, int) else None


def _may_receive(connection: WebSocket, printer_id: int | None) -> bool:
    """Whether ``connection``'s printer scope (#1727) covers ``printer_id``.

    The scope is stamped on the socket at connect (``routes/websocket.py``).
    A socket without one is refused anything printer-bound, so a connection
    that slipped past the stamping can't receive every printer's events.
    """
    if printer_id is None:
        return True
    scope = getattr(connection.state, "bambuddy_printer_scope", None)
    return scope is not None and scope.allows(printer_id)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)

    async def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]):
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return

        data = json.dumps(message)
        printer_id = _message_printer_id(message)
        async with self._lock:
            disconnected = []
            for connection in self.active_connections:
                if not _may_receive(connection, printer_id):
                    continue
                try:
                    await connection.send_text(data)
                except Exception:
                    disconnected.append(connection)

            # Clean up disconnected clients
            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)

    async def broadcast_to_user(self, user_id: int | None, message: dict[str, Any]):
        """Send a message to every connection authenticated as the given user.

        When ``user_id`` is None the message fans out to all connections —
        this is the auth-disabled single-user path, where neither the queue
        item's ``created_by_id`` nor the WS principal is set, and the
        existing fan-out semantics are exactly what the user wants.

        Per-user routing reads ``websocket.state.bambuddy_principal_user_id``
        stamped at connect time (``routes/websocket.py``). Connections
        without a stamped id are skipped on the targeted path so an
        anonymous reader never receives another user's dispatch toast.
        """
        if user_id is None:
            await self.broadcast(message)
            return

        if not self.active_connections:
            return

        data = json.dumps(message)
        printer_id = _message_printer_id(message)
        async with self._lock:
            disconnected = []
            for connection in self.active_connections:
                conn_uid = getattr(connection.state, "bambuddy_principal_user_id", None)
                if conn_uid != user_id or not _may_receive(connection, printer_id):
                    continue
                try:
                    await connection.send_text(data)
                except Exception:
                    disconnected.append(connection)

            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)

    async def refresh_printer_scopes(self):
        """Recompute every connection's printer scope (#1727).

        Called after an admin changes which printers a group may see, or who
        is in a group, so open dashboards stop (or start) receiving those
        printers' events without a reconnect.
        """
        from backend.app.core.auth import is_auth_enabled, principal_printer_scope
        from backend.app.core.database import async_session
        from backend.app.core.printer_scope import ALL_PRINTERS, PrinterScope

        async with self._lock:
            connections = list(self.active_connections)
        if not connections:
            return
        try:
            async with async_session() as db:
                auth_enabled = await is_auth_enabled(db)
                for connection in connections:
                    if not auth_enabled:
                        connection.state.bambuddy_printer_scope = ALL_PRINTERS
                        continue
                    username, api_key_id = getattr(connection.state, "bambuddy_scope_principal", (None, None))
                    connection.state.bambuddy_printer_scope = await principal_printer_scope(db, username, api_key_id)
        except Exception:  # SEC-AUTH-EXC: refresh failed → fail closed (empty scope, then disconnect to re-auth)
            # The old scopes may be wider than what was just granted, so they
            # can't be kept. Drop every socket to no printers and close it with
            # the "unauthorised" code: the SPA mints a new token and reconnects,
            # and its scope is worked out afresh at connect.
            logger.warning("WebSocket printer scope refresh failed; disconnecting clients", exc_info=True)
            for connection in connections:
                connection.state.bambuddy_printer_scope = PrinterScope(frozenset())
                try:
                    await connection.close(code=4401)
                except Exception:  # noqa: BLE001 -- already gone; disconnect() cleans it up
                    pass

    async def send_printer_status(self, printer_id: int, status: dict):
        """Send printer status update to all clients."""
        await self.broadcast(
            {
                "type": "printer_status",
                "printer_id": printer_id,
                "data": status,
            }
        )

    async def send_print_start(self, printer_id: int, data: dict):
        """Notify clients that a print has started."""
        await self.broadcast(
            {
                "type": "print_start",
                "printer_id": printer_id,
                "data": data,
            }
        )

    async def send_print_complete(self, printer_id: int, data: dict):
        """Notify clients that a print has completed."""
        await self.broadcast(
            {
                "type": "print_complete",
                "printer_id": printer_id,
                "data": data,
            }
        )

    async def send_print_confirm_request(self, printer_id: int, data: dict):
        """Ask connected clients for a post-print outcome verdict (#1898)."""
        await self.broadcast(
            {
                "type": "print_confirm_request",
                "printer_id": printer_id,
                "data": data,
            }
        )

    async def send_archive_created(self, archive: dict):
        """Notify clients that a new archive was created."""
        await self.broadcast(
            {
                "type": "archive_created",
                "data": archive,
            }
        )

    async def send_archive_updated(self, archive: dict):
        """Notify clients that an archive was updated."""
        await self.broadcast(
            {
                "type": "archive_updated",
                "data": archive,
            }
        )

    async def send_queue_item_uploading(
        self,
        user_id: int | None,
        queue_item_id: int,
        printer_id: int,
        printer_name: str | None,
        file_name: str,
        total_bytes: int,
    ):
        """Toast trigger: scheduler picked the item up, FTP upload starts."""
        await self.broadcast_to_user(
            user_id,
            {
                "type": "queue_item_uploading",
                "queue_item_id": queue_item_id,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "file_name": file_name,
                "total_bytes": total_bytes,
            },
        )

    async def send_queue_item_upload_progress(
        self,
        user_id: int | None,
        queue_item_id: int,
        bytes_transferred: int,
        total_bytes: int,
    ):
        """Toast update: throttled byte-level progress during the FTP upload."""
        pct = int(round(100 * bytes_transferred / total_bytes)) if total_bytes else 0
        await self.broadcast_to_user(
            user_id,
            {
                "type": "queue_item_upload_progress",
                "queue_item_id": queue_item_id,
                "bytes_transferred": bytes_transferred,
                "total_bytes": total_bytes,
                "pct": pct,
            },
        )

    async def send_queue_item_acked(
        self,
        user_id: int | None,
        queue_item_id: int,
        printer_id: int,
    ):
        """Toast trigger: watchdog confirmed the printer transitioned out of pre_state."""
        await self.broadcast_to_user(
            user_id,
            {
                "type": "queue_item_acked",
                "queue_item_id": queue_item_id,
                "printer_id": printer_id,
            },
        )

    async def send_queue_item_failed(
        self,
        user_id: int | None,
        queue_item_id: int,
        printer_id: int | None,
        reason: str,
    ):
        """Toast trigger: dispatch failed at any stage. Toast turns red, auto-dismisses."""
        await self.broadcast_to_user(
            user_id,
            {
                "type": "queue_item_failed",
                "queue_item_id": queue_item_id,
                "printer_id": printer_id,
                "reason": reason,
            },
        )

    async def send_missing_spool_assignment(
        self,
        printer_id: int,
        printer_name: str,
        missing_slots: list[dict[str, str]],
    ):
        """Notify clients that a print started with missing spool assignments."""
        await self.broadcast(
            {
                "type": "missing_spool_assignment",
                "printer_id": printer_id,
                "printer_name": printer_name,
                "missing_slots": missing_slots,
            }
        )


# Global connection manager
ws_manager = ConnectionManager()
