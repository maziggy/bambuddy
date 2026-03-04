"""Bug report service — posts to the bambuddy.cool relay which holds the GitHub PAT."""

import logging
import time

import httpx

from backend.app.core.config import BUG_REPORT_RELAY_URL
from backend.app.core.database import async_session
from backend.app.models.bug_report import BugReport

logger = logging.getLogger(__name__)

# Rate limiting: max 5 reports per hour
_rate_limit_window = 3600
_rate_limit_max = 5
_rate_limit_timestamps: list[float] = []


def _check_rate_limit() -> bool:
    """Check if rate limit allows a new report. Returns True if allowed."""
    now = time.time()
    _rate_limit_timestamps[:] = [t for t in _rate_limit_timestamps if now - t < _rate_limit_window]
    if len(_rate_limit_timestamps) >= _rate_limit_max:
        return False
    _rate_limit_timestamps.append(now)
    return True


async def submit_report(
    description: str,
    reporter_email: str | None,
    screenshot_base64: str | None,
    support_info: dict | None,
) -> dict:
    """Submit a bug report via the bambuddy.cool relay."""
    if not _check_rate_limit():
        return {
            "success": False,
            "message": "Rate limit exceeded. Please try again later.",
            "issue_url": None,
            "issue_number": None,
        }

    if not BUG_REPORT_RELAY_URL:
        return {
            "success": False,
            "message": "Bug reporting is not configured. BUG_REPORT_RELAY_URL is not set.",
            "issue_url": None,
            "issue_number": None,
        }

    # Build relay payload — email is sent to relay for maintainer notification + issue body
    payload: dict = {"description": description}
    if reporter_email:
        payload["reporter_email"] = reporter_email
    if screenshot_base64:
        payload["screenshot_base64"] = screenshot_base64
    if support_info:
        payload["support_info"] = support_info

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(BUG_REPORT_RELAY_URL, json=payload)
            if resp.status_code != 200:
                error_msg = f"Relay returned HTTP {resp.status_code}"
                logger.error("%s at %s", error_msg, BUG_REPORT_RELAY_URL)
                async with async_session() as db:
                    report = BugReport(
                        description=description,
                        reporter_email=reporter_email,
                        status="failed",
                        error_message=error_msg,
                    )
                    db.add(report)
                    await db.commit()
                return {
                    "success": False,
                    "message": "Bug report relay is not available. Please try again later.",
                    "issue_url": None,
                    "issue_number": None,
                }
            relay_data = resp.json()
    except Exception:
        logger.exception("Failed to reach bug report relay at %s", BUG_REPORT_RELAY_URL)
        async with async_session() as db:
            report = BugReport(
                description=description,
                reporter_email=reporter_email,
                status="failed",
                error_message="Failed to reach bug report relay",
            )
            db.add(report)
            await db.commit()

        return {
            "success": False,
            "message": "Failed to submit bug report. Please try again later.",
            "issue_url": None,
            "issue_number": None,
        }

    if not relay_data.get("success"):
        async with async_session() as db:
            report = BugReport(
                description=description,
                reporter_email=reporter_email,
                status="failed",
                error_message=relay_data.get("message", "Relay returned failure"),
            )
            db.add(report)
            await db.commit()

        return {
            "success": False,
            "message": relay_data.get("message", "Failed to create bug report."),
            "issue_url": None,
            "issue_number": None,
        }

    issue_number = relay_data["issue_number"]
    issue_url = relay_data["issue_url"]

    # Save to DB
    async with async_session() as db:
        report = BugReport(
            description=description,
            reporter_email=reporter_email,
            github_issue_number=issue_number,
            github_issue_url=issue_url,
            status="submitted",
            email_sent=True,
        )
        db.add(report)
        await db.commit()

    return {
        "success": True,
        "message": "Bug report submitted successfully!",
        "issue_url": issue_url,
        "issue_number": issue_number,
    }
