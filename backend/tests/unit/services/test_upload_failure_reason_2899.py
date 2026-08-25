"""Say what actually went wrong, not what usually does (#2899).

Every failed dispatch upload used to carry the same sentence: "Failed to upload
file to printer. Check if SD card is inserted and properly formatted
(FAT32/exFAT)." The reporter got it after a TLS handshake failure and restarted
the printer on the strength of it. That could not have helped -- the handshake
never reached the printer's filesystem, and the cool-off that produced the
repeat failure lives in Bambuddy's own memory, where power-cycling a printer
does not reach.

#2780 had already removed operator advice from this failure's *log* line, for
exactly this reason. The advice survived in the string people actually read.

The information was never missing. ``connect`` separates five failure classes
and ``upload_file`` separates 553/552/550, each with its own log line -- and
both then returned a bare ``False``. These tests pin the reason travelling out
to the caller, and the card being named only where the printer itself raised
storage.
"""

import ftplib  # nosec B402 -- tests construct real ftplib error types
import ssl
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.bambu_ftp import (
    BambuFTPClient,
    FtpFailure,
    FtpFailureKind,
    FtpFailureReport,
    describe_upload_failure,
    upload_file_async,
    with_ftp_retry,
)

pytestmark = pytest.mark.unit

IP = "192.168.50.142"


@pytest.fixture(autouse=True)
def _clean_state():
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()
    yield
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()


# ---------------------------------------------------------------------------
# The client records which of its own branches it took
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), FtpFailureKind.HANDSHAKE, None),
        (TimeoutError("handshake operation timed out"), FtpFailureKind.TIMEOUT, None),
        (ftplib.error_perm("530 Login incorrect."), FtpFailureKind.AUTH, "530"),
        (OSError("Connection reset by peer"), FtpFailureKind.NETWORK, None),
    ],
    ids=["handshake", "timeout", "auth", "network"],
)
def test_connect_records_which_failure_it_hit(error, kind, code):
    transport = MagicMock()
    transport.connect.side_effect = error
    with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport):
        client = BambuFTPClient(IP, "12345678", printer_model="P2S")
        assert client.connect() is False

    assert client.last_failure is not None
    assert client.last_failure.kind is kind
    assert client.last_failure.code == code
    # The underlying text is kept too -- the sentence is for the operator, the
    # detail is for whoever reads the log next to it.
    assert str(error)[:20] in client.last_failure.detail


def test_the_cooloff_skip_is_its_own_kind():
    """ "We did not try" is not the same failure as "we tried and it broke"."""
    BambuFTPClient._handshake_blocked_until[IP] = time.monotonic() + 300
    client = BambuFTPClient(IP, "12345678")

    assert client.connect() is False
    assert client.last_failure is not None
    assert client.last_failure.kind is FtpFailureKind.COOLOFF


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        ("553 Could not create file.", FtpFailureKind.STORAGE),
        ("552 Storage quota exceeded.", FtpFailureKind.STORAGE),
        ("550 Permission denied.", FtpFailureKind.NOT_FOUND),
        ("500 Unknown command.", FtpFailureKind.UNKNOWN),
    ],
    ids=["553", "552", "550", "500"],
)
def test_upload_classifies_the_printers_reply_code(reply, kind, tmp_path):
    """553 and 552 are the printer talking about its own storage.

    That is the one case where naming the SD card is worth anything, and it is
    the case the blanket message was written for before it was applied to
    every failure alike.
    """
    local = tmp_path / "job.3mf"
    local.write_bytes(b"x" * 16)

    client = BambuFTPClient(IP, "12345678")
    client._ftp = MagicMock()
    client._ftp.transfercmd.side_effect = ftplib.error_perm(reply)

    assert client.upload_file(local, "/job.3mf") is False
    assert client.last_failure is not None
    assert client.last_failure.kind is kind
    assert client.last_failure.code == reply[:3]


def test_a_successful_upload_leaves_no_failure_behind(tmp_path):
    """Otherwise a later failure inherits an earlier one's reason."""
    local = tmp_path / "job.3mf"
    local.write_bytes(b"x" * 16)

    client = BambuFTPClient(IP, "12345678")
    client._ftp = MagicMock()
    client.last_failure = FtpFailure(FtpFailureKind.STORAGE, "553 stale", "553")

    assert client.upload_file(local, "/job.3mf") is True
    assert client.last_failure is None


# ---------------------------------------------------------------------------
# The reason reaches the caller
# ---------------------------------------------------------------------------
class TestTheReportReachesTheCaller:
    @pytest.fixture()
    def refusing_printer(self):
        transport = MagicMock()
        transport.connect.side_effect = ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
        with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport):
            yield transport

    async def test_upload_file_async_fills_the_slot(self, refusing_printer, tmp_path):
        local = tmp_path / "job.3mf"
        local.write_bytes(b"x" * 16)
        report = FtpFailureReport()

        assert await upload_file_async(IP, "12345678", local, "/job.3mf", timeout=5.0, failure=report) is False

        assert report.failure is not None
        assert report.failure.kind is FtpFailureKind.HANDSHAKE

    async def test_it_survives_the_retry_loop(self, refusing_printer, tmp_path):
        """with_ftp_retry forwards the slot untouched, so the last try wins.

        The last attempt is the one that decided the outcome, so its reason is
        the one the operator should be given.
        """
        local = tmp_path / "job.3mf"
        local.write_bytes(b"x" * 16)
        report = FtpFailureReport()

        result = await with_ftp_retry(
            upload_file_async,
            IP,
            "12345678",
            local,
            "/job.3mf",
            timeout=5.0,
            respect_handshake_cooloff=False,
            failure=report,
            max_retries=2,
            retry_delay=0.01,
        )

        assert result is None
        assert report.failure is not None
        assert report.failure.kind is FtpFailureKind.HANDSHAKE

    async def test_two_callers_do_not_cross(self, refusing_printer, tmp_path):
        """The slot belongs to the caller, not to the printer.

        A per-IP dict on the client would be the obvious way to do this, and
        it is the way that breaks: a background timelapse fetch running beside
        a dispatch would overwrite the dispatch's reason with its own, and
        report the wrong cause with total confidence.
        """
        local = tmp_path / "job.3mf"
        local.write_bytes(b"x" * 16)
        mine, theirs = FtpFailureReport(), FtpFailureReport()

        await upload_file_async(IP, "12345678", local, "/a.3mf", timeout=5.0, failure=mine)
        assert theirs.failure is None
        assert mine.failure is not None

    async def test_a_caller_that_does_not_ask_is_unaffected(self, refusing_printer, tmp_path):
        """Every other caller passes nothing and must keep working."""
        local = tmp_path / "job.3mf"
        local.write_bytes(b"x" * 16)
        assert await upload_file_async(IP, "12345678", local, "/job.3mf", timeout=5.0) is False


# ---------------------------------------------------------------------------
# The wording
# ---------------------------------------------------------------------------
class TestTheWording:
    def test_only_a_storage_reply_sends_anyone_to_the_card(self):
        """Advice about the card, not mention of it.

        The handshake message names the card too, to rule it out -- that is
        the opposite of what this is guarding against, so the marker is the
        instruction ("formatted FAT32 or exFAT"), not the noun.
        """
        advising = [k for k in FtpFailureKind if "FAT32" in describe_upload_failure(FtpFailure(k, "detail"))]
        assert advising == [FtpFailureKind.STORAGE]

    def test_no_other_failure_asks_anyone_to_touch_the_card(self):
        """Anything that is not a storage reply must not send them there.

        Checked as "do something to the card" rather than "say the words",
        since ruling the card out is exactly what the handshake message does.
        """
        for kind in FtpFailureKind:
            if kind is FtpFailureKind.STORAGE:
                continue
            message = describe_upload_failure(FtpFailure(kind, "detail"))
            assert "Check that its SD card" not in message, kind
            assert "inserted" not in message, kind

    @pytest.mark.parametrize(
        ("kind", "must_say"),
        [
            (FtpFailureKind.COOLOFF, "clears on its own"),
            (FtpFailureKind.HANDSHAKE, "not with TLS"),
            (FtpFailureKind.AUTH, "access code"),
            (FtpFailureKind.TIMEOUT, "did not respond in time"),
            (FtpFailureKind.STORAGE, "FAT32"),
            (FtpFailureKind.NOT_FOUND, "Bambuddy-side"),
            (FtpFailureKind.NETWORK, "server log"),
            (FtpFailureKind.UNKNOWN, "server log"),
        ],
    )
    def test_every_kind_says_something_of_its_own(self, kind, must_say):
        """One line per branch, so none can quietly collapse into the generic.

        Without this, deleting the access-code branch or the timeout branch
        leaves every other assertion here passing -- they only check that the
        card is not named, which the generic message also satisfies.
        """
        assert must_say in describe_upload_failure(FtpFailure(kind, "detail", "553"))

    def test_the_access_code_hint_names_a_screen_that_exists(self):
        """The Access Code field is on the printer form on the Printers page.

        Naming a screen that is not there would be its own version of this
        bug: confident, specific, and a waste of the reader's time.
        """
        message = describe_upload_failure(FtpFailure(FtpFailureKind.AUTH, "530 Login incorrect.", "530"))
        assert "Printers page" in message

    def test_a_handshake_failure_says_the_card_is_not_involved(self):
        message = describe_upload_failure(FtpFailure(FtpFailureKind.HANDSHAKE, "WRONG_VERSION_NUMBER"))
        assert "not with TLS" in message
        assert "SD card is not involved" in message

    def test_it_does_not_prescribe_a_power_cycle(self):
        """#2780 removed that advice from the log because it does not work.

        The reporter of this issue restarted a printer on the strength of the
        user-facing string, so the string has to carry the same restraint.
        """
        for kind in FtpFailureKind:
            message = describe_upload_failure(FtpFailure(kind, "detail"))
            assert "restart the printer" not in message.lower(), kind
            assert "reboot" not in message.lower(), kind

    def test_an_unclassified_failure_points_at_the_log_rather_than_guessing(self):
        for failure in (None, FtpFailure(FtpFailureKind.UNKNOWN, "500 what")):
            message = describe_upload_failure(failure)
            assert "server log" in message
            assert "SD card" not in message

    def test_the_storage_message_carries_the_reply_code(self):
        """So a support bundle and the queue entry can be lined up."""
        message = describe_upload_failure(FtpFailure(FtpFailureKind.STORAGE, "553 Could not create file.", "553"))
        assert "553" in message
        assert "FAT32" in message


# ---------------------------------------------------------------------------
# End to end: what the queue entry says
# ---------------------------------------------------------------------------
@pytest.fixture
async def dispatch_case(tmp_path):
    """Minimal one-printer, one-queued-job database for ``_start_print``."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import backend.app.models  # noqa: F401 - populate Base.metadata
    from backend.app.core.database import Base
    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.models.printer import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    base_dir = tmp_path / "case"
    archive_rel = Path("archives") / "job.3mf"
    archive_abs = base_dir / archive_rel
    archive_abs.parent.mkdir(parents=True, exist_ok=True)
    archive_abs.write_bytes(b"archive payload")

    async with session_maker() as db:
        printer = Printer(
            name="Bambulab P2S-4",
            serial_number="SERIAL",
            ip_address=IP,
            access_code="12345678",
            model="P2S",
        )
        db.add(printer)
        await db.flush()
        archive = PrintArchive(
            printer_id=printer.id,
            filename="job.3mf",
            file_path=str(archive_rel),
            file_size=archive_abs.stat().st_size,
            status="completed",
        )
        db.add(archive)
        await db.flush()
        item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="pending")
        db.add(item)
        await db.commit()
        item_id = item.id

    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, item_id=item_id)
    finally:
        await engine.dispose()


async def _dispatch_failing_with(dispatch_case, failure: FtpFailure | None):
    """Run one dispatch whose upload fails with *failure*.

    Returns the queue item's message and the reason the notification carried,
    which have to agree -- a push saying something different from the screen is
    its own small bug.
    """
    import backend.app.services.print_scheduler as scheduler_module
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.services.print_scheduler import PrintScheduler
    from backend.tests._fixtures.background_tasks import discarding_spawn_patch

    async def _upload(*_args, **kwargs):
        # Stands in for the real wrapper: fills the caller's slot, then fails.
        # Reading kwargs["failure"] rather than accepting it as a parameter is
        # deliberate -- if the dispatch ever stops passing the slot, every
        # message below falls back to the generic one and these tests fail.
        if failure is not None and kwargs.get("failure") is not None:
            kwargs["failure"].failure = failure
        return False

    notify = AsyncMock()
    scheduler = PrintScheduler()
    async with dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, dispatch_case.item_id)
        patches = [
            patch.object(scheduler_module.settings, "base_dir", dispatch_case.base_dir),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
            patch(
                "backend.app.services.print_scheduler.get_ftp_retry_settings",
                AsyncMock(return_value=(False, 0, 0, 1.0)),
            ),
            patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
            patch("backend.app.services.print_scheduler.upload_file_async", _upload),
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_failed", notify),
            discarding_spawn_patch(),
            patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
            patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
            patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await scheduler._start_print(db, item)

        refreshed = await db.get(PrintQueueItem, dispatch_case.item_id)
        assert refreshed.status == "failed"
        return refreshed.error_message or "", notify.await_args.kwargs["reason"]


class TestWhatTheQueueEntrySays:
    async def test_a_handshake_failure_does_not_send_anyone_to_the_sd_card(self, dispatch_case):
        """The report's own case: a TLS failure, answered with card advice.

        The reporter acted on it and restarted the printer. Nothing in that
        path reaches the printer's filesystem, and the cool-off that made the
        next dispatch fail identically lives in Bambuddy's memory, where
        power-cycling a printer does not reach.
        """
        message, reason = await _dispatch_failing_with(
            dispatch_case, FtpFailure(FtpFailureKind.HANDSHAKE, "WRONG_VERSION_NUMBER")
        )

        assert "inserted" not in message, message
        assert "FAT32" not in message, message
        assert "not with TLS" in message, message
        assert reason == message

    async def test_a_553_still_gets_the_card_advice(self, dispatch_case):
        """The advice was written for this case and belongs to it.

        Removing it everywhere would trade one wrong message for a vaguer one;
        the point is to attach it where the printer actually said storage.
        """
        message, reason = await _dispatch_failing_with(
            dispatch_case, FtpFailure(FtpFailureKind.STORAGE, "553 Could not create file.", "553")
        )

        assert "FAT32" in message, message
        assert "553" in message, message
        assert reason == message

    async def test_an_unclassified_failure_points_at_the_log(self, dispatch_case):
        """No reason recorded means no reason invented."""
        message, reason = await _dispatch_failing_with(dispatch_case, None)

        assert "server log" in message, message
        assert "SD card" not in message, message
        assert reason == message
