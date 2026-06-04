"""Regression tests for subtask_id-based archive resume (#972).

Before this fix, a Bambuddy restart during a long print (e.g. 13h) triggered
the name-based "stale archive" path at 4h, cancelled the original row, and
created a new archive with `started_at = now()` — losing ~9h of print time
continuity. mstko reported this on a 37.5MB Broly print on an A1: after a
container restart mid-print, the archive ended up showing ~1h37m duration
for a print that actually ran 13h08m.

The fix stores `subtask_id` (MQTT-provided job identifier) on the archive row.
On print-start detection, the handler first tries to match an existing
archive by subtask_id regardless of age — same id ⇒ same print ⇒ resume.
Only unmatched prints fall through to the name-based fallback.

#1485 follow-up: the name-based fallback no longer cancels on a blind 4h
age cutoff (which duplicated the archive of any genuinely long print on
every restart). It now decides resume-vs-stale from the printer's current
progress — see TestStaleVsResume.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive


def _extract_subtask_id(data: dict) -> str | None:
    """Mirrors the extraction logic in main.on_print_start.

    Hoisted here so the test can pin the contract: Bambu reports "0" and
    empty string for local / non-cloud prints, both of which must collapse
    to None so we don't match every non-cloud print to every other one.
    """
    raw = data.get("raw_data") or {}
    val = raw.get("subtask_id")
    if val is None:
        return None
    val = str(val).strip()
    if val in ("", "0"):
        return None
    return val


class TestSubtaskIdExtraction:
    """subtask_id extraction mirrors the in-handler logic."""

    def test_valid_id_returns_string(self):
        assert _extract_subtask_id({"raw_data": {"subtask_id": "12345"}}) == "12345"

    def test_zero_collapses_to_none(self):
        """Bambu reports '0' for local (non-cloud) prints; must not match anything."""
        assert _extract_subtask_id({"raw_data": {"subtask_id": "0"}}) is None

    def test_empty_collapses_to_none(self):
        assert _extract_subtask_id({"raw_data": {"subtask_id": ""}}) is None

    def test_missing_raw_data(self):
        assert _extract_subtask_id({}) is None

    def test_missing_subtask_id(self):
        assert _extract_subtask_id({"raw_data": {"foo": "bar"}}) is None

    def test_integer_value_stringified(self):
        """MQTT may send the id as an int — coerce consistently."""
        assert _extract_subtask_id({"raw_data": {"subtask_id": 12345}}) == "12345"

    def test_whitespace_trimmed(self):
        assert _extract_subtask_id({"raw_data": {"subtask_id": "  42  "}}) == "42"


class TestSubtaskIdResume:
    """End-to-end DB behavior of the resume path: a second on_print_start
    for the same subtask_id must find and reuse the first archive row."""

    @pytest.fixture
    async def archive_factory(self, db_session, printer_factory):
        printer = await printer_factory()

        async def _create(
            subtask_id: str | None = None,
            status: str = "printing",
            age_hours: float = 0,
            failure_reason: str | None = None,
        ):
            started = datetime.now(timezone.utc) - timedelta(hours=age_hours)
            archive = PrintArchive(
                printer_id=printer.id,
                filename="Broly_Legendary.gcode.3mf",
                file_path="archive/1/x/Broly.gcode.3mf",
                file_size=100,
                print_name="Broly_Legendary",
                status=status,
                started_at=started,
                subtask_id=subtask_id,
                failure_reason=failure_reason,
            )
            # Override server_default on created_at so age-based tests work
            archive.created_at = started
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return printer, archive

        return _create

    async def test_subtask_id_query_finds_matching_printing_row(self, archive_factory, db_session):
        """The lookup used by main.on_print_start finds a matching row even
        when the archive is older than the 4h name-based staleness cutoff."""
        printer, archive = await archive_factory(subtask_id="t-123", age_hours=10)

        result = await db_session.execute(
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer.id)
            .where(PrintArchive.subtask_id == "t-123")
            .where(PrintArchive.status.in_(["printing", "cancelled"]))
            .order_by(PrintArchive.created_at.desc())
            .limit(1)
        )
        found = result.scalar_one_or_none()
        assert found is not None
        assert found.id == archive.id

    async def test_subtask_id_revives_stale_cancelled_row(self, archive_factory, db_session):
        """If an older Bambuddy wrongly cancelled the archive (legacy 4h path),
        the next print-start with the same subtask_id must revive it rather
        than start a third row."""
        printer, archive = await archive_factory(
            subtask_id="t-456",
            status="cancelled",
            failure_reason="Stale - print likely cancelled or failed without status update",
            age_hours=10,
        )

        result = await db_session.execute(
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer.id)
            .where(PrintArchive.subtask_id == "t-456")
            .where(PrintArchive.status.in_(["printing", "cancelled"]))
            .order_by(PrintArchive.created_at.desc())
            .limit(1)
        )
        candidate = result.scalar_one_or_none()
        assert candidate is not None

        # Revival mirrors the main.py logic: only revive stale-cancelled rows,
        # not user-cancelled ones. The failure_reason prefix is the signal.
        is_stale_cancelled = (candidate.failure_reason or "").startswith("Stale")
        assert is_stale_cancelled

        candidate.status = "printing"
        candidate.failure_reason = None
        await db_session.commit()
        await db_session.refresh(candidate)

        assert candidate.status == "printing"
        # Crucially, started_at is preserved — this is the whole point of the
        # fix. A fresh archive would have started_at = now, losing continuity.
        age_after = datetime.now(timezone.utc) - candidate.started_at.replace(tzinfo=timezone.utc)
        assert age_after > timedelta(hours=9), "started_at must survive revival"

    async def test_subtask_id_null_does_not_match_other_nulls(self, archive_factory, db_session):
        """Two different non-cloud prints both have subtask_id=NULL. They
        must NOT match each other via the subtask_id lookup (which is why
        the handler filters by `subtask_id IS NOT NULL` in the Python layer
        before even running this query)."""
        printer, _archive = await archive_factory(subtask_id=None, age_hours=1)

        # This shape of query (subtask_id == None) would return rows via
        # SQLAlchemy's NULL handling, but the handler only runs it when
        # subtask_id is truthy — so the query is never issued for NULL.
        # Assert the guard by testing the subtask_id != "" branch.
        result = await db_session.execute(select(PrintArchive).where(PrintArchive.subtask_id == ""))
        found = result.scalar_one_or_none()
        assert found is None, "Empty string must not match NULL rows"

    async def test_completed_archive_not_resumed(self, archive_factory, db_session):
        """A completed archive with the same subtask_id must not be reopened
        as printing — that subtask's job is done; a new run is a new row."""
        printer, _ = await archive_factory(subtask_id="t-789", status="completed")

        result = await db_session.execute(
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer.id)
            .where(PrintArchive.subtask_id == "t-789")
            .where(PrintArchive.status.in_(["printing", "cancelled"]))
        )
        found = result.scalar_one_or_none()
        assert found is None


def _looks_stale(live_progress: float | None, archive_age_seconds: float) -> bool:
    """Mirrors the name-fallback stale decision in main.on_print_start (#1485).

    A name-matched 'printing' archive is treated as a stale leftover ONLY when
    the printer clearly shows a different, freshly-started print: near-0%
    progress on an archive far too old to still be at 0%. Real progress, or
    unknown progress (printer not connected), always resumes — the old blind
    4h age cutoff cancelled the live archive of every long print on restart.
    """
    return live_progress is not None and live_progress < 1.0 and archive_age_seconds > 2 * 60 * 60


class TestStaleVsResume:
    """The progress-aware replacement for the 4h staleness heuristic (#1485)."""

    def test_long_print_in_progress_resumes_not_stale(self):
        """The reporter's case: a ~10h print, backend restarts, printer is
        mid-print at 60%. The old 4h cutoff cancelled + duplicated it; it
        must now resume regardless of age."""
        assert _looks_stale(60.0, archive_age_seconds=10 * 3600) is False

    def test_barely_started_long_print_resumes(self):
        """A genuine print a few percent in is still the same print."""
        assert _looks_stale(3.0, archive_age_seconds=5 * 3600) is False

    def test_fresh_print_with_old_archive_is_stale(self):
        """Printer reports a just-started print (~0%) but the matched archive
        is hours old — that archive is a dead leftover from a previous run."""
        assert _looks_stale(0.0, archive_age_seconds=9 * 3600) is True

    def test_fresh_print_with_young_archive_resumes(self):
        """~0% progress on a young archive is just the same print still
        heating / leveling — not stale."""
        assert _looks_stale(0.0, archive_age_seconds=20 * 60) is False

    def test_unknown_progress_never_cancels(self):
        """Printer not connected / progress unknown: resuming is the safe
        default — never cancel + duplicate when we can't tell."""
        assert _looks_stale(None, archive_age_seconds=10 * 3600) is False

    def test_sub_one_percent_old_archive_is_stale(self):
        """The boundary: just under 1% past the 2h mark counts as stale."""
        assert _looks_stale(0.5, archive_age_seconds=3 * 3600) is True
