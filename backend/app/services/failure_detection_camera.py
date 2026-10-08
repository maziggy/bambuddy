"""Shared camera capture for AI failure detection providers."""

import logging

from backend.app.core.database import async_session
from backend.app.models.printer import Printer

logger = logging.getLogger(__name__)


async def capture_detection_frame(printer_id: int, *, session_factory=async_session, timeout: int = 20) -> bytes | None:
    """Capture a JPEG, sharing live camera buffers to avoid competing readers."""
    # Late import to avoid cycles at module load time
    from backend.app.services.camera import capture_camera_frame_bytes
    from backend.app.services.external_camera import capture_frame as capture_external_frame

    async with session_factory() as db:
        printer = await db.get(Printer, printer_id)
    if printer is None:
        raise ValueError(f"Printer {printer_id} not found")

    if printer.external_camera_enabled and printer.external_camera_url:
        # Same rule as the built-in branch below, which this used to skip:
        # an external camera is single-reader too, so polling while a viewer
        # is attached just fails (#2707).
        from backend.app.api.routes.camera import live_frame_for_capture

        defer, buffered = live_frame_for_capture(printer_id)
        if defer:
            if buffered:
                return buffered
            logger.info(
                "AI failure detection: viewer attached for printer %s but buffer empty; "
                "skipping this poll to avoid competing camera handle (#2707)",
                printer_id,
            )
            return None
        return await capture_external_frame(
            printer.external_camera_url,
            printer.external_camera_type,
            timeout=timeout,
            snapshot_url=printer.external_camera_snapshot_url,
        )

    # Reuse the fan-out broadcaster's buffered frame when a viewer is
    # already watching — avoids opening a second concurrent RTSP socket
    # on printers that allow only one camera connection (e.g. X2D
    # firmware 01.01.00.00; see #1271). Buffered frame is <1s old while
    # a viewer is connected.
    #
    # When a viewer is attached but no frame is buffered yet (startup
    # race, mid-reconnect), we DELIBERATELY skip this poll cycle instead
    # of falling through to capture_camera_frame_bytes. Opening a fresh
    # RTSP/chamber socket would compete with the live viewer and kick
    # the fan-out connection on most firmwares — exactly the freeze
    # reported in #1348. The poll loop retries in ~10s.
    from backend.app.api.routes.camera import is_stream_active, try_get_active_buffered_frame

    if is_stream_active(printer_id):
        buffered = try_get_active_buffered_frame(printer_id)
        if buffered:
            return buffered
        logger.info(
            "AI failure detection: viewer attached for printer %s but buffer empty; skipping this poll to avoid competing camera socket (#1348)",
            printer_id,
        )
        return None

    return await capture_camera_frame_bytes(
        ip_address=printer.ip_address,
        access_code=printer.access_code,
        model=printer.model,
        timeout=timeout,
    )
