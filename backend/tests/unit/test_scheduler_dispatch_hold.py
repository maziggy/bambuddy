"""Regression tests for the in-memory dispatch hold (#1157).

When the scheduler dispatches a print, it records a per-printer hold that
prevents a second dispatch onto the same printer until either the printer
transitions out of pre_state OR a hard timeout expires. This is defense in
depth alongside the DB ``busy_printers`` seed.

Why it exists: on the H2D Pro, ``project_file`` ack can take 80–210 s. During
that window users were getting 3 plates of the same multi-plate file
dispatched 30 s apart onto the same printer — the seed query was empirically
missing in-flight items even though the queue items were marked ``printing``
in the DB. The hold removes the dependency on DB-row visibility / completion-
callback timing for this guard.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.app.services.print_scheduler import PrintScheduler


def _status(state: str, subtask_id: str | None = None):
    return SimpleNamespace(state=state, subtask_id=subtask_id, gcode_file=None)


class TestDispatchHoldHoldsThePrinter:
    """A printer that just received a project_file is locked out of new
    dispatches until something releases it."""

    def test_held_immediately_after_mark(self):
        sched = PrintScheduler()
        get_status = MagicMock(return_value=_status("FINISH", "subtask-1"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="subtask-1")
            assert sched._printer_in_dispatch_hold(42) is True

    def test_unmarked_printer_not_held(self):
        sched = PrintScheduler()
        assert sched._printer_in_dispatch_hold(42) is False

    def test_state_unchanged_keeps_hold(self):
        """Printer still reporting pre_state with no subtask_id advance ⇒ held.

        This is the main scenario: H2D Pro at FINISH for ~80 s after
        ``project_file``; the scheduler must not double-dispatch into that
        window.
        """
        sched = PrintScheduler()
        get_status = MagicMock(return_value=_status("FINISH", "subtask-1"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="subtask-1")
            assert sched._printer_in_dispatch_hold(42) is True


class TestDispatchHoldReleases:
    """The hold must release once the printer has actually picked up the job,
    so the next pending item for this printer can dispatch normally."""

    def test_release_via_explicit_call(self):
        sched = PrintScheduler()
        sched._mark_printer_dispatched(42, "FINISH", "subtask-1")
        sched._release_dispatch_hold(42)
        assert 42 not in sched._dispatch_holds

    def test_release_is_idempotent(self):
        sched = PrintScheduler()
        sched._release_dispatch_hold(42)  # never marked
        sched._release_dispatch_hold(42)  # double-release
        assert 42 not in sched._dispatch_holds

    def test_state_transition_after_min_cooldown_releases(self):
        """If the printer transitions away from pre_state AND the minimum
        cooldown has elapsed, the hold drops on the next check."""
        sched = PrintScheduler()
        sched._dispatch_min_cooldown = 0.0  # Skip the cooldown floor for this test
        get_status = MagicMock(return_value=_status("PREPARE", "subtask-1"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="subtask-1")
            assert sched._printer_in_dispatch_hold(42) is False
            assert 42 not in sched._dispatch_holds

    def test_subtask_id_advance_releases(self):
        """H2D firmware can echo the new subtask_id back on push_status before
        flipping gcode_state — that's also a definitive 'job accepted' signal,
        same shape as the existing watchdog logic (#1078)."""
        sched = PrintScheduler()
        sched._dispatch_min_cooldown = 0.0
        get_status = MagicMock(return_value=_status("FINISH", "new-subtask-99"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="old-subtask-1")
            assert sched._printer_in_dispatch_hold(42) is False

    def test_transition_within_cooldown_still_holds(self):
        """Even after a state transition, hold for at least min_cooldown so a
        slow printer that briefly pulses through PREPARE→RUNNING→PREPARE
        doesn't open a window for double-dispatch."""
        sched = PrintScheduler()
        sched._dispatch_min_cooldown = 60.0
        get_status = MagicMock(return_value=_status("PREPARE", "subtask-1"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="subtask-1")
            # Cooldown not elapsed (just-marked) → still held even though
            # state already transitioned.
            assert sched._printer_in_dispatch_hold(42) is True


class TestDispatchHoldHardTimeout:
    """A lost MQTT session must not lock a printer out of the queue forever."""

    def test_hard_timeout_drops_hold(self):
        sched = PrintScheduler()
        sched._dispatch_max_hold = 0.001  # ~1 ms — instant expiry
        get_status = MagicMock(return_value=_status("FINISH", "subtask-1"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="subtask-1")
            import time

            time.sleep(0.005)
            assert sched._printer_in_dispatch_hold(42) is False
            assert 42 not in sched._dispatch_holds


class TestDispatchHoldFallbacks:
    """Edge cases around missing pre-dispatch data."""

    def test_no_pre_state_falls_back_to_time_only_hold(self):
        """If the printer was disconnected at dispatch time we have no
        pre_state to compare against. Hold for the minimum cooldown anyway —
        better than allowing an immediate second dispatch onto a printer we
        couldn't even read state from."""
        sched = PrintScheduler()
        sched._dispatch_min_cooldown = 60.0
        sched._mark_printer_dispatched(42, pre_state=None, pre_subtask_id=None)
        # Status doesn't matter — there's no pre_state to compare.
        get_status = MagicMock(return_value=_status("RUNNING", "anything"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            assert sched._printer_in_dispatch_hold(42) is True

    def test_no_pre_state_releases_after_cooldown(self):
        sched = PrintScheduler()
        sched._dispatch_min_cooldown = 0.001
        sched._mark_printer_dispatched(42, pre_state=None, pre_subtask_id=None)
        import time

        time.sleep(0.005)
        assert sched._printer_in_dispatch_hold(42) is False

    def test_status_unavailable_keeps_hold(self):
        """If the printer disconnects after dispatch we can't read state —
        keep the hold until the hard timeout. Don't release on missing data,
        because that would let a second dispatch land on a printer we have
        no visibility into."""
        sched = PrintScheduler()
        get_status = MagicMock(return_value=None)
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="subtask-1")
            assert sched._printer_in_dispatch_hold(42) is True


class TestPerPrinterIsolation:
    """Holds on one printer must not affect another."""

    def test_hold_does_not_leak_across_printers(self):
        sched = PrintScheduler()
        get_status = MagicMock(return_value=_status("FINISH", "subtask-1"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status):
            sched._mark_printer_dispatched(42, pre_state="FINISH", pre_subtask_id="subtask-1")
            # Printer 99 was never dispatched-to — must not be held.
            assert sched._printer_in_dispatch_hold(99) is False
            # Printer 42 still held.
            assert sched._printer_in_dispatch_hold(42) is True


class TestWatchdogIntegration:
    """The watchdog drops the dispatch hold on its happy paths so the next
    pending item can dispatch immediately. Without this, a successful print
    leaves the hold in place until the hard timeout — blocking valid follow-
    up dispatches."""

    def test_release_dispatch_hold_callable_from_module_level_scheduler(self):
        """The static watchdog calls ``scheduler._release_dispatch_hold(...)``
        on transition observed. Smoke-test that the public API is reachable
        and idempotent on the module-level instance the watchdog uses.
        """
        from backend.app.services.print_scheduler import scheduler

        scheduler._release_dispatch_hold(99999)  # not held — must not raise
        scheduler._mark_printer_dispatched(99999, "FINISH", "subtask-1")
        assert 99999 in scheduler._dispatch_holds
        scheduler._release_dispatch_hold(99999)
        assert 99999 not in scheduler._dispatch_holds
