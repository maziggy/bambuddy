"""Regression tests for ``BackgroundDispatchService._verify_print_response``.

The background-dispatch watchdog used to be fire-and-forget — it logged a
warning and force-reconnected MQTT, but the dispatch job had already been
marked successful. The user therefore saw "Print started successfully" while
the printer never actually transitioned (#1042 follow-up). The watchdog now
returns a bool so the caller can fail the dispatch job when the printer
doesn't acknowledge the command, mirroring what `_watchdog_print_start` does
on the queue side.

Both transition signals are accepted: ``state`` advancing past ``pre_state``
*or* ``subtask_id`` advancing past ``pre_subtask_id`` — H2D firmware can sit
at FINISH for ~50 s after accepting ``project_file`` while echoing the new
subtask_id back almost immediately (#1078).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.background_dispatch import BackgroundDispatchService


def _status(state: str, subtask_id: str | None = None, gcode_file: str | None = None):
    """Minimal stand-in for PrinterState — only the fields the watchdog reads."""
    return SimpleNamespace(state=state, subtask_id=subtask_id, gcode_file=gcode_file)


class TestReturnsTrueOnPickup:
    @pytest.mark.asyncio
    async def test_returns_true_on_state_change(self):
        get_status = MagicMock(return_value=_status("RUNNING", "OLD_SUBTASK"))
        with patch(
            "backend.app.services.background_dispatch.printer_manager.get_status",
            get_status,
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1S",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.3,
                poll_interval=0.05,
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_on_subtask_id_change_even_if_state_still_finish(self):
        """#1078: H2D keeps state=FINISH for ~50 s after accepting project_file
        but flips subtask_id immediately. Must be accepted as a pickup signal."""
        get_status = MagicMock(return_value=_status("FINISH", "NEW_SUBTASK_12345"))
        with patch(
            "backend.app.services.background_dispatch.printer_manager.get_status",
            get_status,
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="H2D",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK_99999",
                timeout=0.3,
                poll_interval=0.05,
            )

        assert result is True


class TestReturnsFalseOnTimeout:
    @pytest.mark.asyncio
    async def test_returns_false_when_neither_state_nor_subtask_id_changes(self):
        """The exact #1042 scenario: P1S sits in FAILED with HMS pending,
        accepts the MQTT publish, never transitions. Watchdog must report
        failure so the caller fails the dispatch job."""
        get_status = MagicMock(return_value=_status("FINISH", "OLD_SUBTASK"))
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1S",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        assert result is False
        client.force_reconnect_stale_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_pre_subtask_id_none_and_state_unchanged(self):
        """Backward-compat: callers without a captured pre_subtask_id (e.g. the
        printer never reported one) must still get the timeout failure path
        based on state alone."""
        get_status = MagicMock(return_value=_status("FINISH", "ANYTHING"))
        get_client = MagicMock(return_value=None)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1S",
                pre_state="FINISH",
                pre_subtask_id=None,
                timeout=0.2,
                poll_interval=0.05,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_subtask_id_none_post_dispatch_does_not_count_as_change(self):
        """If the printer transiently reports subtask_id=None during the
        watchdog window (e.g. mid-reconnect), that must not be treated as
        "advanced past pre_subtask_id" — otherwise we'd false-pass and mark
        a never-started print as successful."""
        get_status = MagicMock(return_value=_status("FINISH", None))
        get_client = MagicMock(return_value=None)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1S",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        assert result is False


class TestDisconnectHandling:
    @pytest.mark.asyncio
    async def test_disconnect_does_not_short_circuit_window(self):
        """A momentary ``get_status() is None`` (brief MQTT disconnect mid-window)
        must not immediately fail the dispatch — the printer may reconnect and
        still produce a valid transition before timeout. Falsely failing on the
        first missed tick is the previous bug class we're moving away from."""
        # First call: disconnected. Second call onward: reconnected and transitioned.
        get_status = MagicMock(side_effect=[None, _status("RUNNING")])
        with patch(
            "backend.app.services.background_dispatch.printer_manager.get_status",
            get_status,
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1S",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.3,
                poll_interval=0.05,
            )

        assert result is True
        assert get_status.call_count >= 2

    @pytest.mark.asyncio
    async def test_disconnect_for_full_window_returns_false(self):
        """Persistent disconnect for the full window is treated as failure.
        Better to false-fail and let the user retry than to false-succeed and
        leave them watching an idle printer (#1042)."""
        get_status = MagicMock(return_value=None)
        get_client = MagicMock(return_value=None)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1S",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        assert result is False


class TestDefaults:
    def test_default_timeout_matches_queue_watchdog(self):
        """Queue and background watchdogs need the same 90 s default to give
        slow H2D FINISH→PREPARE transitions the same headroom on both paths."""
        import inspect

        sig = inspect.signature(BackgroundDispatchService._verify_print_response)
        assert sig.parameters["timeout"].default == 90.0


class TestGcodeFileDiscriminator:
    """#1150 vs #887/#936 discriminator: skip the forced reconnect when the
    printer's gcode_file changed since pre-dispatch (project_file landed,
    printer is parsing slowly — reconnecting mid-parse causes 0500_4003).
    Reconnect when gcode_file is unchanged (publish was silently swallowed —
    half-broken session needs the original recovery)."""

    @pytest.mark.asyncio
    async def test_skips_reconnect_when_gcode_file_changed(self):
        get_status = MagicMock(
            return_value=_status("FINISH", "OLD_SUBTASK", gcode_file="/new.3mf"),
        )
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            result = await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1P",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                pre_gcode_file="/old.3mf",
                timeout=0.2,
                poll_interval=0.05,
            )

        assert result is False
        client.force_reconnect_stale_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnects_when_gcode_file_unchanged(self):
        # The half-broken-session case (#887/#936): publish was dropped, so
        # the printer is still showing the previous file. Reconnect to clear
        # the broken paho QoS-1 queue.
        get_status = MagicMock(
            return_value=_status("FINISH", "OLD_SUBTASK", gcode_file="/old.3mf"),
        )
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1P",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                pre_gcode_file="/old.3mf",
                timeout=0.2,
                poll_interval=0.05,
            )

        client.force_reconnect_stale_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_reconnect_when_pre_gcode_file_was_none(self):
        # Printer just connected (pre_gcode_file=None) and now reports a
        # file — that's a clear "command landed" signal too.
        get_status = MagicMock(
            return_value=_status("FINISH", "OLD_SUBTASK", gcode_file="/new.3mf"),
        )
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1P",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                pre_gcode_file=None,
                timeout=0.2,
                poll_interval=0.05,
            )

        client.force_reconnect_stale_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnects_when_no_pre_gcode_file_arg_supplied(self):
        # Backward-compat: callers that don't pass pre_gcode_file at all
        # (everything but our updated dispatch sites) must still get the
        # original reconnect-on-timeout behaviour. Here pre_gcode_file
        # defaults to None and the printer's current gcode_file is also
        # None → publish_landed=False → reconnect.
        get_status = MagicMock(
            return_value=_status("FINISH", "OLD_SUBTASK", gcode_file=None),
        )
        client = MagicMock()
        get_client = MagicMock(return_value=client)

        with (
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                get_status,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_client",
                get_client,
            ),
        ):
            await BackgroundDispatchService._verify_print_response(
                printer_id=42,
                printer_name="P1P",
                pre_state="FINISH",
                pre_subtask_id="OLD_SUBTASK",
                timeout=0.2,
                poll_interval=0.05,
            )

        client.force_reconnect_stale_session.assert_called_once()


# ---------------------------------------------------------------------------
# Integration tests: the call sites in _run_reprint_archive and
# _run_print_library_file must (a) await the watchdog instead of fire-and-
# forget, (b) raise RuntimeError on watchdog False so _run_active_job marks
# the job failed, (c) rollback the library-file flow's freshly-created
# archive on timeout. Heavy mocking — the goal is to verify the new wiring,
# not to re-test the dependencies.
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from backend.app.services.background_dispatch import (  # noqa: E402
    ActiveDispatchState,
    PrintDispatchJob,
)


def _make_session_factory(db_mock):
    """Build an async-session factory whose context manager yields ``db_mock``.

    Mirrors the ``async with async_session() as db`` shape used by both
    ``_run_*`` methods so the test can intercept ``db.rollback`` / ``db.scalar``.
    """

    @asynccontextmanager
    async def _factory():
        yield db_mock

    return _factory


def _printer_namespace():
    return SimpleNamespace(
        id=10,
        name="P1S",
        ip_address="1.2.3.4",
        access_code="abc",
        model="P1S",
    )


def _make_dispatch_job(kind: str = "reprint_archive") -> PrintDispatchJob:
    return PrintDispatchJob(
        id=1,
        kind=kind,
        source_id=99,
        source_name="Test.gcode.3mf",
        printer_id=10,
        printer_name="P1S",
        options={},
        requested_by_user_id=None,
        requested_by_username=None,
    )


@pytest.fixture
def reprint_archive_mocks(tmp_path):
    """Mock harness for ``_run_reprint_archive`` covering every external
    dependency up to (and including) ``start_print``. The watchdog is left
    real so the caller can patch ``_verify_print_response`` per-test."""
    archive_file = tmp_path / "test.3mf"
    archive_file.write_bytes(b"fake-3mf-content")

    archive = SimpleNamespace(
        id=99,
        filename="Test.gcode.3mf",
        file_path=str(archive_file),
    )

    db = MagicMock()
    db.scalar = AsyncMock(return_value=_printer_namespace())
    db.rollback = AsyncMock()

    archive_service = MagicMock()
    archive_service.get_archive = AsyncMock(return_value=archive)

    return {
        "archive": archive,
        "archive_file": archive_file,
        "db": db,
        "archive_service": archive_service,
        "session_factory": _make_session_factory(db),
    }


@pytest.fixture
def library_file_mocks(tmp_path):
    """Mock harness for ``_run_print_library_file`` — separate from the
    reprint fixture because the library flow creates its archive via
    ``archive_service.archive_print(...)`` rather than fetching one."""
    src_file = tmp_path / "lib_src.3mf"
    src_file.write_bytes(b"fake-3mf-content")

    lib_file = SimpleNamespace(
        id=22,
        filename="cube.gcode.3mf",
        file_path=str(src_file.relative_to(tmp_path)),
    )
    lib_file.active = staticmethod(lambda: lib_file)  # mimic LibraryFile.active() chainable

    new_archive = SimpleNamespace(id=500, filename="cube.gcode.3mf", file_path=str(src_file))

    db = MagicMock()
    db.scalar = AsyncMock()  # configured per-test
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    archive_service = MagicMock()
    archive_service.archive_print = AsyncMock(return_value=new_archive)

    return {
        "lib_file": lib_file,
        "src_file": src_file,
        "new_archive": new_archive,
        "db": db,
        "archive_service": archive_service,
        "session_factory": _make_session_factory(db),
    }


class TestReprintArchiveDispatchWiring:
    """Verify ``_run_reprint_archive`` (a) awaits the watchdog inline and
    (b) raises RuntimeError on False so the dispatch job is marked failed."""

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_watchdog_returns_false(self, reprint_archive_mocks):
        """The exact #1042 propagation gap: watchdog detects non-transition,
        _run_reprint_archive must surface it as a RuntimeError so the surrounding
        _run_active_job marks the job failed (instead of silently completing)."""
        from backend.app.services.background_dispatch import BackgroundDispatchService

        m = reprint_archive_mocks
        service = BackgroundDispatchService()
        job = _make_dispatch_job(kind="reprint_archive")

        watchdog = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.background_dispatch.async_session", m["session_factory"]),
            patch(
                "backend.app.services.background_dispatch.ArchiveService",
                return_value=m["archive_service"],
            ),
            patch.object(BackgroundDispatchService, "_verify_print_response", watchdog),
            patch(
                "backend.app.services.background_dispatch.printer_manager.is_connected",
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                return_value=SimpleNamespace(state="FINISH", subtask_id="OLD_SUBTASK"),
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.start_print",
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.delete_file_async",
                new_callable=AsyncMock,
            ),
            patch(
                "backend.app.services.background_dispatch.with_ftp_retry",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.get_ftp_retry_settings",
                new_callable=AsyncMock,
                return_value=(False, 0, 0, 30.0),
            ),
            patch(
                "backend.app.services.background_dispatch.upload_file_async",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.ws_manager.broadcast",
                new_callable=AsyncMock,
            ),
            patch("backend.app.main.register_expected_print"),
            pytest.raises(RuntimeError, match="did not acknowledge print command"),
        ):
            await service._run_reprint_archive(job)

        # Watchdog received the captured pre-state and pre_subtask_id.
        watchdog.assert_awaited_once()
        kwargs = watchdog.await_args.kwargs
        args = watchdog.await_args.args
        assert "FINISH" in args  # pre_state
        assert kwargs["pre_subtask_id"] == "OLD_SUBTASK"

    @pytest.mark.asyncio
    async def test_succeeds_when_watchdog_returns_true(self, reprint_archive_mocks):
        """Happy path: watchdog confirms pickup; _run_reprint_archive returns
        without raising. Guards against the wiring accidentally raising on True."""
        from backend.app.services.background_dispatch import BackgroundDispatchService

        m = reprint_archive_mocks
        service = BackgroundDispatchService()
        job = _make_dispatch_job(kind="reprint_archive")

        with (
            patch("backend.app.services.background_dispatch.async_session", m["session_factory"]),
            patch(
                "backend.app.services.background_dispatch.ArchiveService",
                return_value=m["archive_service"],
            ),
            patch.object(
                BackgroundDispatchService,
                "_verify_print_response",
                AsyncMock(return_value=True),
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.is_connected",
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.get_status",
                return_value=SimpleNamespace(state="FINISH", subtask_id="OLD_SUBTASK"),
            ),
            patch(
                "backend.app.services.background_dispatch.printer_manager.start_print",
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.delete_file_async",
                new_callable=AsyncMock,
            ),
            patch(
                "backend.app.services.background_dispatch.with_ftp_retry",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.get_ftp_retry_settings",
                new_callable=AsyncMock,
                return_value=(False, 0, 0, 30.0),
            ),
            patch(
                "backend.app.services.background_dispatch.upload_file_async",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "backend.app.services.background_dispatch.ws_manager.broadcast",
                new_callable=AsyncMock,
            ),
            patch("backend.app.main.register_expected_print"),
        ):
            await service._run_reprint_archive(job)  # must not raise

        # Reprint flow does not touch the existing archive — no rollback expected.
        m["db"].rollback.assert_not_called()


class TestRunActiveJobMarksFailedOnRuntimeError:
    """End-to-end: a watchdog-driven RuntimeError must reach
    `_mark_job_finished(failed=True)` via the existing ``_run_active_job``
    catch-all, so the dispatch UI shows a real failure (not "Done")."""

    @pytest.mark.asyncio
    async def test_runtime_error_from_process_job_marks_failed_with_message(self):
        from backend.app.services.background_dispatch import BackgroundDispatchService

        service = BackgroundDispatchService()
        job = _make_dispatch_job()
        # Place the job into _active_jobs so _set_active_message has a target.
        service._active_jobs[job.id] = ActiveDispatchState(job=job, message="")

        failure_message = (
            "Printer did not acknowledge print command — state still FINISH. "
            "Check the printer for a pending error (HMS code, plate-clear prompt, "
            "SD card) and try again."
        )

        with (
            patch.object(
                BackgroundDispatchService,
                "_process_job",
                AsyncMock(side_effect=RuntimeError(failure_message)),
            ),
            patch.object(
                BackgroundDispatchService,
                "_mark_job_finished",
                new_callable=AsyncMock,
            ) as mark_finished,
        ):
            await service._run_active_job(job)

        mark_finished.assert_awaited_once()
        kwargs = mark_finished.await_args.kwargs
        assert kwargs["failed"] is True
        assert "did not acknowledge print command" in kwargs["message"]
