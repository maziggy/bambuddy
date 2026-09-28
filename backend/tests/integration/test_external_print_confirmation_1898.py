"""Ask for the outcome of prints Bambuddy did not start (#1898 follow-up).

The ask-for-outcome flag rides from a queue item onto the archive at dispatch.
On a farm where most jobs are started at the printer's screen, in Bambu Studio
or in the Handy app there is no queue item to ride from, so every archive
``on_print_start`` created had ``confirm_requested`` false and the feature
looked broken. ``confirm_outcome_external_prints`` is the missing source.

Both archive-creating branches of ``on_print_start`` are driven here against a
real database: the no-3MF fallback (what a P1S/A1 farm actually hits) and the
normal path that archives a downloaded 3MF.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
)
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings

DISPATCH = "/data/Metadata/plate_1.gcode"
SUBTASK = "Bracket_plate_1"


@pytest.fixture(autouse=True)
def _clear_print_state():
    dicts = (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
    )
    for d in dicts:
        d.clear()
    yield
    for d in dicts:
        d.clear()


class _StubArchiveService:
    """Stands in for ArchiveService on the downloaded-3MF branch.

    Writes the row the real service would write, without needing a parseable
    3MF on disk. Everything this test asserts happens *after* the row exists.
    """

    def __init__(self, db):
        self.db = db

    async def archive_print(self, **kwargs):
        archive = PrintArchive(
            printer_id=kwargs.get("printer_id"),
            filename=f"{SUBTASK}.gcode.3mf",
            file_path=f"archives/test/{SUBTASK}.gcode.3mf",
            file_size=2048,
            print_name=SUBTASK,
            status="printing",
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(archive)
        await self.db.commit()
        await self.db.refresh(archive)
        return archive


async def _drive_print_start(test_engine, printer, *, download_ok: bool, extra_patches=()) -> dict:
    """Run ``on_print_start`` against the test database.

    ``download_ok`` picks the branch: False leaves the 3MF unreachable and the
    no-3MF fallback archive is written inline; True takes the ArchiveService
    branch.
    """
    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    state = MagicMock(
        current_project_url=f"ftp://{SUBTASK}.gcode.3mf",
        sdcard=True,
        sdcard_reported=True,
    )

    patches = [
        patch("backend.app.main.async_session", session_maker),
        patch("backend.app.core.database.async_session", session_maker),
        patch("backend.app.main.download_file_async", new=AsyncMock(return_value=download_ok)),
        patch("backend.app.main.download_file_try_paths_async", new=AsyncMock(return_value=None)),
        patch("backend.app.main.get_cached_3mf", return_value=None),
        patch("backend.app.main.cache_3mf_download"),
        patch("backend.app.main.peek_plate_index_in_3mf", return_value=None),
        patch("backend.app.main.ArchiveService", _StubArchiveService),
        # Imported inside the function, so patching it anywhere else lets the
        # directory walk open real sockets.
        patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=[])),
        patch("backend.app.main.ftps_handshake_blocked", return_value=False),
        patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30))),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
        # Real, it would spawn a task that outlives the test by a minute.
        patch("backend.app.main._schedule_fallback_3mf_retry"),
        patch("backend.app.main._store_spoolman_print_data", new_callable=AsyncMock),
        # Imported inside on_print_start; it would try to persist the mocked
        # printer state's AMS trays and fail on the MagicMock values.
        patch("backend.app.services.usage_tracker.on_print_start", new_callable=AsyncMock),
    ]

    mocks: dict = {}
    with ExitStack() as stack:
        for p in [*patches, *extra_patches]:
            mocks[p.attribute] = stack.enter_context(p)
        notif = stack.enter_context(patch("backend.app.main.notification_service"))
        plug = stack.enter_context(patch("backend.app.main.smart_plug_manager"))
        ws = stack.enter_context(patch("backend.app.main.ws_manager"))
        relay = stack.enter_context(patch("backend.app.main.mqtt_relay"))
        pm = stack.enter_context(patch("backend.app.main.printer_manager"))

        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_created = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        relay.on_print_start = AsyncMock()
        relay.on_archive_created = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST1898"))

        from backend.app.main import on_print_start

        await on_print_start(printer.id, {"filename": DISPATCH, "subtask_name": SUBTASK})
    return mocks


async def _set_setting(db_session, key: str, value: str) -> None:
    db_session.add(Settings(key=key, value=value))
    await db_session.commit()


async def _created_archive(db_session, printer_id: int) -> PrintArchive:
    db_session.expire_all()
    archive = await db_session.scalar(
        select(PrintArchive).where(PrintArchive.printer_id == printer_id).order_by(PrintArchive.id.desc()).limit(1)
    )
    assert archive is not None, "on_print_start created no archive"
    return archive


class TestExternalPrintGetsTheOutcomePrompt:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_3mf_fallback_asks_when_the_setting_is_on(self, test_engine, db_session, printer_factory):
        """The P1S/A1 case: the 3MF cannot be fetched, the archive is written
        inline — and that is the row the completion path reads the flag off."""
        printer = await printer_factory()
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")

        await _drive_print_start(test_engine, printer, download_ok=False)

        archive = await _created_archive(db_session, printer.id)
        assert archive.extra_data.get("no_3mf_available") is True
        assert archive.confirm_requested is True
        # What the completion path gates the prompt on.
        assert archive.user_verdict is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_archive_reaches_the_rest_of_the_1898_machinery(self, test_engine, db_session, printer_factory):
        """An externally started print is now a pending confirmation like any
        other: the prompt the completion path emits is gated on exactly these
        two fields, and the plate-release default resolves the same row."""
        from backend.app.services.print_confirmation import resolve_pending_confirmation_as_good

        printer = await printer_factory()
        printer_id = printer.id
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")

        await _drive_print_start(test_engine, printer, download_ok=False)

        archive = await _created_archive(db_session, printer_id)
        archive.status = "completed"
        await db_session.commit()

        assert await resolve_pending_confirmation_as_good(db_session, printer_id) == archive.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_downloaded_3mf_archive_asks_when_the_setting_is_on(self, test_engine, db_session, printer_factory):
        """The other creation branch: the 3MF arrived and ArchiveService wrote
        the row. The flag is set on the row afterwards rather than passed into
        archive_print, which also serves the queue dispatcher."""
        printer = await printer_factory()
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")

        await _drive_print_start(test_engine, printer, download_ok=True)

        archive = await _created_archive(db_session, printer.id)
        assert archive.file_path.endswith(".3mf")
        assert archive.confirm_requested is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_default_leaves_external_prints_alone(self, test_engine, db_session, printer_factory):
        """Default off: nothing about today's behaviour changes for an install
        that never touches the new setting — no row for it at all."""
        printer = await printer_factory()

        await _drive_print_start(test_engine, printer, download_ok=False)

        archive = await _created_archive(db_session, printer.id)
        assert archive.confirm_requested is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setting_off_does_not_ask(self, test_engine, db_session, printer_factory):
        printer = await printer_factory()
        await _set_setting(db_session, "confirm_outcome_external_prints", "false")

        await _drive_print_start(test_engine, printer, download_ok=False)

        assert (await _created_archive(db_session, printer.id)).confirm_requested is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_odd_stored_value_counts_as_off(self, test_engine, db_session, printer_factory):
        """Settings live in a VARCHAR column and every other reader treats
        anything that is not "true" as off."""
        printer = await printer_factory()
        await _set_setting(db_session, "confirm_outcome_external_prints", "None")

        await _drive_print_start(test_engine, printer, download_ok=False)

        assert (await _created_archive(db_session, printer.id)).confirm_requested is False


class TestAFailureHereCostsOnlyThePrompt:
    """The prompt is optional; the rest of print start is not.

    After a failed statement, a rollback of the whole transaction expires every
    object the session holds, and on an async session the next read of one
    raises instead of reloading. The print start below the check reads both
    the printer and the archive, so the check runs in a savepoint, and the flag
    write reloads what it used after its own rollback.
    """

    @staticmethod
    def _assert_print_start_finished(mocks, archive_id: int) -> None:
        assert archive_id in _active_prints.values()
        mocks["_record_energy_start"].assert_awaited()
        assert mocks["_record_energy_start"].await_args.args[0].id == archive_id
        mocks["_capture_timelapse_baseline_at_start"].assert_awaited()
        assert mocks["_capture_timelapse_baseline_at_start"].await_args.kwargs["archive_id"] == archive_id

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("download_ok", [True, False])
    async def test_a_failing_query_in_the_check(self, test_engine, db_session, printer_factory, download_ok):
        from sqlalchemy import text

        from backend.app.api.routes import settings as settings_routes

        real_get_setting = settings_routes.get_setting

        async def _broken_for_this_key(db, key):
            if key == "confirm_outcome_external_prints":
                # A real failed statement, not just an exception: this is what
                # leaves the transaction needing a rollback.
                await db.execute(text("SELECT no_such_column FROM settings"))
            return await real_get_setting(db, key)

        printer = await printer_factory()
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")

        mocks = await _drive_print_start(
            test_engine,
            printer,
            download_ok=download_ok,
            extra_patches=[patch.object(settings_routes, "get_setting", _broken_for_this_key)],
        )

        archive = await _created_archive(db_session, printer.id)
        assert archive.confirm_requested is False
        self._assert_print_start_finished(mocks, archive.id)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_failing_flag_write(self, test_engine, db_session, printer_factory):
        async def _yes_but_poison_the_commit(db, printer_id, observed_name=None):
            # Two rows for one unique key: the commit that carries the flag fails.
            db.add(Settings(key="x_1898_duplicate", value="a"))
            db.add(Settings(key="x_1898_duplicate", value="b"))
            return True

        printer = await printer_factory()

        mocks = await _drive_print_start(
            test_engine,
            printer,
            download_ok=True,
            extra_patches=[patch("backend.app.main._ask_outcome_for_external_print", _yes_but_poison_the_commit)],
        )

        archive = await _created_archive(db_session, printer.id)
        # archive_print committed the row; only the flag is lost.
        assert archive.confirm_requested is False
        self._assert_print_start_finished(mocks, archive.id)


class TestAQueuedPrintStillDecidesForItself:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_dispatched_job_is_not_overridden_by_the_setting(
        self, test_engine, db_session, printer_factory, archive_factory
    ):
        """A queue item that deliberately has the ask-for-outcome flag off must
        stay off. A restart mid-print empties the expected-print registry, so
        the queue row — which the scheduler commits to "printing" before the
        MQTT send — is the durable record that Bambuddy started this."""
        printer = await printer_factory()
        # Dispatched but not yet reported as started: the archive the scheduler
        # attached to the row only turns "printing" once on_print_start runs, so
        # the name-match resume above cannot find it and the queue row is the
        # only record that Bambuddy sent this print.
        source = await archive_factory(printer.id, filename=f"{SUBTASK}.gcode.3mf", status="pending", with_run=False)
        db_session.add(
            PrintQueueItem(
                printer_id=printer.id,
                archive_id=source.id,
                status="printing",
                confirm_outcome=False,
            )
        )
        await db_session.commit()
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")

        await _drive_print_start(test_engine, printer, download_ok=False)

        archive = await _created_archive(db_session, printer.id)
        assert archive.confirm_requested is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_queue_items_own_yes_survives_print_start(
        self, test_engine, db_session, printer_factory, archive_factory
    ):
        """The dispatcher copies ``confirm_outcome`` onto the archive before
        the print starts; the expected-print branch of ``on_print_start`` must
        leave that alone even while the external-print setting is off."""
        from backend.app.main import register_expected_print

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            filename=f"{SUBTASK}.gcode.3mf",
            status="pending",
            confirm_requested=True,
            with_run=False,
        )
        db_session.add(
            PrintQueueItem(
                printer_id=printer.id,
                archive_id=archive.id,
                status="printing",
                confirm_outcome=True,
            )
        )
        await db_session.commit()
        archive_id, printer_id = archive.id, printer.id
        register_expected_print(printer_id, f"{SUBTASK}.gcode.3mf", archive_id)

        await _drive_print_start(test_engine, printer, download_ok=False)

        db_session.expire_all()
        refreshed = await db_session.get(PrintArchive, archive_id)
        assert refreshed.status == "printing"
        assert refreshed.confirm_requested is True
        # No second row for the same print.
        rows = (await db_session.scalars(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).all()
        assert len(rows) == 1


class TestSettingsRoundTrip:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_defaults_to_off_and_round_trips(self, async_client):
        response = await async_client.get("/api/v1/settings/")
        assert response.status_code == 200
        assert response.json()["confirm_outcome_external_prints"] is False

        response = await async_client.put("/api/v1/settings/", json={"confirm_outcome_external_prints": True})
        assert response.status_code == 200
        assert response.json()["confirm_outcome_external_prints"] is True

        assert (await async_client.get("/api/v1/settings/")).json()["confirm_outcome_external_prints"] is True

        response = await async_client.put("/api/v1/settings/", json={"confirm_outcome_external_prints": False})
        assert response.status_code == 200
        assert (await async_client.get("/api/v1/settings/")).json()["confirm_outcome_external_prints"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_updating_it_leaves_the_per_job_default_alone(self, async_client):
        """Two different questions: one seeds the per-job toggle in the print
        dialog, the other covers prints that never see that dialog."""
        await async_client.put("/api/v1/settings/", json={"default_confirm_outcome": True})

        body = (await async_client.put("/api/v1/settings/", json={"confirm_outcome_external_prints": True})).json()
        assert body["default_confirm_outcome"] is True
        assert body["confirm_outcome_external_prints"] is True


class TestAStrandedQueueRowDoesNotMuteTheSetting:
    """A ``printing`` row is not proof that Bambuddy started what is printing now.

    ``_completion_belongs_to_queue_item`` deliberately leaves a row open when a
    completion's subtask name disagrees with the file it was dispatched with,
    and the scheduler's stranded sweep only takes it back once the printer has
    sat connected and terminal for minutes. Treating any such row as "we
    dispatched this" switched the setting off for every screen-started print in
    between -- the exact symptom the setting exists to cure.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_row_left_open_for_another_file_still_asks(
        self, test_engine, db_session, printer_factory, archive_factory
    ):
        printer = await printer_factory()
        stranded_for = await archive_factory(
            printer.id, filename="SomeOtherJob.gcode.3mf", status="printing", with_run=False
        )
        db_session.add(
            PrintQueueItem(
                printer_id=printer.id,
                archive_id=stranded_for.id,
                status="printing",
                confirm_outcome=False,
            )
        )
        await db_session.commit()
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")
        stranded_filename = stranded_for.filename

        await _drive_print_start(test_engine, printer, download_ok=False)

        archive = await _created_archive(db_session, printer.id)
        assert archive.filename != stranded_filename
        assert archive.confirm_requested is True


class TestAReprintAsksAgain:
    """The reprint reuses the archive row, and the completion prompt is gated on
    ``user_verdict is None`` -- so without a reset the second run inherits the
    first run's answer and is never asked about."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_previous_runs_verdict_does_not_carry_over(
        self, test_engine, db_session, printer_factory, archive_factory
    ):
        from backend.app.main import register_expected_print

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            filename=f"{SUBTASK}.gcode.3mf",
            status="completed",
            confirm_requested=True,
            user_verdict="good",
            confirm_token="token-from-the-first-run",
            with_run=False,
        )
        archive_id, printer_id = archive.id, printer.id
        register_expected_print(printer_id, f"{SUBTASK}.gcode.3mf", archive_id)

        await _drive_print_start(test_engine, printer, download_ok=False)

        db_session.expire_all()
        refreshed = await db_session.get(PrintArchive, archive_id)
        assert refreshed.status == "printing"
        assert refreshed.confirm_requested is True
        assert refreshed.user_verdict is None
        assert refreshed.confirm_token is None

        # And the completion really does ask again, which is the point.
        refreshed.status = "completed"
        await db_session.commit()
        sent, ws, notif = await _dispatch(db_session, printer_id, archive_id)
        assert sent is True
        ws.send_print_confirm_request.assert_awaited_once()
        notif.on_print_confirm_request.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_archive_that_was_never_asked_about_keeps_its_verdict(
        self, test_engine, db_session, printer_factory, archive_factory
    ):
        """The reset is scoped to rows that ask. An archive answered once and
        later reprinted with the flag off must keep the answer it has."""
        from backend.app.main import register_expected_print

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            filename=f"{SUBTASK}.gcode.3mf",
            status="completed",
            confirm_requested=False,
            user_verdict="reject",
            with_run=False,
        )
        archive_id = archive.id
        register_expected_print(printer.id, f"{SUBTASK}.gcode.3mf", archive_id)

        await _drive_print_start(test_engine, printer, download_ok=False)

        db_session.expire_all()
        assert (await db_session.get(PrintArchive, archive_id)).user_verdict == "reject"


async def _dispatch(db_session, printer_id: int, archive_id: int, **kwargs):
    """Run the completion path's outcome dispatch with the two emitters mocked."""
    from backend.app.main import dispatch_outcome_confirmation

    with (
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.notification_service") as notif,
    ):
        ws.send_print_confirm_request = AsyncMock()
        notif.on_print_confirm_request = AsyncMock()
        sent = await dispatch_outcome_confirmation(
            db_session,
            printer_id,
            "Bench P1S",
            {"subtask_name": SUBTASK},
            archive_id,
            **kwargs,
        )
    return sent, ws, notif


class TestTheCompletionEmitsThePrompt:
    """The half of the feature the user actually sees.

    Everything else here asserts a column value; this drives the block
    ``on_print_complete``'s notification task runs -- which is wrapped in a bare
    ``except Exception`` that only logs, so a regression in it is invisible
    unless something pins it.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_external_print_gets_a_prompt_with_one_tap_links(self, test_engine, db_session, printer_factory):
        printer = await printer_factory()
        printer_id = printer.id
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")
        await _set_setting(db_session, "external_url", "https://farm.example.com/")

        await _drive_print_start(test_engine, printer, download_ok=False)

        archive = await _created_archive(db_session, printer_id)
        archive_id = archive.id
        assert archive.confirm_requested is True
        archive.status = "completed"
        await db_session.commit()

        sent, ws, notif = await _dispatch(db_session, printer_id, archive_id)

        assert sent is True
        ws.send_print_confirm_request.assert_awaited_once()
        assert ws.send_print_confirm_request.await_args.args[1]["archive_id"] == archive_id

        notif.on_print_confirm_request.assert_awaited_once()
        kwargs = notif.on_print_confirm_request.await_args.kwargs
        token = (await db_session.get(PrintArchive, archive_id)).confirm_token
        assert token, "the one-tap links need a minted capability token"
        assert kwargs["good_url"] == f"https://farm.example.com/api/v1/archives/confirm/{token}/good"
        assert kwargs["reject_url"] == f"https://farm.example.com/api/v1/archives/confirm/{token}/reject"
        assert kwargs["confirm_url"] == f"https://farm.example.com/archives?confirm={archive_id}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_links_stay_absolute_without_an_external_url(self, test_engine, db_session, printer_factory):
        """Telegram's inline keyboard and ntfy's action buttons are both dropped
        for a relative URL, so an install that never set external_url used to
        get a message carrying two unusable paths and no buttons at all."""
        printer = await printer_factory()
        printer_id = printer.id
        await _set_setting(db_session, "confirm_outcome_external_prints", "true")

        await _drive_print_start(test_engine, printer, download_ok=False)

        archive = await _created_archive(db_session, printer_id)
        archive_id = archive.id
        archive.status = "completed"
        await db_session.commit()

        _, _, notif = await _dispatch(db_session, printer_id, archive_id)

        kwargs = notif.on_print_confirm_request.await_args.kwargs
        assert kwargs["good_url"].startswith("http")
        assert kwargs["reject_url"].startswith("http")
        assert kwargs["confirm_url"].startswith("http")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_failed_prompt_leaves_the_session_usable_for_the_email(self, db_session):
        """The completion task sends the per-user print email on the same
        session right after the prompt, so a prompt that dies mid-flush must
        not take the email with it."""
        from backend.app.main import _dispatch_outcome_confirmation_safely

        async def _dies_mid_flush(db, *args, **kwargs):
            db.add(Settings(key="x_1898_duplicate", value="a"))
            db.add(Settings(key="x_1898_duplicate", value="b"))
            await db.flush()

        with patch("backend.app.main.dispatch_outcome_confirmation", _dies_mid_flush):
            await _dispatch_outcome_confirmation_safely(db_session, 1, "X1C", {}, 1, {})

        # What _dispatch_user_print_email does next: read from the session.
        # Without the rollback this raises PendingRollbackError.
        await db_session.execute(select(Settings).limit(1))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_already_answered_archive_is_not_asked_again(self, db_session, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id, status="completed", confirm_requested=True, user_verdict="good", with_run=False
        )

        sent, ws, notif = await _dispatch(db_session, printer.id, archive.id)

        assert sent is False
        ws.send_print_confirm_request.assert_not_awaited()
        notif.on_print_confirm_request.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_archive_that_never_opted_in_is_not_asked(self, db_session, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="completed", confirm_requested=False, with_run=False)

        sent, _, notif = await _dispatch(db_session, printer.id, archive.id)

        assert sent is False
        notif.on_print_confirm_request.assert_not_awaited()
