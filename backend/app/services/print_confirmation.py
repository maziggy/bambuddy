"""Post-print outcome confirmation helpers (#1898)."""

import logging
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry

logger = logging.getLogger(__name__)

# How a verdict reached the archive. 'reaction' is written by the Telegram
# reaction handler (#3046), which lives on its own branch — listed here so the
# vocabulary is complete and the UI can label it the day that lands.
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


def stamp_verdict(archive: PrintArchive, source: str) -> None:
    """Record a verdict's provenance and the moment it landed (#1898).

    Both fields move together on every verdict write, which is what keeps the
    "already answered" page from pairing a new source with the timestamp of an
    older decision. `retire_confirm_token` is separate on purpose: spending the
    one-tap capability happens once, recording a verdict can happen again.
    """
    archive.user_verdict_source = source
    archive.user_verdict_at = datetime.now(timezone.utc)


def retire_confirm_token(archive: PrintArchive) -> None:
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


async def resolve_pending_confirmation_as_good(db: AsyncSession, printer_id: int) -> int | None:
    """Mark the printer's latest pending-confirmation archive as good.

    Backs the opt-in ``confirm_default_good_on_plate_clear`` setting: releasing
    the build plate is the moment the operator moves on to the next job, so an
    unanswered outcome prompt can default to "good part" right there instead of
    lingering as unconfirmed. Only the LATEST pending archive is resolved — the
    plate release refers to the print that just came off the plate, not to
    older unanswered prompts.

    Mirrors the verdict onto the latest PrintLogEntry (the #1444 mirror) and
    retires the one-tap capability token. Deliberately does NOT commit — both
    callers (the clear-plate route and the queue dispatcher) manage their own
    transaction.

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

    archive.user_verdict = "good"
    stamp_verdict(archive, "plate_clear")
    retire_confirm_token(archive)

    latest_entry = await db.scalar(
        select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc()).limit(1)
    )
    if latest_entry is not None:
        latest_entry.user_verdict = "good"

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
