"""Tests for the connected-edge reconciliation that recovers from missed
PRINT COMPLETE events (#1542 follow-up).

Background: the PRINT COMPLETE MQTT callback is purely reactive to a single
state transition (RUNNING → IDLE / FINISH / FAILED). When the printer
finishes during an MQTT disconnect window — typical on the A1 line with
unstable MQTT keepalives — Bambuddy never observes the transition. If a
smart plug then cuts power between completion and the next reconnect, the
firmware auto-replays whatever's still on the SD card and produces a ghost
print on next power-up. Reporter (#1542 second case) saw this hit 4 out of
4 of his A1s.

These tests cover:
  * `_is_active_archive_stale` — the pure decision function for whether an
    archive in `status="printing"` should be reconciled given the printer's
    current state.
  * `reconcile_stale_active_prints` — the orchestrator that queries the DB,
    runs the decision function, and synthesises `on_print_complete` for
    each stale archive.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import _is_active_archive_stale, _reconciled_end_time, _reconciled_outcome
from backend.app.services.bambu_mqtt import HMSError


def _state(
    state: str,
    *,
    subtask_id: str = "",
    subtask_name: str = "",
    connected: bool = True,
    progress: float = 0.0,
    layer_num: int = 0,
    hms_errors: list | None = None,
) -> SimpleNamespace:
    """Minimal PrinterState stub for the pure decision function."""
    return SimpleNamespace(
        state=state,
        subtask_id=subtask_id,
        subtask_name=subtask_name,
        connected=connected,
        raw_data={},
        progress=progress,
        layer_num=layer_num,
        hms_errors=hms_errors or [],
    )


def _archive(
    subtask_id: str | None = "ABC123",
    filename: str = "ghost.3mf",
    print_name: str = "ghost",
    *,
    started_at: datetime | None = None,
    print_time_seconds: int | None = None,
) -> SimpleNamespace:
    """Minimal PrintArchive stub — only the fields the decision function reads."""
    return SimpleNamespace(
        id=42,
        subtask_id=subtask_id,
        filename=filename,
        print_name=print_name,
        started_at=started_at,
        print_time_seconds=print_time_seconds,
        plate_id=None,
        file_path=None,
    )


class TestIsActiveArchiveStale:
    """Decision function — covers all three stale triggers + the
    intentionally-conservative no-op cases."""

    # Trigger 1: printer is in a terminal state.
    @pytest.mark.parametrize("terminal_state", ["IDLE", "FINISH", "FAILED", "idle", "finish", "failed"])
    def test_terminal_state_marks_stale(self, terminal_state):
        archive = _archive(subtask_id="ABC123")
        state = _state(terminal_state, subtask_id="ABC123", subtask_name="ghost")
        is_stale, reason = _is_active_archive_stale(archive, state)
        assert is_stale is True
        assert terminal_state.upper() in reason

    # Trigger 2: printer is running a different subtask_id.
    def test_subtask_id_changed_marks_stale(self):
        archive = _archive(subtask_id="OLD_ID")
        state = _state("RUNNING", subtask_id="NEW_ID", subtask_name="something")
        is_stale, reason = _is_active_archive_stale(archive, state)
        assert is_stale is True
        assert "subtask_id" in reason
        assert "OLD_ID" in reason
        assert "NEW_ID" in reason

    # Trigger 3: printer is running but doesn't know what it's running.
    def test_empty_subtask_name_marks_stale(self):
        archive = _archive(subtask_id="ABC123")
        state = _state("RUNNING", subtask_id="", subtask_name="")
        is_stale, reason = _is_active_archive_stale(archive, state)
        assert is_stale is True
        assert "empty" in reason.lower() or "subtask_name" in reason

    # Healthy case: same subtask_id, running.
    def test_matching_running_print_not_stale(self):
        archive = _archive(subtask_id="ABC123")
        state = _state("RUNNING", subtask_id="ABC123", subtask_name="ghost")
        is_stale, _ = _is_active_archive_stale(archive, state)
        assert is_stale is False

    # PAUSE is not a stale signal — the print is paused, not ended.
    def test_paused_print_with_matching_subtask_not_stale(self):
        archive = _archive(subtask_id="ABC123")
        state = _state("PAUSE", subtask_id="ABC123", subtask_name="ghost")
        is_stale, _ = _is_active_archive_stale(archive, state)
        assert is_stale is False

    # PREPARE / SLICING are not stale either — pre-print phases.
    @pytest.mark.parametrize("pre_running_state", ["PREPARE", "SLICING"])
    def test_pre_running_states_with_matching_subtask_not_stale(self, pre_running_state):
        archive = _archive(subtask_id="ABC123")
        state = _state(pre_running_state, subtask_id="ABC123", subtask_name="ghost")
        is_stale, _ = _is_active_archive_stale(archive, state)
        assert is_stale is False

    # Missing subtask_id on the archive side: don't have evidence either
    # way, fall through to the empty-subtask_name check.
    def test_archive_with_no_subtask_id_falls_to_subtask_name_check(self):
        archive = _archive(subtask_id=None)
        state = _state("RUNNING", subtask_id="ANYTHING", subtask_name="something")
        # Subtask_name is populated → not stale, no false positive.
        is_stale, _ = _is_active_archive_stale(archive, state)
        assert is_stale is False

    # Missing subtask_id on both sides: still triggers the empty-subtask_name
    # branch if the printer doesn't know what it's running.
    def test_both_subtask_ids_missing_running_with_empty_name_stale(self):
        archive = _archive(subtask_id=None)
        state = _state("RUNNING", subtask_id="", subtask_name="")
        is_stale, _ = _is_active_archive_stale(archive, state)
        assert is_stale is True

    # IDLE wins over PRINT-STATE checks — the terminal-state branch fires
    # first regardless of what the subtask fields look like.
    def test_idle_state_overrides_matching_subtask(self):
        archive = _archive(subtask_id="ABC123")
        state = _state("IDLE", subtask_id="ABC123", subtask_name="ghost")
        is_stale, reason = _is_active_archive_stale(archive, state)
        assert is_stale is True
        assert "IDLE" in reason

    # #1679: defensive pre-push guard. Even if reconcile gets called against
    # a PrinterState that's still on construction defaults (state="unknown"
    # / empty / None, subtask_name=""), the function must NOT report stale —
    # otherwise the reactive PRINT COMPLETE later creates a duplicate
    # archive and filament gets double-counted. The on_printer_status_change
    # caller is the primary fix (gates the reconcile spawn on real state),
    # but this guard is belt-and-braces for any future caller.
    @pytest.mark.parametrize("degenerate_state", ["unknown", "UNKNOWN", "Unknown", "", None])
    def test_pre_push_state_returns_not_stale_even_with_empty_subtask(self, degenerate_state):
        archive = _archive(subtask_id="ABC123")
        state = _state(degenerate_state, subtask_id="", subtask_name="")
        is_stale, _ = _is_active_archive_stale(archive, state)
        assert is_stale is False, (
            f"state.state={degenerate_state!r} means MQTT hasn't pushed real data yet; "
            "treating an in-flight archive as stale here causes the #1679 "
            "duplicate-archive + filament-double-count regression"
        )


class TestReconcileStaleActivePrints:
    """Orchestrator-level tests — mock the printer manager + DB session so
    we can drive the decision flow end-to-end without standing up real
    fixtures.

    These cover:
      * No printer status (disconnected) → no-op, no on_print_complete fired.
      * No active archives → no-op.
      * Stale archive → synthesised on_print_complete called with status
        ``"aborted"`` and the `_reconciled: True` marker so downstream code
        can distinguish synthetic from real completions.
      * Non-stale archive → on_print_complete NOT called (no false positive
        on a healthy in-flight print).
      * Exception inside on_print_complete must NOT block reconciliation
        for subsequent archives or crash the caller.
    """

    @pytest.mark.asyncio
    async def test_no_status_skips_reconciliation(self):
        from backend.app.main import reconcile_stale_active_prints

        with patch("backend.app.main.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = None
            count = await reconcile_stale_active_prints(printer_id=1)
        assert count == 0

    @pytest.mark.asyncio
    async def test_disconnected_status_skips_reconciliation(self):
        from backend.app.main import reconcile_stale_active_prints

        with patch("backend.app.main.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = _state("RUNNING", connected=False)
            count = await reconcile_stale_active_prints(printer_id=1)
        # Disconnected state would be making decisions against cached state —
        # the connected-edge handler in on_printer_status_change is the only
        # place that should drive reconciliation.
        assert count == 0

    @pytest.mark.asyncio
    async def test_no_active_archives_returns_zero(self):
        from backend.app.main import reconcile_stale_active_prints

        with patch("backend.app.main.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = _state("IDLE")
            with patch("backend.app.main.async_session") as mock_session:
                session_ctx = AsyncMock()
                session_ctx.scalars = AsyncMock(return_value=MagicMock(all=lambda: []))  # no open runs
                session_ctx.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))
                mock_session.return_value.__aenter__.return_value = session_ctx
                count = await reconcile_stale_active_prints(printer_id=1)
        assert count == 0

    @pytest.mark.asyncio
    async def test_stale_archive_synthesises_aborted_completion(self):
        from backend.app.main import reconcile_stale_active_prints

        stale = _archive(subtask_id="OLD_ID", filename="ghost.3mf", print_name="ghost")
        with patch("backend.app.main.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = _state("IDLE", subtask_id="", subtask_name="")
            with patch("backend.app.main.async_session") as mock_session:
                session_ctx = AsyncMock()
                session_ctx.scalars = AsyncMock(return_value=MagicMock(all=lambda: []))  # no open runs
                session_ctx.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [stale])))
                mock_session.return_value.__aenter__.return_value = session_ctx
                with patch("backend.app.main.on_print_complete", new=AsyncMock()) as mock_complete:
                    count = await reconcile_stale_active_prints(printer_id=1)
        assert count == 1
        mock_complete.assert_awaited_once()
        # Verify the synthesised payload shape.
        args, kwargs = mock_complete.call_args
        assert args[0] == 1
        payload = args[1]
        assert payload["status"] == "aborted"
        assert payload["filename"] == "ghost.3mf"
        assert payload["_reconciled"] is True

    @pytest.mark.asyncio
    async def test_non_stale_archive_does_not_synthesise(self):
        from backend.app.main import reconcile_stale_active_prints

        healthy = _archive(subtask_id="ABC123")
        with patch("backend.app.main.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = _state("RUNNING", subtask_id="ABC123", subtask_name="ghost")
            with patch("backend.app.main.async_session") as mock_session:
                session_ctx = AsyncMock()
                session_ctx.scalars = AsyncMock(return_value=MagicMock(all=lambda: []))  # no open runs
                session_ctx.execute = AsyncMock(
                    return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [healthy]))
                )
                mock_session.return_value.__aenter__.return_value = session_ctx
                with patch("backend.app.main.on_print_complete", new=AsyncMock()) as mock_complete:
                    count = await reconcile_stale_active_prints(printer_id=1)
        assert count == 0
        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_print_complete_failure_does_not_block_rest(self):
        """An exception during one archive's synthesis must not abort
        reconciliation for the other archives — and must not propagate to
        the caller (the connected-edge handler is a hot path)."""
        from backend.app.main import reconcile_stale_active_prints

        a1 = _archive(subtask_id="A", filename="a.3mf")
        a1.id = 1
        a2 = _archive(subtask_id="B", filename="b.3mf")
        a2.id = 2
        with patch("backend.app.main.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = _state("IDLE")
            with patch("backend.app.main.async_session") as mock_session:
                session_ctx = AsyncMock()
                session_ctx.scalars = AsyncMock(return_value=MagicMock(all=lambda: []))  # no open runs
                session_ctx.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [a1, a2])))
                mock_session.return_value.__aenter__.return_value = session_ctx
                # First call raises, second call must still happen.
                mock_complete = AsyncMock(side_effect=[RuntimeError("boom"), None])
                with patch("backend.app.main.on_print_complete", new=mock_complete):
                    count = await reconcile_stale_active_prints(printer_id=1)
        # Only the second archive is recorded as reconciled (first raised).
        assert count == 1
        assert mock_complete.await_count == 2


class TestReconciledOutcome:
    """#3310: only the printer's FINISH or FAILED for the archive's own job
    says how the print ended; anything else stays aborted."""

    @pytest.mark.parametrize(
        ("printer_state", "archive_id", "printer_id", "outcome"),
        [
            ("FINISH", "ABC123", "ABC123", "completed"),
            ("finish", "ABC123", "ABC123", "completed"),
            ("FAILED", "ABC123", "ABC123", "failed"),
            # Reported as a number by some firmware.
            ("FINISH", "123", 123, "completed"),
            ("IDLE", "ABC123", "ABC123", "aborted"),
            # A print started from the screen while Bambuddy was down.
            ("FINISH", "ABC123", "XYZ789", "aborted"),
            ("FAILED", "ABC123", "XYZ789", "aborted"),
            ("FINISH", "ABC123", "", "aborted"),
            ("FINISH", None, "", "aborted"),
            # "0" is every print started outside the cloud, not one job.
            ("FINISH", "0", "0", "aborted"),
        ],
    )
    def test_outcome(self, printer_state, archive_id, printer_id, outcome):
        archive = _archive(subtask_id=archive_id)
        state = _state(printer_state, subtask_id=printer_id, subtask_name="ghost")
        assert _reconciled_outcome(archive, state) == outcome


class TestReconciledEndTime:
    NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def test_start_plus_estimate(self):
        archive = _archive(started_at=datetime(2026, 10, 20, 6, 0), print_time_seconds=3 * 3600)
        assert _reconciled_end_time(archive, self.NOW) == datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)

    def test_never_later_than_now(self):
        archive = _archive(started_at=datetime(2026, 10, 20, 11, 0), print_time_seconds=5 * 3600)
        assert _reconciled_end_time(archive, self.NOW) == self.NOW

    @pytest.mark.parametrize(
        ("started_at", "estimate"),
        [(None, 3600), (datetime(2026, 10, 20, 6, 0), None), (datetime(2026, 10, 20, 6, 0), 0)],
    )
    def test_unknown_without_start_or_estimate(self, started_at, estimate):
        archive = _archive(started_at=started_at, print_time_seconds=estimate)
        assert _reconciled_end_time(archive, self.NOW) is None

    READ = "backend.app.utils.threemf_tools.extract_print_time_from_3mf"

    def _multi_plate(self, plate_id=None):
        archive = _archive(started_at=datetime(2026, 10, 20, 6, 0), print_time_seconds=5 * 3600)
        archive.plate_id, archive.file_path = plate_id, "archives/x/part.3mf"
        return archive

    def test_the_plate_s_own_estimate(self):
        """A multi-plate archive carries the whole file's estimate (#1593)."""
        with patch(self.READ, return_value=1800) as read:
            ended = _reconciled_end_time(self._multi_plate(plate_id=2), self.NOW)
        assert ended == datetime(2026, 10, 20, 6, 30, tzinfo=timezone.utc)
        assert read.call_args.args[1] == 2

    def test_the_run_s_plate_over_the_archive_s(self):
        """A reprint of plate 3 reuses an archive that still says plate 1."""
        with patch(self.READ, return_value=1800) as read:
            _reconciled_end_time(self._multi_plate(plate_id=1), self.NOW, plate_id=3)
        assert read.call_args.args[1] == 3

    def test_no_plate_on_a_single_plate_file(self):
        with patch(self.READ, return_value=5 * 3600):
            ended = _reconciled_end_time(self._multi_plate(), self.NOW)
        assert ended == datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)

    def test_no_plate_on_a_multi_plate_file_is_unknown(self):
        """The first plate's estimate differs from the sum: which plate printed?"""
        with patch(self.READ, return_value=1800):
            assert _reconciled_end_time(self._multi_plate(), self.NOW) is None

    def test_an_unreadable_file_is_unknown(self):
        with patch(self.READ, side_effect=OSError("gone")):
            assert _reconciled_end_time(self._multi_plate(plate_id=2), self.NOW) is None


class TestReconcilePayload:
    async def _payload(self, archive, state):
        from backend.app.main import reconcile_stale_active_prints

        with patch("backend.app.main.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = state
            with patch("backend.app.main.async_session") as mock_session:
                session_ctx = AsyncMock()
                session_ctx.scalars = AsyncMock(return_value=MagicMock(all=lambda: []))  # no open runs
                session_ctx.execute = AsyncMock(
                    return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [archive]))
                )
                mock_session.return_value.__aenter__.return_value = session_ctx
                with patch("backend.app.main.on_print_complete", new=AsyncMock()) as mock_complete:
                    assert await reconcile_stale_active_prints(printer_id=1) == 1
        return mock_complete.call_args.args[1]

    @pytest.mark.asyncio
    async def test_finished_while_bambuddy_restarted(self):
        started = datetime.now(timezone.utc) - timedelta(hours=10)
        archive = _archive(subtask_id="ABC123", started_at=started, print_time_seconds=2 * 3600)
        payload = await self._payload(
            archive, _state("FINISH", subtask_id="ABC123", subtask_name="ghost", progress=100, layer_num=250)
        )
        assert payload["status"] == "completed"
        assert payload["_reconciled"] is True
        assert payload["_reconciled_completed_at"] == started + timedelta(hours=2)
        assert (payload["last_progress"], payload["last_layer_num"]) == (100, 250)
        assert "hms_errors" not in payload

    @pytest.mark.asyncio
    async def test_failed_while_bambuddy_restarted(self):
        error = HMSError(code="0x4057", attr=0x0300_0000, module=0x03, severity=1, full_code="0300000000004057")
        payload = await self._payload(
            _archive(subtask_id="ABC123"),
            _state("FAILED", subtask_id="ABC123", subtask_name="ghost", progress=40, layer_num=80, hms_errors=[error]),
        )
        assert payload["status"] == "failed"
        assert (payload["last_progress"], payload["last_layer_num"]) == (40, 80)
        assert payload["hms_errors"] == [error.completion_payload()]
        assert "_reconciled_completed_at" not in payload

    @pytest.mark.asyncio
    async def test_another_job_s_progress_is_not_this_archive_s(self):
        archive = _archive(subtask_id="ABC123", started_at=datetime.now(timezone.utc), print_time_seconds=3600)
        payload = await self._payload(
            archive, _state("FINISH", subtask_id="XYZ789", subtask_name="other", progress=100, layer_num=250)
        )
        assert payload["status"] == "aborted"
        for key in ("last_progress", "last_layer_num", "hms_errors", "_reconciled_completed_at"):
            assert key not in payload


@pytest.mark.asyncio
async def test_the_queue_job_s_plate_reaches_the_estimate():
    from backend.app.main import reconcile_stale_active_prints

    archive = _archive(subtask_id="ABC123", started_at=datetime.now(timezone.utc), print_time_seconds=3600)
    archive.plate_id = 1
    archives = MagicMock(scalars=lambda: MagicMock(all=lambda: [archive]))
    plates = MagicMock(all=lambda: [(archive.id, 3)])
    with patch("backend.app.main.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = _state("FINISH", subtask_id="ABC123", subtask_name="ghost")
        with patch("backend.app.main.async_session") as mock_session:
            session_ctx = AsyncMock()
            session_ctx.scalars = AsyncMock(return_value=MagicMock(all=lambda: []))  # no open runs
            session_ctx.execute = AsyncMock(side_effect=[archives, plates])
            mock_session.return_value.__aenter__.return_value = session_ctx
            with (
                patch("backend.app.main._reconciled_end_time", return_value=None) as end_time,
                patch("backend.app.main.on_print_complete", new=AsyncMock()),
            ):
                await reconcile_stale_active_prints(printer_id=1)
    assert end_time.call_args.args[2] == 3
