"""Unit tests for the per-run filament helper (#1378).

The helper computes what value to write into PrintLogEntry.filament_used_grams
for a given print event — partial-aware so failed / cancelled / stopped prints
don't inflate stats with the full slicer estimate.
"""

from backend.app.main import _compute_run_filament_grams


class TestComputeRunFilamentGrams:
    def test_completed_returns_archive_estimate(self):
        # Completed print: the slicer estimate is approximately what was used.
        assert _compute_run_filament_grams("completed", 100.0, 100, []) == 100.0

    def test_completed_returns_estimate_even_when_tracked_differs(self):
        # When a print completes, the estimate is the canonical "this print used X"
        # value — the tracked spool delta might be lower (some slots untracked)
        # but the print is done, so the full estimate is the right answer.
        assert _compute_run_filament_grams("completed", 100.0, 100, [{"weight_used": 10}]) == 100.0

    def test_failed_uses_tracked_spool_delta(self):
        # Failed reprint at 10g actual: inventory tracked the spool delta.
        # The estimate was 100g; we want 10g recorded for stats.
        assert _compute_run_filament_grams("failed", 100.0, 10, [{"weight_used": 10.0}]) == 10.0

    def test_cancelled_uses_tracked_spool_delta(self):
        # Same logic for cancelled.
        assert _compute_run_filament_grams("cancelled", 100.0, 12, [{"weight_used": 8.5}]) == 8.5

    def test_stopped_uses_tracked_spool_delta(self):
        assert _compute_run_filament_grams("stopped", 100.0, 15, [{"weight_used": 12.0}]) == 12.0

    def test_failed_with_no_tracked_falls_back_to_progress_scale(self):
        # No inventory tracking: scale estimate by progress% (10% of 100g = 10g).
        assert _compute_run_filament_grams("failed", 100.0, 10, []) == 10.0

    def test_failed_with_no_tracked_and_no_progress_returns_none(self):
        # Nothing to infer from — return None rather than guess the estimate.
        assert _compute_run_filament_grams("failed", 100.0, 0, []) is None

    def test_failed_with_partial_progress_rounds_correctly(self):
        # 100g × 33% = 33.0g (rounded to 1 decimal)
        assert _compute_run_filament_grams("failed", 100.0, 33, []) == 33.0

    def test_failed_with_no_estimate_returns_none(self):
        # No estimate, no tracked usage → can't compute anything.
        assert _compute_run_filament_grams("failed", None, 50, []) is None

    def test_failed_with_no_estimate_but_tracked_uses_tracked(self):
        # Tracked spool delta is authoritative even without an estimate.
        assert _compute_run_filament_grams("failed", None, 50, [{"weight_used": 5.0}]) == 5.0

    def test_tracked_overrides_progress_scale_when_both_available(self):
        # If inventory says 8g but progress says 15g, trust inventory (it's measured).
        assert _compute_run_filament_grams("failed", 100.0, 15, [{"weight_used": 8.0}]) == 8.0

    def test_progress_above_100_clamps_to_full_estimate(self):
        # Defensive: progress overshoot doesn't multiply past the estimate.
        assert _compute_run_filament_grams("failed", 100.0, 150, []) == 100.0

    def test_multiple_tracked_slots_summed(self):
        # Multi-filament print, two slots tracked.
        usage = [{"weight_used": 5.0}, {"weight_used": 3.5}, {"weight_used": 1.0}]
        assert _compute_run_filament_grams("failed", 100.0, 20, usage) == 9.5

    def test_completed_with_none_estimate_returns_none(self):
        # Archive somehow has no estimate (rare; archive_print parsed nothing).
        assert _compute_run_filament_grams("completed", None, 100, []) is None
