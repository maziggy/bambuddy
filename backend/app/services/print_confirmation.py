"""Post-print outcome confirmation helpers (#1898)."""

import logging
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry

logger = logging.getLogger(__name__)

# How a verdict reached the archive. 'reaction' is written by the Telegram
# reaction poller (#3046, services/telegram_reactions.py) through
# apply_outcome_verdict below.
VERDICT_SOURCES = ("dialog", "link", "plate_clear", "printer_card", "api", "reaction")

# Link-preview unfurlers and mail-security scanners fetch every URL they find in
# a message, unattended, within seconds of it being sent. Nothing they can do
# with a GET records a verdict any more -- that is the POST route's job -- so
# this list is the second layer: it decides whether the confirmation page
# submits its own form, which is what keeps a human at one tap. A scanner that
# runs JavaScript would otherwise press the button on the operator's behalf.
# Matched as case-insensitive substrings of the User-Agent; the generic "bot"
# token covers TelegramBot, Discordbot, Slackbot-LinkExpanding, Twitterbot and
# LinkedInBot in one go.
UNATTENDED_FETCH_AGENTS = (
    "bot",
    "crawler",
    "spider",
    "facebookexternalhit",
    "whatsapp",
    "skypeuripreview",
    "bingpreview",
    "safelinks",
    "urldefense",
    "proofpoint",
    "mimecast",
    "barracuda",
    "forcepoint",
)

# Prefetch / preload hints. A finger on a notification button is never one.
UNATTENDED_FETCH_HEADERS = {
    "purpose": ("prefetch", "preview"),
    "x-purpose": ("prefetch", "preview"),
    "x-moz": ("prefetch",),
    "sec-purpose": ("prefetch",),
}


def is_unattended_fetch(method: str, headers: Mapping[str, str]) -> bool:
    """Whether a request for a one-tap verdict link came from a machine.

    Decides whether the confirmation page submits itself. False positives are
    deliberately cheap -- a browser mistaken for a bot gets the same page with
    a button to press -- so the lists above err towards catching more.
    """
    if method.upper() != "GET":
        # Anything that is not the page load is not a page load: only the GET
        # route renders, and only a GET can be widened to HEAD by a future
        # router change.
        return True
    for header, markers in UNATTENDED_FETCH_HEADERS.items():
        value = (headers.get(header) or "").lower()
        if value and any(marker in value for marker in markers):
            return True
    agent = (headers.get("user-agent") or "").lower()
    return any(marker in agent for marker in UNATTENDED_FETCH_AGENTS)


# The one-tap marker. The confirmation page submits its own form only when the
# URL it was opened from carries this, and the marker is put on exactly one
# thing: the Telegram inline keyboard's buttons -- an affordance no unfurler,
# gateway or proxy reads, for the same reason the capability URLs themselves no
# longer travel in message text.
#
# It is what closes the gap the User-Agent list above cannot: a mail-security
# sandbox that renders HTML and runs JavaScript sends an ordinary Chrome string
# (so does literal HeadlessChrome), and a verdict URL that reached it did so out
# of the message BODY -- where the marker never appears. That fetch now gets the
# page with a button on it and records nothing. The operator's tap on the
# notification button still costs exactly one tap.
ONE_TAP_PARAM = "tap"


def one_tap_url(url: str) -> str:
    """Mark a verdict URL as one a human is about to press.

    Only for the affordances a person taps directly. A URL that goes into text
    anybody's machine might follow is left unmarked on purpose.
    """
    return f"{url}{'&' if '?' in url else '?'}{ONE_TAP_PARAM}=1"


def is_one_tap_request(query_params: Mapping[str, str]) -> bool:
    """Whether this page load came from a button rather than from message text."""
    return (query_params.get(ONE_TAP_PARAM) or "") == "1"


def stamp_verdict(archive: PrintArchive | PrintLogEntry, source: str) -> None:
    """Record a verdict's provenance and the moment it landed (#1898).

    Both fields move together on every verdict write, which is what keeps the
    "already answered" page from pairing a new source with the timestamp of an
    older decision. `retire_confirm_token` is separate on purpose: spending the
    one-tap capability happens once, recording a verdict can happen again.
    """
    archive.user_verdict_source = source
    archive.user_verdict_at = datetime.now(timezone.utc)


def retire_confirm_token(archive: PrintArchive | PrintLogEntry) -> None:
    """Spend the one-tap capability token without destroying it.

    The token is still single-use: once ``confirm_token_used_at`` is stamped,
    no verdict path accepts it again. Keeping the VALUE is what lets the
    one-tap route recognise a link belonging to an already-answered print and
    say so, instead of 404ing as if the link had never been real (the live-farm
    case: the plate-clear default answered the prompt, then the user tapped the
    Telegram button and got "invalid or already used").
    """
    if archive.confirm_token and archive.confirm_token_used_at is None:
        archive.confirm_token_used_at = datetime.now(timezone.utc)


VERDICTS = ("good", "reject")


async def latest_run(db: AsyncSession, archive_id: int) -> PrintLogEntry | None:
    """The archive's latest finished run by its start: the one its card shows."""
    return await db.scalar(
        select(PrintLogEntry)
        .where(PrintLogEntry.archive_id == archive_id)
        .order_by(PrintLogEntry.started_at.desc().nulls_last(), PrintLogEntry.id.desc())
        .limit(1)
    )


async def run_of_archive(db: AsyncSession, archive_id: int, run_id: int) -> PrintLogEntry | None:
    """The archive's finished run ``run_id``, or None when it is not one."""
    return await db.scalar(
        select(PrintLogEntry).where(PrintLogEntry.id == run_id, PrintLogEntry.archive_id == archive_id)
    )


async def record_verdict(
    db: AsyncSession,
    archive: PrintArchive,
    verdict: str | None,
    *,
    source: str | None,
    run: PrintLogEntry | None,
) -> None:
    """Write a verdict, or clear it (``verdict`` None), on the run it answers.

    Plates of one archive are runs of their own, each with its own question,
    so the verdict is the run's: its value, provenance and its one-tap token
    spent. The archive shows its latest run's, so it takes the verdict when
    ``run`` is that run, or when there is no run (a print logged before runs).
    Clearing drops the provenance but leaves the tokens spent: they were used.
    Deliberately does NOT commit.
    """
    holders: list[PrintArchive | PrintLogEntry] = []
    if run is not None:
        holders.append(run)
    if run is None or (latest := await latest_run(db, archive.id)) is None or latest.id == run.id:
        holders.append(archive)
    for holder in holders:
        holder.user_verdict = verdict
        if verdict is None:
            holder.user_verdict_source = None
            holder.user_verdict_at = None
        else:
            stamp_verdict(holder, source or "api")
            retire_confirm_token(holder)


async def apply_outcome_verdict(
    db: AsyncSession,
    archive: PrintArchive,
    verdict: str,
    *,
    source: str,
    run: PrintLogEntry | None = None,
) -> bool:
    """Record a verdict on a print that has not been answered yet.

    For verdict paths that arrive with nobody looking at the archive, like the
    Telegram reaction poller (#3046). ``run`` is the run the prompt asked
    about; without one, the archive's latest (a prompt sent before runs had
    their own). Written by :func:`record_verdict`.

    First verdict wins. A print that already carries one is left exactly as
    it is and False is returned, so a late reaction cannot flip a decision
    somebody made in the meantime. Deliberately does NOT commit.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"Verdict must be one of {VERDICTS}, got {verdict!r}")
    if source not in VERDICT_SOURCES:
        raise ValueError(f"Verdict source must be one of {VERDICT_SOURCES}, got {source!r}")
    if run is None:
        if archive.user_verdict is not None:
            return False
        run = await latest_run(db, archive.id)
    elif run.user_verdict is not None:
        return False

    await record_verdict(db, archive, verdict, source=source, run=run)
    return True


async def confirm_token_holder(db: AsyncSession, token: str) -> tuple[PrintArchive, PrintLogEntry | None] | None:
    """The print a one-tap capability token asks about: the run it was minted
    for, with its archive, or an archive's own token (minted before runs had
    theirs, answering its latest run). None for a token nobody holds."""
    if not token:
        return None
    run = await db.scalar(select(PrintLogEntry).where(PrintLogEntry.confirm_token == token))
    if run is not None:
        archive = await db.get(PrintArchive, run.archive_id) if run.archive_id is not None else None
        return (archive, run) if archive is not None else None
    archive = await db.scalar(select(PrintArchive).where(PrintArchive.confirm_token == token))
    return (archive, None) if archive is not None else None


async def resolve_pending_confirmation_as_good(db: AsyncSession, printer_id: int) -> int | None:
    """Mark the printer's latest print awaiting its verdict as good.

    Backs the opt-in ``confirm_default_good_on_plate_clear`` setting: releasing
    the build plate is the moment the operator moves on to the next job, so an
    unanswered outcome prompt can default to "good part" right there instead of
    lingering as unconfirmed. Only the LATEST pending one is resolved -- the
    plate release refers to the print that just came off the plate, not to
    older unanswered prompts. Found by the run's printer: a plate of the
    archive printed since on another printer moved the archive there. A run
    logged before runs carried the opt-in asks, and is answered, as its
    archive is.

    Retires the one-tap capability token (:func:`record_verdict`). Deliberately
    does NOT commit -- both callers (the clear-plate route and the queue
    dispatcher) manage their own transaction.

    Returns the resolved archive id, or None when nothing was pending.
    """
    run = await db.scalar(
        select(PrintLogEntry)
        .join(PrintArchive, PrintArchive.id == PrintLogEntry.archive_id)
        .where(
            PrintLogEntry.printer_id == printer_id,
            PrintLogEntry.status == "completed",
            PrintLogEntry.user_verdict.is_(None),
            or_(
                PrintLogEntry.confirm_requested.is_(True),
                and_(
                    PrintLogEntry.confirm_requested.is_(None),
                    PrintArchive.confirm_requested.is_(True),
                    PrintArchive.user_verdict.is_(None),
                ),
            ),
        )
        .order_by(PrintLogEntry.started_at.desc().nulls_last(), PrintLogEntry.id.desc())
        .limit(1)
    )
    archive = await db.get(PrintArchive, run.archive_id) if run is not None else None
    if archive is None:
        # A printer with no such run logged: its archive, as before runs.
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
        run = await latest_run(db, archive.id)
    await record_verdict(db, archive, "good", source="plate_clear", run=run)

    logger.info("[#1898] Plate clear defaulted archive %s to 'good' (printer %s)", archive.id, printer_id)
    return archive.id


async def confirm_outcome_for_new_queue_item(db: AsyncSession, *, started_outside_bambuddy: bool = False) -> bool:
    """The ask-for-outcome flag for a queue item created without the print dialog.

    The dialog seeds its own per-job toggle from ``default_confirm_outcome``.
    Every other queue-creation path -- the virtual printer, the library bulk
    add, the webhook, a pipeline run -- has no toggle to seed and used to leave
    the column at its ``False`` default, so "Ask for Outcome" only ever reached
    jobs queued by hand.

    ``started_outside_bambuddy`` additionally honours
    ``confirm_outcome_external_prints``: a plate sent from Bambu Studio to a
    virtual printer is one of the prints that setting's description names, but
    it arrives with a queue item, so ``on_print_start`` never sees it as
    external and the setting could not otherwise reach it.
    """
    from backend.app.api.routes.settings import get_setting, setting_is_true

    if setting_is_true(await get_setting(db, "default_confirm_outcome")):
        return True
    return started_outside_bambuddy and setting_is_true(await get_setting(db, "confirm_outcome_external_prints"))
