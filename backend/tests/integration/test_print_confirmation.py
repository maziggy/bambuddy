"""Integration tests for the post-print outcome confirmation (#1898).

Covers the verdict PATCH (incl. the #1444-style mirror to the latest
PrintLogEntry and token retirement), the unauthenticated capability-token
endpoint, response defaults, and the verdict-aware statistics.
"""

import re

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
        # Retired by stamping, not by dropping the value: the link stays
        # resolvable so a later tap can be told it is already answered.
        assert archive.confirm_token == "test-token-mirror"
        assert archive.confirm_token_used_at is not None
        assert archive.user_verdict_source == "api"

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

        response = await async_client.post("/api/v1/archives/confirm/test-token-good/good")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

        await db_session.refresh(archive)
        assert archive.user_verdict == "good"
        assert archive.user_verdict_source == "link"
        assert archive.confirm_token == "test-token-good"
        assert archive.confirm_token_used_at is not None

        entry = await db_session.scalar(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc())
        )
        assert entry is not None
        assert entry.user_verdict == "good"

        # Second use of the same token: spent, and said so rather than 404.
        response = await async_client.get("/api/v1/archives/confirm/test-token-good/reject")
        assert response.status_code == 200
        assert "Already answered" in response.text
        await db_session.refresh(archive)
        assert archive.user_verdict == "good"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spent_link_reports_the_recorded_verdict_without_changing_it(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """The live-farm case (#1898): the plate-clear default answered the
        prompt, then the Telegram button was tapped. The link must name the
        verdict on file, say how it got there, and leave it alone."""
        from backend.app.services.print_confirmation import resolve_pending_confirmation_as_good

        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="plate-cleared-token")

        assert await resolve_pending_confirmation_as_good(db_session, printer.id) == archive.id
        await db_session.commit()

        response = await async_client.get("/api/v1/archives/confirm/plate-cleared-token/reject")
        assert response.status_code == 200
        body = response.text
        assert "Already answered" in body
        assert "Good part" in body
        assert "plate was cleared" in body
        assert f"/archives?confirm={archive.id}" in body

        await db_session.refresh(archive)
        assert archive.user_verdict == "good"
        assert archive.user_verdict_source == "plate_clear"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spent_link_after_the_verdict_was_cleared_again(
        self, async_client: AsyncClient, archive_factory, printer_factory
    ):
        """Clearing the verdict in the app does not un-spend the link: the
        capability was used, so the page explains rather than re-opening it."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="cleared-again-token")

        assert (await async_client.post("/api/v1/archives/confirm/cleared-again-token/good")).status_code == 200
        assert (
            await async_client.patch(f"/api/v1/archives/{archive.id}", json={"user_verdict": None})
        ).status_code == 200

        response = await async_client.get("/api/v1/archives/confirm/cleared-again-token/good")
        assert response.status_code == 200
        assert "Already answered" in response.text
        assert (await async_client.get(f"/api/v1/archives/{archive.id}")).json()["user_verdict"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_token_and_garbage_verdict(self, async_client: AsyncClient):
        assert (await async_client.get("/api/v1/archives/confirm/no-such-token/good")).status_code == 404
        assert (await async_client.get("/api/v1/archives/confirm/whatever/maybe")).status_code == 400
        assert (await async_client.post("/api/v1/archives/confirm/no-such-token/good")).status_code == 404
        assert (await async_client.post("/api/v1/archives/confirm/whatever/maybe")).status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_get_records_nothing_whoever_sends_it(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """The blocker. Telegram and Slack GET the URLs in a message to build a
        preview card, mail gateways detonate them before delivery, proxies and
        browsers prefetch. While GET was the route that recorded, any of those
        settled the outcome before the operator read the question — always
        towards 'good', because good_url came first — and spent the token, so
        the real tap landed on "already answered". A scrap part counted as a
        success for good, in the statistics this branch adds.

        The heuristic below decides how the page behaves, not whether the
        verdict is written: nothing a GET can say records anything.
        """
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="unfurler-token")

        for agent in (
            "TelegramBot (like TwitterBot)",
            "Slackbot-LinkExpanding 1.0",
            "Mimecast-Link-Protect",
            # The one that used to be allowed straight through to the write.
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        ):
            response = await async_client.get(
                "/api/v1/archives/confirm/unfurler-token/good", headers={"user-agent": agent}
            )
            assert response.status_code == 200, agent
            assert "Confirm this outcome" in response.text, agent
            assert "<form method='post'" in response.text, agent

            await db_session.refresh(archive)
            assert archive.user_verdict is None, agent
            assert archive.confirm_token_used_at is None, f"{agent} must leave the capability spendable"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_forms_post_records_the_verdict(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """What the page's form does when it is submitted. Unfurlers, scanners
        and prefetchers issue GET; none of them POSTs."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="unfurler-then-tap")

        response = await async_client.post("/api/v1/archives/confirm/unfurler-then-tap/reject")
        assert response.status_code == 200
        assert "Rejected" in response.text

        await db_session.refresh(archive)
        assert archive.user_verdict == "reject"
        assert archive.user_verdict_source == "link"
        assert archive.confirm_token_used_at is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_phone_browser_still_records_in_one_tap(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """The split must not cost the feature its point. The page opened from
        a notification button submits itself, so the tap on the notification is
        still the only tap — and the script carries the CSP nonce, without
        which the policy in main.py would block it and cost the operator a
        second tap.

        The ``?tap=1`` is what the Telegram inline keyboard carries. This test
        used to load the URL without it and still expect the script, which was
        the remaining hole rather than the feature: that unmarked URL is the
        one a scanner gets out of a message body.
        """
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="phone-token")

        response = await async_client.get(
            "/api/v1/archives/confirm/phone-token/good?tap=1",
            headers={
                "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
            },
        )
        assert response.status_code == 200
        assert "getElementById('confirm-form').submit()" in response.text

        nonce = re.search(r"<script nonce='([^']+)'", response.text)
        assert nonce, "the inline script needs the per-request nonce or the CSP blocks it"
        assert f"'nonce-{nonce.group(1)}'" in response.headers["content-security-policy"]

        # And the submit that script performs is the thing that records. The
        # form has no action attribute, so a browser POSTs back to the URL it
        # loaded — query string and all, which the route must not mind.
        await db_session.refresh(archive)
        assert archive.user_verdict is None
        assert (await async_client.post("/api/v1/archives/confirm/phone-token/good?tap=1")).status_code == 200
        await db_session.refresh(archive)
        assert archive.user_verdict == "good"
        assert archive.confirm_token_used_at is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "headers",
        [
            {"user-agent": "TelegramBot (like TwitterBot)"},
            {
                "user-agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
                "purpose": "prefetch",
            },
        ],
    )
    async def test_an_unattended_fetch_gets_no_self_submitting_script(
        self, async_client: AsyncClient, archive_factory, printer_factory, headers
    ):
        """Second layer, for the scanners that do run JavaScript: a request
        that already looks automated is served the button without the script
        that would press it."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="no-script-token")
        assert archive.confirm_token == "no-script-token"

        response = await async_client.get("/api/v1/archives/confirm/no-script-token/good?tap=1", headers=headers)
        assert response.status_code == 200
        assert "<form method='post'" in response.text
        assert "<script" not in response.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "agent",
        [
            # A mail-security sandbox detonating the link: renders HTML, runs
            # JavaScript, and says it is Chrome — because as far as it is
            # concerned it is.
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36",
            # Not even hiding, and the User-Agent list still has no word for it.
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "HeadlessChrome/126.0.0.0 Safari/537.36",
        ],
    )
    async def test_a_link_out_of_the_message_body_never_submits_itself(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session, agent
    ):
        """The residual after taking the write off GET, now closed.

        The User-Agent list catches the fetchers that announce themselves, and
        none of those run JavaScript anyway. The ones that do run it — Safe
        Links detonation, click-time sandboxing, a browser-isolation proxy —
        send an ordinary Chrome string, so the list cannot name them and the
        page used to press its own button for them.

        What it can never have is the ``?tap=1`` off a notification button: a
        verdict URL only reaches a scanner by travelling in message text, where
        the marker is not. An install that kept ``{good_url}`` in its prompt
        body (the migration leaves an edited body alone, on purpose) is covered
        by this, which is the case the plain URL below stands for.
        """
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="detonated-token")

        response = await async_client.get(
            "/api/v1/archives/confirm/detonated-token/good", headers={"user-agent": agent}
        )
        assert response.status_code == 200
        assert "Confirm this outcome" in response.text
        assert "<form method='post'" in response.text
        assert "submit()" not in response.text, "a body link must never press its own button"

        await db_session.refresh(archive)
        assert archive.user_verdict is None
        assert archive.confirm_token_used_at is None, "the operator's tap must still be worth something"

        # And the same browser, arriving from the button, is still one tap.
        marked = await async_client.get(
            "/api/v1/archives/confirm/detonated-token/good?tap=1", headers={"user-agent": agent}
        )
        assert "getElementById('confirm-form').submit()" in marked.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_head_request_records_nothing(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Mail scanners probe with HEAD. FastAPI's APIRoute does not widen a
        GET route to HEAD the way a plain Starlette Route does, so the probe is
        refused outright — and the GET it would widen to writes nothing now."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="head-token")

        assert (await async_client.head("/api/v1/archives/confirm/head-token/good")).status_code == 405

        await db_session.refresh(archive)
        assert archive.user_verdict is None
        assert archive.confirm_token_used_at is None

    def test_confirm_links_exempt_from_auth_middleware(self):
        """The one-tap links are tapped on a phone with no session, so the
        global auth middleware must step aside for them — the capability token
        in the path is the credential. Pins the PUBLIC_API_PREFIXES entry;
        without it, enabling authentication 401s every verdict link."""
        from backend.app.main import PUBLIC_API_PREFIXES

        assert "/api/v1/archives/confirm/" in PUBLIC_API_PREFIXES


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
        assert newest_pending.user_verdict_source == "plate_clear"
        assert newest_pending.confirm_token == "pending-token"
        assert newest_pending.confirm_token_used_at is not None
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


class TestVerdictSource:
    """How a verdict came in (#1898) — every writer on this branch stamps it."""

    def test_every_source_has_a_phrase_for_the_already_answered_page(self):
        """Including 'reaction', which the Telegram reaction work (#3046) will
        write from its own branch: the page that explains a spent link must not
        fall silent the day it lands."""
        from backend.app.api.routes.archives import _VERDICT_SOURCE_PHRASES
        from backend.app.services.print_confirmation import VERDICT_SOURCES

        assert set(VERDICT_SOURCES) == set(_VERDICT_SOURCE_PHRASES)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_response_exposes_the_source(self, async_client: AsyncClient, archive_factory, printer_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)

        assert (await async_client.get(f"/api/v1/archives/{archive.id}")).json()["user_verdict_source"] is None

        assert (
            await async_client.patch(
                f"/api/v1/archives/{archive.id}", json={"user_verdict": "good", "user_verdict_source": "dialog"}
            )
        ).status_code == 200
        body = (await async_client.get(f"/api/v1/archives/{archive.id}")).json()
        assert body["user_verdict"] == "good"
        assert body["user_verdict_source"] == "dialog"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_printer_card_and_bare_patch_sources(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        printer = await printer_factory()
        from_card = await archive_factory(printer.id, confirm_requested=True)
        from_script = await archive_factory(printer.id, confirm_requested=True)

        await async_client.patch(
            f"/api/v1/archives/{from_card.id}", json={"user_verdict": "good", "user_verdict_source": "printer_card"}
        )
        # No source claimed: some script or integration did it.
        await async_client.patch(f"/api/v1/archives/{from_script.id}", json={"user_verdict": "reject"})

        await db_session.refresh(from_card)
        await db_session.refresh(from_script)
        assert from_card.user_verdict_source == "printer_card"
        assert from_script.user_verdict_source == "api"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_server_owned_sources_cannot_be_claimed_over_the_api(
        self, async_client: AsyncClient, archive_factory, printer_factory
    ):
        """'link', 'plate_clear' and 'reaction' are stamped by the paths that
        own them — a PATCH may not forge them."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)

        for forged in ("link", "plate_clear", "reaction", "nonsense"):
            response = await async_client.patch(
                f"/api/v1/archives/{archive.id}", json={"user_verdict": "good", "user_verdict_source": forged}
            )
            assert response.status_code == 422, forged

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_page_dates_the_verdict_on_file_not_the_spent_token(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """A verdict changed later must not be reported with the older decision's time.

        The live case: the plate-clear default answers a print, the operator
        changes the verdict in the app an hour later, then the old Telegram link
        is tapped. Reporting the new verdict with the old timestamp would
        describe two different events as one.
        """
        from datetime import datetime, timedelta, timezone

        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="dates-token")

        await async_client.patch(
            f"/api/v1/archives/{archive.id}", json={"user_verdict": "good", "user_verdict_source": "dialog"}
        )
        await db_session.refresh(archive)
        first_at = archive.user_verdict_at
        assert first_at is not None
        # Age the first decision so the two timestamps cannot coincide.
        archive.user_verdict_at = first_at - timedelta(hours=1)
        archive.confirm_token_used_at = first_at - timedelta(hours=1)
        await db_session.commit()

        await async_client.patch(
            f"/api/v1/archives/{archive.id}", json={"user_verdict": "reject", "user_verdict_source": "dialog"}
        )
        await db_session.refresh(archive)
        assert archive.user_verdict == "reject"
        assert archive.user_verdict_at > archive.confirm_token_used_at

        page = await async_client.get(f"/api/v1/archives/confirm/{archive.confirm_token}/good")
        assert page.status_code == 200

        # SQLite hands back naive datetimes; they are already UTC, which is how
        # the route formats them too.
        def _as_shown(value):
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc)
            return value.strftime("%Y-%m-%d %H:%M UTC")

        shown = _as_shown(archive.user_verdict_at)
        stale = _as_shown(archive.confirm_token_used_at)
        assert shown in page.text
        assert stale not in page.text
        # And the link still changed nothing.
        await db_session.refresh(archive)
        assert archive.user_verdict == "reject"

    async def test_a_re_sent_prompt_carries_a_live_token(self, archive_factory, printer_factory, db_session):
        """A spent token must never be re-used for a new prompt.

        Since a verdict keeps the token value on the row, "has a token" stopped
        meaning "answerable": without minting a fresh one, every button in the
        new message would land on the already-answered page.
        """
        from backend.app.main import dispatch_outcome_confirmation

        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="spent-token")
        from backend.app.services.print_confirmation import retire_confirm_token

        retire_confirm_token(archive)
        await db_session.commit()
        assert archive.confirm_token_used_at is not None

        await dispatch_outcome_confirmation(db_session, printer.id, printer.name, {}, archive.id, None)

        await db_session.refresh(archive)
        assert archive.confirm_token != "spent-token"
        assert archive.confirm_token_used_at is None

    async def test_clearing_the_verdict_drops_the_source(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="drop-source-token")

        await async_client.patch(
            f"/api/v1/archives/{archive.id}", json={"user_verdict": "good", "user_verdict_source": "dialog"}
        )
        await async_client.patch(f"/api/v1/archives/{archive.id}", json={"user_verdict": None})

        await db_session.refresh(archive)
        assert archive.user_verdict is None
        assert archive.user_verdict_source is None
        # ...but the capability stays spent.
        assert archive.confirm_token_used_at is not None
