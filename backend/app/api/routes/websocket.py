from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.core.websocket import ws_manager
from backend.app.services.printer_manager import printer_manager, printer_state_to_dict


router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await ws_manager.connect(websocket)

    try:
        # Send initial status of all printers
        statuses = printer_manager.get_all_statuses()
        for printer_id, state in statuses.items():
            await websocket.send_json({
                "type": "printer_status",
                "printer_id": printer_id,
                "data": printer_state_to_dict(state),
            })

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
                        await websocket.send_json({
                            "type": "printer_status",
                            "printer_id": printer_id,
                            "data": printer_state_to_dict(state),
                        })

    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)
