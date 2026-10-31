"""Each print of an archive has its own outcome question (#1898, archive redesign P1b).

Plates of one archive printed on two printers are two runs. Each asks for its
own verdict with its own one-tap token; an answer lands on the run it was
asked about, and the archive shows its latest run's.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

T1 = datetime(2026, 10, 30, 9)
T2 = T1 + timedelta(hours=1)


@pytest.fixture
async def two_plates(db_session, printer_factory):
    """One archive, plate 1 printed on A, then plate 2 on B; both asked for
    their outcome and neither is answered."""
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    archive = PrintArchive(
        printer_id=b.id,
        filename="two.gcode.3mf",
        file_path="archives/test/two.gcode.3mf",
        file_size=1,
        print_name="two",
        status="completed",
        started_at=T2,
        confirm_requested=True,
    )
    db_session.add(archive)
    await db_session.flush()
    first = PrintLogEntry(
        archive_id=archive.id, printer_id=a.id, plate_id=1, status="completed", started_at=T1, confirm_requested=True
    )
    second = PrintLogEntry(
        archive_id=archive.id, printer_id=b.id, plate_id=2, status="completed", started_at=T2, confirm_requested=True
    )
    db_session.add_all([first, second])
    await db_session.commit()
    return SimpleNamespace(archive=archive.id, a=a.id, b=b.id, first=first.id, second=second.id)


async def _row(db_session, model, row_id):
    db_session.expire_all()
    return await db_session.get(model, row_id)


async def _prompt(db_session, plates, run_id):
    """The completion's outcome prompt for ``run_id``; returns (ws payload, provider kwargs)."""
    from backend.app.main import dispatch_outcome_confirmation

    with (
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.notification_service") as notify,
    ):
        ws.send_print_confirm_request = AsyncMock()
        notify.on_print_confirm_request = AsyncMock()
        sent = await dispatch_outcome_confirmation(db_session, plates.a, "A", {}, plates.archive, run_id=run_id)
    assert sent
    return ws.send_print_confirm_request.await_args.args[1], notify.on_print_confirm_request.await_args.kwargs


async def test_each_plates_prompt_has_its_own_token(db_session, two_plates):
    first_ws, first_links = await _prompt(db_session, two_plates, two_plates.first)
    await _prompt(db_session, two_plates, two_plates.second)

    first_token = (await _row(db_session, PrintLogEntry, two_plates.first)).confirm_token
    second_token = (await _row(db_session, PrintLogEntry, two_plates.second)).confirm_token
    assert first_token and second_token and first_token != second_token
    assert (await _row(db_session, PrintArchive, two_plates.archive)).confirm_token is None
    assert first_ws["run_id"] == two_plates.first
    assert first_links["run_id"] == two_plates.first
    assert first_links["good_url"].endswith(f"/confirm/{first_token}/good")
    assert first_links["confirm_url"].endswith(f"?confirm={two_plates.archive}&run={two_plates.first}")


async def test_an_answered_plate_is_not_asked_again(db_session, two_plates):
    from backend.app.main import dispatch_outcome_confirmation

    first = await _row(db_session, PrintLogEntry, two_plates.first)
    first.user_verdict = "good"
    await db_session.commit()

    with patch("backend.app.main.ws_manager"), patch("backend.app.main.notification_service"):
        assert not await dispatch_outcome_confirmation(
            db_session, two_plates.a, "A", {}, two_plates.archive, run_id=two_plates.first
        )


async def test_a_plates_link_answers_that_plate(async_client, db_session, two_plates):
    await _prompt(db_session, two_plates, two_plates.first)
    token = (await _row(db_session, PrintLogEntry, two_plates.first)).confirm_token

    response = await async_client.post(f"/api/v1/archives/confirm/{token}/reject")
    assert response.status_code == 200, response.text

    first = await _row(db_session, PrintLogEntry, two_plates.first)
    assert (first.user_verdict, first.user_verdict_source) == ("reject", "link")
    assert first.confirm_token_used_at is not None
    assert (await _row(db_session, PrintLogEntry, two_plates.second)).user_verdict is None
    # The archive shows plate 2, its latest, still unanswered.
    assert (await _row(db_session, PrintArchive, two_plates.archive)).user_verdict is None

    again = await async_client.get(f"/api/v1/archives/confirm/{token}/good")
    assert "Already answered" in again.text and "Rejected" in again.text
    assert f"run={two_plates.first}" in again.text


async def test_a_link_sent_before_runs_had_tokens_answers_the_latest(async_client, db_session, two_plates):
    archive = await _row(db_session, PrintArchive, two_plates.archive)
    archive.confirm_token = "archive-token"
    await db_session.commit()

    assert (await async_client.post("/api/v1/archives/confirm/archive-token/good")).status_code == 200

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    assert (archive.user_verdict, archive.confirm_token_used_at is not None) == ("good", True)
    assert (await _row(db_session, PrintLogEntry, two_plates.second)).user_verdict == "good"
    assert (await _row(db_session, PrintLogEntry, two_plates.first)).user_verdict is None


async def test_the_dialog_answers_the_plate_it_asks_about(async_client, db_session, two_plates):
    response = await async_client.patch(
        f"/api/v1/archives/{two_plates.archive}",
        json={
            "user_verdict": "reject",
            "failure_reason": "spaghetti",
            "user_verdict_source": "dialog",
            "run_id": two_plates.first,
        },
    )
    assert response.status_code == 200, response.text

    first = await _row(db_session, PrintLogEntry, two_plates.first)
    assert (first.user_verdict, first.user_verdict_source, first.failure_reason) == ("reject", "dialog", "spaghetti")
    second = await _row(db_session, PrintLogEntry, two_plates.second)
    assert (second.user_verdict, second.failure_reason) == (None, None)
    archive = await _row(db_session, PrintArchive, two_plates.archive)
    assert (archive.user_verdict, archive.failure_reason) == (None, None)


async def test_an_answer_for_the_latest_plate_shows_on_the_archive(async_client, db_session, two_plates):
    response = await async_client.patch(
        f"/api/v1/archives/{two_plates.archive}",
        json={"user_verdict": "good", "user_verdict_source": "printer_card", "run_id": two_plates.second},
    )
    assert response.status_code == 200, response.text

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    assert (archive.user_verdict, archive.user_verdict_source) == ("good", "printer_card")
    assert (await _row(db_session, PrintLogEntry, two_plates.first)).user_verdict is None


async def test_an_answer_without_a_run_goes_to_the_latest(async_client, db_session, two_plates):
    response = await async_client.patch(f"/api/v1/archives/{two_plates.archive}", json={"user_verdict": "good"})
    assert response.status_code == 200, response.text

    assert (await _row(db_session, PrintLogEntry, two_plates.second)).user_verdict == "good"
    assert (await _row(db_session, PrintLogEntry, two_plates.first)).user_verdict is None
    assert (await _row(db_session, PrintArchive, two_plates.archive)).user_verdict == "good"


async def test_a_run_of_another_archive_or_other_fields_are_refused(async_client, db_session, two_plates):
    other = PrintArchive(filename="o.3mf", file_path="archives/test/o.3mf", file_size=1, status="completed")
    db_session.add(other)
    await db_session.flush()
    stranger = PrintLogEntry(archive_id=other.id, status="completed")
    db_session.add(stranger)
    await db_session.commit()

    url = f"/api/v1/archives/{two_plates.archive}"
    assert (await async_client.patch(url, json={"user_verdict": "good", "run_id": stranger.id})).status_code == 404
    assert (await async_client.patch(url, json={"notes": "x", "run_id": two_plates.first})).status_code == 400


async def test_the_archive_can_be_read_as_one_of_its_runs(async_client, db_session, two_plates):
    first = await _row(db_session, PrintLogEntry, two_plates.first)
    first.user_verdict = "reject"
    await db_session.commit()

    response = await async_client.get(f"/api/v1/archives/{two_plates.archive}?run_id={two_plates.first}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["run_id"], body["printer_id"], body["plate_id"], body["user_verdict"]) == (
        two_plates.first,
        two_plates.a,
        1,
        "reject",
    )
    plain = (await async_client.get(f"/api/v1/archives/{two_plates.archive}")).json()
    assert (plain["run_id"], plain["user_verdict"]) == (None, None)
    missing = await async_client.get(f"/api/v1/archives/{two_plates.archive}?run_id=999999")
    assert missing.status_code == 404


async def test_each_printer_card_shows_its_own_plate(async_client, db_session, two_plates):
    first = await _row(db_session, PrintLogEntry, two_plates.first)
    first.user_verdict = "good"
    await db_session.commit()

    response = await async_client.get("/api/v1/archives/last-per-printer")
    assert response.status_code == 200, response.text
    cards = {card["printer_id"]: card for card in response.json()}
    assert {pid: (c["id"], c["run_id"], c["plate_id"], c["user_verdict"]) for pid, c in cards.items()} == {
        two_plates.a: (two_plates.archive, two_plates.first, 1, "good"),
        two_plates.b: (two_plates.archive, two_plates.second, 2, None),
    }


async def test_clearing_a_printers_plate_answers_its_own_plate(db_session, two_plates):
    from backend.app.services.print_confirmation import resolve_pending_confirmation_as_good

    assert await resolve_pending_confirmation_as_good(db_session, two_plates.a) == two_plates.archive
    await db_session.commit()

    first = await _row(db_session, PrintLogEntry, two_plates.first)
    assert (first.user_verdict, first.user_verdict_source) == ("good", "plate_clear")
    assert (await _row(db_session, PrintLogEntry, two_plates.second)).user_verdict is None
    assert (await _row(db_session, PrintArchive, two_plates.archive)).user_verdict is None


async def test_a_new_plate_ending_shows_its_open_question_on_the_archive(db_session, two_plates):
    """The archive's verdict was the previous print's: the latest run's (none
    yet) replaces it once the new run ends."""
    from backend.app.services.print_log import mirror_latest_run

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    first = await db_session.get(PrintLogEntry, two_plates.first)
    archive.user_verdict = "good"
    archive.user_verdict_source = "dialog"
    first.user_verdict = "good"
    await db_session.commit()

    await mirror_latest_run(db_session, two_plates.archive)
    await db_session.commit()

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    assert (archive.user_verdict, archive.user_verdict_source) == (None, None)


async def test_a_run_without_provenance_keeps_the_archives(db_session, two_plates):
    """Runs logged before they kept how the verdict arrived: the same verdict
    on the archive keeps its own provenance."""
    from backend.app.services.print_log import mirror_latest_run

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    second = await db_session.get(PrintLogEntry, two_plates.second)
    archive.user_verdict = "good"
    archive.user_verdict_source = "plate_clear"
    second.user_verdict = "good"
    await db_session.commit()

    await mirror_latest_run(db_session, two_plates.archive)
    await db_session.commit()

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    assert (archive.user_verdict, archive.user_verdict_source) == ("good", "plate_clear")


async def test_a_run_logged_before_runs_owned_their_verdict_leaves_the_archives(db_session, two_plates):
    """Its verdict was never copied onto it: the archive's is kept."""
    from backend.app.services.print_log import mirror_latest_run

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    second = await db_session.get(PrintLogEntry, two_plates.second)
    archive.user_verdict = "reject"
    second.confirm_requested = None
    await db_session.commit()

    await mirror_latest_run(db_session, two_plates.archive)
    await db_session.commit()

    assert (await _row(db_session, PrintArchive, two_plates.archive)).user_verdict == "reject"


async def test_a_run_on_a_printer_outside_the_callers_scope_is_not_theirs(async_client, db_session, two_plates):
    """Scoped to printer B, the archive's: plate 1 printed on A is neither
    read nor judged through the archive, and A's card is not theirs."""
    from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

    admin = await _admin_token(async_client)
    jwt, _ = await _user(
        async_client,
        admin,
        "scoped",
        ["archives:read_all", "archives:update_all", "printers:read"],
        printer_ids=[two_plates.b],
    )
    url = f"/api/v1/archives/{two_plates.archive}"

    assert (await async_client.get(f"{url}?run_id={two_plates.first}", headers=_auth(jwt))).status_code == 404
    patched = await async_client.patch(
        url, headers=_auth(jwt), json={"user_verdict": "reject", "run_id": two_plates.first}
    )
    assert patched.status_code == 404
    assert (await _row(db_session, PrintLogEntry, two_plates.first)).user_verdict is None
    # Their own printer's plate is theirs.
    own = await async_client.patch(url, headers=_auth(jwt), json={"user_verdict": "good", "run_id": two_plates.second})
    assert own.status_code == 200, own.text

    cards = (await async_client.get("/api/v1/archives/last-per-printer", headers=_auth(jwt))).json()
    assert [(c["printer_id"], c["run_id"]) for c in cards] == [(two_plates.b, two_plates.second)]


async def test_a_card_is_shown_for_the_plate_printed_on_the_callers_printer(async_client, db_session, two_plates):
    """Scoped to printer A, whose plate it printed, while the archive names
    B: the archive is theirs to see by that plate (archive redesign), and the
    card is that plate's."""
    from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

    admin = await _admin_token(async_client)
    jwt, _ = await _user(
        async_client, admin, "scoped-a", ["archives:read_all", "printers:read"], printer_ids=[two_plates.a]
    )

    cards = (await async_client.get("/api/v1/archives/last-per-printer", headers=_auth(jwt))).json()
    assert [(c["id"], c["run_id"], c["printer_id"]) for c in cards] == [
        (two_plates.archive, two_plates.first, two_plates.a)
    ]
