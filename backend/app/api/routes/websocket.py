"""GHSA-r2qv follow-up — WebSocket auth gate.

Previously ``/api/v1/ws`` accepted *any* network client and immediately
streamed every ``printer_status`` / ``print_start`` / ``print_complete``
/ ``archive_*`` / ``inventory_changed`` broadcast back to it. That is
the GHSA-gc24 shape on a different protocol — anyone who could reach
the HTTP port could subscribe to every printer event in the system.

This endpoint now validates a short-lived token (minted by
``POST /api/v1/auth/ws-token`` behind ``Permission.WEBSOCKET_CONNECT``)
*before* ``websocket.accept()``. When auth is disabled, no token is
required (the legacy SPA-friendly path). The token is reused across
reconnects within its 60-minute window so a brief network blip does
not require a round-trip to the auth router.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from backend.app.core.auth import is_auth_enabled, principal_printer_scope, verify_websocket_token_principal
from backend.app.core.database import async_session
from backend.app.core.printer_scope import ALL_PRINTERS, PrinterScope
from backend.app.core.websocket import ws_manager
from backend.app.models.user import User
from backend.app.services.printer_manager import printer_manager, printer_state_to_dict

logger = logging.getLogger(__name__)
router = APIRouter()

# 4401 mirrors the WebSocket "unauthorised" application close code
# convention used by Sec-WebSocket-Protocol authors (private-use range
# is 4000-4999 per RFC 6455). The SPA distinguishes 4401 from network
# drops and refetches a token instead of retrying with the old one.
_WS_CLOSE_UNAUTHORIZED = 4401


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)) -> None:
    """WebSocket endpoint for real-time updates.

    Connection auth (GHSA-r2qv follow-up):

    - Auth disabled  → connect without a token, identical to the prior
      behaviour (single-user / local-network deployments).
    - Auth enabled   → ``?token=<value>`` query param must hold an
      unexpired token minted via ``POST /api/v1/auth/ws-token``.
      Missing / invalid / expired token → ``close(code=4401)`` *before*
      ``accept()`` so no ``ws_manager.broadcast`` ever reaches the
      caller (broadcasts walk ``active_connections`` blindly — letting
      an unauthenticated socket into that list is a fan-out leak).

    The auth check is fail-closed at every error path: a DB exception
    while reading the ``auth_enabled`` setting closes the connection
    rather than admitting the caller.
    """
    # Authenticate before accept() so an unauth caller never lands in
    # ws_manager.active_connections (where broadcasts blindly fan out).
    try:
        async with async_session() as db:
            auth_required = await is_auth_enabled(db)
    except Exception:  # SEC-AUTH-EXC: DB failure on auth probe → fail-closed (refuse connect), matches is_auth_enabled itself which returns True on error
        logger.error("WebSocket auth probe failed; refusing connection", exc_info=True)
        await websocket.close(code=_WS_CLOSE_UNAUTHORIZED)
        return

    principal: str | None = None
    api_key_id: int | None = None
    printer_scope: PrinterScope = ALL_PRINTERS
    if auth_required:
        if not token:
            logger.info("WebSocket connect refused: no token (auth enabled)")
            await websocket.close(code=_WS_CLOSE_UNAUTHORIZED)
            return
        token_principal = await verify_websocket_token_principal(token)
        if token_principal is None:
            logger.info("WebSocket connect refused: invalid or expired token")
            await websocket.close(code=_WS_CLOSE_UNAUTHORIZED)
            return
        principal, api_key_id = token_principal
        # Which printers this socket may hear about (#1727). Fail-closed: if
        # it can't be worked out the socket is refused rather than admitted
        # with every printer.
        try:
            async with async_session() as db:
                printer_scope = await principal_printer_scope(db, principal, api_key_id)
        except Exception:  # SEC-AUTH-EXC: scope lookup failure → refuse connect (fail-closed)
            logger.error("WebSocket printer scope lookup failed; refusing connection", exc_info=True)
            await websocket.close(code=_WS_CLOSE_UNAUTHORIZED)
            return

    # Token verified (or auth disabled); now safe to admit the connection.
    logger.info("WebSocket client connecting (principal=%s)", principal if principal else "<anonymous>")
    # Stamped before connect() puts the socket in the broadcast list, so no
    # broadcast can reach it unfiltered (ws_manager refuses printer-bound
    # messages to a socket without a scope anyway).
    websocket.state.bambuddy_printer_scope = printer_scope
    websocket.state.bambuddy_scope_principal = (principal, api_key_id)
    await ws_manager.connect(websocket)
    # Stash on connection state for any future per-message permission
    # logic; today the message handlers are read-only and only respond
    # to the requesting socket, so the stash is informational. The
    # explicit attribute (rather than a side dict) means a future
    # ``broadcast_to_principal()`` helper can filter on it without
    # touching every call site.
    websocket.state.bambuddy_principal = principal
    # Resolve principal username → User.id once at connect so
    # ``ws_manager.broadcast_to_user()`` can filter without re-querying
    # per message. Auth-disabled path keeps None (broadcast_to_user fans
    # out to all when target is None — matches the legacy single-user
    # toast behaviour). API-keyed principal is empty string → None.
    principal_user_id: int | None = None
    if principal:
        try:
            async with async_session() as db:
                row = await db.execute(select(User.id).where(User.username == principal))
                principal_user_id = row.scalar_one_or_none()
        except Exception:  # SEC-AUTH-EXC: resolution failure is non-fatal — degrades to no per-user routing
            logger.warning("WebSocket principal resolve failed for %s", principal, exc_info=True)
    websocket.state.bambuddy_principal_user_id = principal_user_id
    logger.info("WebSocket client connected")

    try:
        # Send initial status of all printers.
        statuses = {
            pid: state
            for pid, state in printer_manager.get_all_statuses().items()
            if websocket.state.bambuddy_printer_scope.allows(pid)
        }
        for printer_id, state in statuses.items():
            await websocket.send_json(
                {
                    "type": "printer_status",
                    "printer_id": printer_id,
                    "data": printer_state_to_dict(
                        state,
                        printer_id,
                        printer_manager.get_model(printer_id),
                        printer_manager.get_drying_targets(printer_id),
                    ),
                }
            )

        logger.info("Sent initial status for %s printers", len(statuses))

        # Keep connection alive and handle incoming messages.
        while True:
            data = await websocket.receive_json()

            # Handle ping/pong for keepalive
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            # Handle status request
            elif data.get("type") == "get_status":
                printer_id = data.get("printer_id")
                if printer_id and websocket.state.bambuddy_printer_scope.allows(printer_id):
                    state = printer_manager.get_status(printer_id)
                    if state:
                        await websocket.send_json(
                            {
                                "type": "printer_status",
                                "printer_id": printer_id,
                                "data": printer_state_to_dict(
                                    state,
                                    printer_id,
                                    printer_manager.get_model(printer_id),
                                    printer_manager.get_drying_targets(printer_id),
                                ),
                            }
                        )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e, exc_info=True)
        await ws_manager.disconnect(websocket)
