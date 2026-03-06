import logging

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.core.auth import ALGORITHM, SECRET_KEY, _validate_api_key, get_user_by_username, is_auth_enabled
from backend.app.core.database import async_session
from backend.app.core.permissions import Permission
from backend.app.core.websocket import ws_manager
from backend.app.services.background_dispatch import background_dispatch
from backend.app.services.printer_manager import printer_manager, printer_state_to_dict

logger = logging.getLogger(__name__)
router = APIRouter()


async def _authenticate_ws_token(token: str) -> bool:
    """Validate a WebSocket auth token (JWT or API key) and check WEBSOCKET_CONNECT permission."""
    async with async_session() as db:
        # API key (bb_ prefix)
        if token.startswith("bb_"):
            api_key = await _validate_api_key(db, token)
            return api_key is not None

        # JWT
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if not username:
                return False
        except jwt.exceptions.PyJWTError:
            return False

        user = await get_user_by_username(db, username)
        if user is None or not user.is_active:
            return False

        return user.has_permission(Permission.WEBSOCKET_CONNECT.value)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)):
    """WebSocket endpoint for real-time updates."""
    # Auth check before accepting the connection
    async with async_session() as db:
        auth_enabled = await is_auth_enabled(db)

    if auth_enabled:
        if not token or not await _authenticate_ws_token(token):
            await websocket.close(code=1008)
            return

    logger.info("WebSocket client connecting...")
    await ws_manager.connect(websocket)
    logger.info("WebSocket client connected")

    try:
        # Send initial status of all printers
        statuses = printer_manager.get_all_statuses()
        for printer_id, state in statuses.items():
            await websocket.send_json(
                {
                    "type": "printer_status",
                    "printer_id": printer_id,
                    "data": printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
                }
            )

        dispatch_state = await background_dispatch.get_state()
        if (dispatch_state.get("dispatched", 0) + dispatch_state.get("processing", 0)) > 0:
            await websocket.send_json(
                {
                    "type": "background_dispatch",
                    "data": dispatch_state,
                }
            )
        logger.info("Sent initial status for %s printers", len(statuses))

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_json()

            # Handle ping/pong for keepalive
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            # Handle status request
            elif data.get("type") == "get_status":
                printer_id = data.get("printer_id")
                if printer_id:
                    state = printer_manager.get_status(printer_id)
                    if state:
                        await websocket.send_json(
                            {
                                "type": "printer_status",
                                "printer_id": printer_id,
                                "data": printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
                            }
                        )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e, exc_info=True)
        await ws_manager.disconnect(websocket)
