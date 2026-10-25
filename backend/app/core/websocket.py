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


# A printer id no limited scope holds: what an archive event carries when its
# printer cannot be told, so only sockets that see every printer receive it.
_UNKNOWN_PRINTER = -1


async def _archive_printer_id(archive_id: Any) -> int | None:
    """The printer an archive belongs to (None: printer-less, seen by everyone)."""
    from sqlalchemy import select

    from backend.app.core.database import async_session
    from backend.app.models.archive import PrintArchive

    if not isinstance(archive_id, int):
        return _UNKNOWN_PRINTER
    try:
        async with async_session() as db:
            row = (await db.execute(select(PrintArchive.printer_id).where(PrintArchive.id == archive_id))).first()
    except Exception:
        logger.exception("Could not look up the printer of archive %s for a live update", archive_id)
        return _UNKNOWN_PRINTER
    if row is None:
        return _UNKNOWN_PRINTER
    return row[0]


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

    async def broadcast_to_users(self, user_ids: set[int], message: dict[str, Any]):
        """Send a message to every connection of any of ``user_ids``, as
        ``broadcast_to_user`` does for one."""
        if not self.active_connections:
            return

        data = json.dumps(message)
        printer_id = _message_printer_id(message)
        async with self._lock:
            disconnected = []
            for connection in self.active_connections:
                conn_uid = getattr(connection.state, "bambuddy_principal_user_id", None)
                if conn_uid is None or conn_uid not in user_ids or not _may_receive(connection, printer_id):
                    continue
                try:
                    await connection.send_text(data)
                except Exception:
                    disconnected.append(connection)

            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)

    async def close_user(self, user_id: int):
        """Close a deleted user's sockets, which would otherwise keep receiving
        the messages of whoever is given their id next."""
        await self._close(lambda c: getattr(c.state, "bambuddy_principal_user_id", None) == user_id)

    async def close_signed_in(self):
        """Close every signed-in user's socket, after everyone is signed out (a
        backup restore). API-key and anonymous sockets stay: nothing ended
        theirs."""
        await self._close(lambda c: getattr(c.state, "bambuddy_principal_user_id", None) is not None)

    async def close_anonymous(self):
        """Close the sockets opened while authentication was off, which have
        every printer, once it is turned on."""
        await self._close(
            lambda c: getattr(c.state, "bambuddy_scope_principal", (None, None)) in ((None, None), ("", None))
        )

    async def close_api_keys(self, key_ids):
        """Close the sockets opened with deleted API keys. Their scope is looked
        up by key id on every refresh: SQLite gives the id to the next key."""
        key_ids = set(key_ids)
        await self._close(lambda c: getattr(c.state, "bambuddy_scope_principal", (None, None))[1] in key_ids)

    async def _close(self, matches):
        async with self._lock:
            theirs = [c for c in self.active_connections if matches(c)]
            for connection in theirs:
                self.active_connections.remove(connection)
        for connection in theirs:
            try:
                await connection.close(code=4401)
            except Exception:  # noqa: BLE001 -- already gone
                pass

    async def refresh_printer_scopes(self):
        """Recompute every connection's printer scope (#1727).

        Called after an admin changes which printers a group may see, or who
        is in a group, so open dashboards stop (or start) receiving those
        printers' events without a reconnect.
        """
        from backend.app.core.auth import (
            is_auth_enabled,
            principal_may_connect,
            principal_printer_scope,
            usable_api_key_prefix,
        )
        from backend.app.core.database import async_session
        from backend.app.core.printer_scope import ALL_PRINTERS, PrinterScope

        async with self._lock:
            connections = list(self.active_connections)
        if not connections:
            return

        orphaned = []
        try:
            async with async_session() as db:
                auth_enabled = await is_auth_enabled(db)
                for connection in connections:
                    if not auth_enabled:
                        connection.state.bambuddy_printer_scope = ALL_PRINTERS
                        continue
                    username, api_key_id = getattr(connection.state, "bambuddy_scope_principal", (None, None))
                    if api_key_id is not None:
                        # The key is looked up by id, which SQLite gives to the
                        # next key created: a socket whose key is gone, or whose
                        # id another key now has, is closed, not re-scoped (a key
                        # rotated from the command line, which can't close it).
                        # Disabled or expired counts as gone: REST refuses it.
                        prefix = await usable_api_key_prefix(db, api_key_id)
                        if prefix is None or prefix != getattr(connection.state, "bambuddy_api_key_prefix", prefix):
                            connection.state.bambuddy_printer_scope = PrinterScope(frozenset())
                            orphaned.append(connection)
                            continue
                    if not await principal_may_connect(db, username, api_key_id):
                        # websocket:connect was taken away (a group or key change)
                        connection.state.bambuddy_printer_scope = PrinterScope(frozenset())
                        orphaned.append(connection)
                        continue
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
            return
        if orphaned:
            await self._close(lambda c: c in orphaned)

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

    async def send_print_confirm_request(self, printer_id: int, data: dict, user_ids: set[int] | None = None):
        """Ask connected clients for a post-print outcome verdict (#1898).

        ``user_ids`` are the users who may answer it; None asks everyone who
        can see the printer.
        """
        message = {"type": "print_confirm_request", "printer_id": printer_id, "data": data}
        if user_ids is None:
            await self.broadcast(message)
        else:
            await self.broadcast_to_users(user_ids, message)

    async def send_archive_created(self, archive: dict):
        """Notify clients that a new archive was created.

        Only that one was, on which printer: whose it is and what it holds stays
        with the archive routes, which every socket's client asks next anyway.
        """
        await self.broadcast(
            {
                "type": "archive_created",
                "data": {"printer_id": archive.get("printer_id")},
            }
        )

    async def send_archive_updated(self, archive: dict):
        """Notify clients that an archive was updated.

        Only sockets that may see the archive's printer receive it: callers send
        just the changed fields, so the printer is looked up here when missing.
        As for a new archive, the message says no more than that.
        """
        printer_id = archive.get("printer_id")
        if not isinstance(printer_id, int):
            printer_id = await _archive_printer_id(archive.get("id"))
        await self.broadcast({"type": "archive_updated", "printer_id": printer_id, "data": {"printer_id": printer_id}})

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
