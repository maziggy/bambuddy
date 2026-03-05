"""Bug report endpoint for submitting user bug reports to GitHub."""

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.api.routes.support import (
    _apply_log_level,
    _collect_support_info,
    _get_debug_setting,
    _get_recent_sanitized_logs,
    _set_debug_setting,
)
from backend.app.core.database import async_session
from backend.app.services.bug_report import submit_report
from backend.app.services.printer_manager import printer_manager

router = APIRouter(prefix="/bug-report", tags=["bug-report"])
logger = logging.getLogger(__name__)

LOG_COLLECTION_SECONDS = 30


class BugReportRequest(BaseModel):
    description: str
    email: str | None = None
    screenshot_base64: str | None = None
    include_support_info: bool = True


class BugReportResponse(BaseModel):
    success: bool
    message: str
    issue_url: str | None = None
    issue_number: int | None = None


async def _collect_debug_logs() -> str:
    """Enable debug logging, push all printers, wait, then collect logs."""
    # Check if debug was already enabled
    async with async_session() as db:
        was_debug, _ = await _get_debug_setting(db)

    # Enable debug logging
    if not was_debug:
        async with async_session() as db:
            await _set_debug_setting(db, True)
        _apply_log_level(True)
        logger.info("Bug report: temporarily enabled debug logging")

    # Send push_all to all connected printers
    for printer_id in list(printer_manager._clients.keys()):
        try:
            printer_manager.request_status_update(printer_id)
        except Exception:
            logger.debug("Failed to push_all for printer %s", printer_id)

    # Wait for logs to accumulate
    await asyncio.sleep(LOG_COLLECTION_SECONDS)

    # Collect logs
    logs = await _get_recent_sanitized_logs()

    # Restore previous log level if it wasn't debug before
    if not was_debug:
        async with async_session() as db:
            await _set_debug_setting(db, False)
        _apply_log_level(False)
        logger.info("Bug report: restored normal logging")

    return logs


@router.post("/submit", response_model=BugReportResponse)
async def submit_bug_report(report: BugReportRequest):
    """Submit a bug report. No auth required — anyone should be able to report bugs."""
    support_info = None
    if report.include_support_info:
        try:
            support_info = await _collect_support_info()
            logs = await _collect_debug_logs()
            if logs:
                support_info["recent_logs"] = logs
        except Exception:
            logger.exception("Failed to collect support info for bug report")

    result = await submit_report(
        description=report.description,
        reporter_email=report.email,
        screenshot_base64=report.screenshot_base64,
        support_info=support_info,
    )
    return BugReportResponse(**result)
