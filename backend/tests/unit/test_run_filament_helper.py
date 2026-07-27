"""Unit tests for the per-run filament helper (#1378, #1390).

The helper computes what value to write into PrintLogEntry.filament_used_grams
for a given print event — partial-aware so failed / cancelled / stopped prints
don't inflate stats with the full slicer estimate, and tracker-aware so
completed prints agree with the per-spool counter on the Inventory page.
"""

from types import SimpleNamespace

import backend.app.utils.threemf_tools as threemf_tools
from backend.app.main import _compute_run_filament_grams, _plate_scoped_run_estimate


class TestComputeRunFilamentGrams:
    def test_completed_no_tracker_returns_archive_estimate(self):
        # Completed print without inventory tracking: the slicer estimate is
        # the canonical "this print used X" value.
        assert _compute_run_filament_grams("completed", 100.0, 100, []) == 100.0

    def test_completed_prefers_tracked_over_estimate(self):
        # #1390: when inventory tracked the AMS weight delta, Stats should
        # reflect that — same source that drives "Total Consumed" on the
        # Inventory page. Two halves of the app must show the same number.
        assert _compute_run_filament_grams("completed", 100.0, 100, [{"weight_used": 96.5}]) == 96.5

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


class TestPlateScopedRunEstimate:
    """#2614: a plate dispatched from a multi-plate 3MF must log only that plate's
    filament/cost, not the archive's whole-file totals."""

    def _archive(self, **kw):
        return SimpleNamespace(
            id=1,
            plate_id=kw.get("plate_id", 3),
            filament_used_grams=kw.get("filament_used_grams", 12006.49),
            cost=kw.get("cost", 240.13),
            file_path=kw.get("file_path", "archive/1/heart.gcode.3mf"),
        )

    def _patch_plate_grams(self, monkeypatch, grams):
        monkeypatch.setattr(
            threemf_tools,
            "extract_plate_metadata_from_3mf",
            lambda path, plate_id: SimpleNamespace(filament_used_grams=grams),
        )

    def test_scopes_grams_and_scales_cost_to_plate(self, monkeypatch, tmp_path):
        f = tmp_path / "heart.gcode.3mf"
        f.write_bytes(b"stub")
        self._patch_plate_grams(monkeypatch, 350.0)
        grams, cost = _plate_scoped_run_estimate(self._archive(), f)
        assert grams == 350.0
        # cost scaled by the plate's share of the whole-file grams.
        assert cost == round(240.13 * (350.0 / 12006.49), 2)

    def test_no_plate_id_returns_whole_file_values(self, monkeypatch, tmp_path):
        f = tmp_path / "heart.gcode.3mf"
        f.write_bytes(b"stub")
        # Extractor must not even be consulted.
        self._patch_plate_grams(monkeypatch, 350.0)
        grams, cost = _plate_scoped_run_estimate(self._archive(plate_id=None), f)
        assert (grams, cost) == (12006.49, 240.13)

    def test_missing_file_returns_whole_file_values(self, monkeypatch):
        from pathlib import Path

        self._patch_plate_grams(monkeypatch, 350.0)
        grams, cost = _plate_scoped_run_estimate(self._archive(), Path("/nope/gone.3mf"))
        assert (grams, cost) == (12006.49, 240.13)

    def test_zero_plate_estimate_falls_back(self, monkeypatch, tmp_path):
        f = tmp_path / "heart.gcode.3mf"
        f.write_bytes(b"stub")
        self._patch_plate_grams(monkeypatch, 0.0)
        grams, cost = _plate_scoped_run_estimate(self._archive(), f)
        assert (grams, cost) == (12006.49, 240.13)

    def test_extractor_error_falls_back(self, monkeypatch, tmp_path):
        f = tmp_path / "heart.gcode.3mf"
        f.write_bytes(b"stub")

        def _boom(path, plate_id):
            raise ValueError("corrupt 3mf")

        monkeypatch.setattr(threemf_tools, "extract_plate_metadata_from_3mf", _boom)
        grams, cost = _plate_scoped_run_estimate(self._archive(), f)
        assert (grams, cost) == (12006.49, 240.13)

    def test_no_archive_cost_keeps_cost_none(self, monkeypatch, tmp_path):
        f = tmp_path / "heart.gcode.3mf"
        f.write_bytes(b"stub")
        self._patch_plate_grams(monkeypatch, 350.0)
        grams, cost = _plate_scoped_run_estimate(self._archive(cost=None), f)
        assert grams == 350.0
        assert cost is None
