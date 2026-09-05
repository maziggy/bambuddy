"""Integration tests for the post-print outcome confirmation (#1898).

Covers the verdict PATCH (incl. the #1444-style mirror to the latest
PrintLogEntry and token retirement), the unauthenticated capability-token
endpoint, response defaults, and the verdict-aware statistics.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.print_log import PrintLogEntry


class TestOutcomeVerdictPatch:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_defaults_present_on_response(self, async_client: AsyncClient, archive_factory, printer_factory):
        """Archives created without any verdict expose the new fields with
        their defaults — no verdict, no pending confirmation."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["user_verdict"] is None
        assert body["confirm_requested"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_verdict_mirrors_to_latest_log_entry(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Setting the verdict via PATCH lands on the archive AND the latest
        PrintLogEntry (verdict-aware statistics read the log), and retires a
        pending confirmation token."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="test-token-mirror")

        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"user_verdict": "reject"})
        assert response.status_code == 200
        assert response.json()["user_verdict"] == "reject"

        entry = await db_session.scalar(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc())
        )
        assert entry is not None
        assert entry.user_verdict == "reject"

        await db_session.refresh(archive)
        assert archive.user_verdict == "reject"
        assert archive.confirm_token is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_rejects_unknown_verdict(self, async_client: AsyncClient, archive_factory, printer_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"user_verdict": "meh"})
        assert response.status_code == 422


class TestConfirmTokenEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_token_records_verdict_and_retires_token(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """The one-tap link from a push notification records the verdict
        without auth, mirrors it to the log entry, and single-uses the token."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="test-token-good")

        response = await async_client.get("/api/v1/archives/confirm/test-token-good/good")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

        await db_session.refresh(archive)
        assert archive.user_verdict == "good"
        assert archive.confirm_token is None

        entry = await db_session.scalar(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc())
        )
        assert entry is not None
        assert entry.user_verdict == "good"

        # Second use of the same token: gone.
        response = await async_client.get("/api/v1/archives/confirm/test-token-good/reject")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_token_and_garbage_verdict(self, async_client: AsyncClient):
        assert (await async_client.get("/api/v1/archives/confirm/no-such-token/good")).status_code == 404
        assert (await async_client.get("/api/v1/archives/confirm/whatever/maybe")).status_code == 400


class TestDefaultGoodOnPlateClear:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_helper_resolves_only_the_latest_pending(self, archive_factory, printer_factory, db_session):
        """Releasing the plate refers to the print that just came off it: only
        the LATEST pending archive flips to good; older unanswered prompts and
        already-answered ones stay untouched. The verdict mirrors to the run."""
        from backend.app.services.print_confirmation import resolve_pending_confirmation_as_good

        printer = await printer_factory()
        older = await archive_factory(printer.id, confirm_requested=True)
        newest_pending = await archive_factory(printer.id, confirm_requested=True, confirm_token="pending-token")
        answered = await archive_factory(printer.id, confirm_requested=True, user_verdict="reject")

        resolved = await resolve_pending_confirmation_as_good(db_session, printer.id)
        await db_session.commit()

        assert resolved == newest_pending.id
        await db_session.refresh(newest_pending)
        assert newest_pending.user_verdict == "good"
        assert newest_pending.confirm_token is None
        await db_session.refresh(older)
        assert older.user_verdict is None
        await db_session.refresh(answered)
        assert answered.user_verdict == "reject"

        entry = await db_session.scalar(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == newest_pending.id).order_by(PrintLogEntry.id.desc())
        )
        assert entry is not None and entry.user_verdict == "good"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_helper_noop_without_pending(self, archive_factory, printer_factory, db_session):
        from backend.app.services.print_confirmation import resolve_pending_confirmation_as_good

        printer = await printer_factory()
        await archive_factory(printer.id)  # completed, never asked

        assert await resolve_pending_confirmation_as_good(db_session, printer.id) is None


class TestVerdictStatistics:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_failure_analysis_reports_rejects_separately(
        self, async_client: AsyncClient, archive_factory, printer_factory
    ):
        """A completed-but-rejected print stays OUT of the machine failure
        rate but shows up as rejected_prints and lowers the yield rate."""
        printer = await printer_factory()
        good = await archive_factory(printer.id, print_name="Good Part")
        rejected = await archive_factory(printer.id, print_name="Scrap Part")
        await archive_factory(printer.id, print_name="Machine Failure", status="failed", run_status="failed")

        # Confirm one good, reject one — through the API so the mirror runs.
        assert (
            await async_client.patch(f"/api/v1/archives/{good.id}", json={"user_verdict": "good"})
        ).status_code == 200
        assert (
            await async_client.patch(
                f"/api/v1/archives/{rejected.id}", json={"user_verdict": "reject", "failure_reason": "other"}
            )
        ).status_code == 200

        response = await async_client.get("/api/v1/archives/analysis/failures?days=30")
        assert response.status_code == 200
        body = response.json()

        # Machine numbers unchanged by the quality dimension:
        assert body["failed_prints"] == 1
        assert body["failure_rate"] == pytest.approx(33.3, abs=0.1)
        # Quality dimension:
        assert body["rejected_prints"] == 1
        assert body["yield_rate"] == pytest.approx(33.3, abs=0.1)
        assert body["rejects_by_reason"] == {"other": 1}
