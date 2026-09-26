"""Post-print outcome confirmation helpers (#1898)."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry

logger = logging.getLogger(__name__)

VERDICTS = ("good", "reject")


async def apply_outcome_verdict(
    db: AsyncSession,
    archive: PrintArchive,
    verdict: str,
    *,
    reason: str | None = None,
) -> bool:
    """Record a verdict on an archive that has not been answered yet.

    The one place every unattended verdict path goes through — the one-tap
    capability link, the plate-clear default and the Telegram reaction
    poller (#3046) — so they agree on what a verdict entails: the archive's
    user_verdict, the same value mirrored onto the latest PrintLogEntry
    (verdict-aware statistics read the log, the #1444 mirror), and the
    capability token retired so the push-notification links stop working.

    First verdict wins. An archive that already carries one is left exactly
    as it is and False is returned, so a late reaction or a second tap on an
    old link cannot flip a decision somebody made in the meantime. The
    Edit Archive modal's PATCH route is deliberately not routed through
    here — it is the explicit way to change a verdict afterwards.

    ``reason`` is an optional failure_reason for a reject. Deliberately does
    NOT commit — callers manage their own transaction.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"Verdict must be one of {VERDICTS}, got {verdict!r}")
    if archive.user_verdict is not None:
        return False

    archive.user_verdict = verdict
    archive.confirm_token = None
    if reason is not None and verdict == "reject":
        archive.failure_reason = reason

    latest_entry = await db.scalar(
        select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc()).limit(1)
    )
    if latest_entry is not None:
        latest_entry.user_verdict = verdict
        if reason is not None and verdict == "reject":
            latest_entry.failure_reason = reason
    return True


async def resolve_pending_confirmation_as_good(db: AsyncSession, printer_id: int) -> int | None:
    """Mark the printer's latest pending-confirmation archive as good.

    Backs the opt-in ``confirm_default_good_on_plate_clear`` setting: releasing
    the build plate is the moment the operator moves on to the next job, so an
    unanswered outcome prompt can default to "good part" right there instead of
    lingering as unconfirmed. Only the LATEST pending archive is resolved — the
    plate release refers to the print that just came off the plate, not to
    older unanswered prompts.

    Deliberately does NOT commit — both callers (the clear-plate route and the
    queue dispatcher) manage their own transaction.

    Returns the resolved archive id, or None when nothing was pending.
    """
    archive = await db.scalar(
        select(PrintArchive)
        .where(
            PrintArchive.printer_id == printer_id,
            PrintArchive.status == "completed",
            PrintArchive.confirm_requested.is_(True),
            PrintArchive.user_verdict.is_(None),
        )
        .order_by(PrintArchive.id.desc())
        .limit(1)
    )
    if archive is None:
        return None

    await apply_outcome_verdict(db, archive, "good")

    logger.info("[#1898] Plate clear defaulted archive %s to 'good' (printer %s)", archive.id, printer_id)
    return archive.id
