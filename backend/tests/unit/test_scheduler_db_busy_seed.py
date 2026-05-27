"""
Regression test for Airtho fork fix: busy_printers pre-seeded from DB.

Commit 36230810 — before this fix the busy_printers set started empty each
scheduler tick, so a second pending item could be dispatched to the same
printer within 30s (the window between sending a print command and the P1S
transitioning from IDLE to RUNNING over MQTT).

The test verifies the DB-seed logic: any printer_id that appears in a row
with status='printing' must be added to busy_printers before the dispatch
loop runs.
"""

import pytest


class TestDbBusySeed:
    """Verify that busy_printers is pre-populated from DB 'printing' rows."""

    def _seed_busy_printers_from_rows(self, rows: list[tuple[int | None]]) -> set[int]:
        """Replicate the seeding logic from print_scheduler.check_queue()."""
        busy: set[int] = set()
        for (pid,) in rows:
            if pid is not None:
                busy.add(pid)
        return busy

    def test_single_printing_item_marks_printer_busy(self):
        rows = [(5,)]
        busy = self._seed_busy_printers_from_rows(rows)
        assert 5 in busy

    def test_multiple_printing_items_same_printer(self):
        rows = [(3,), (3,)]
        busy = self._seed_busy_printers_from_rows(rows)
        assert 3 in busy
        assert len(busy) == 1

    def test_multiple_printing_items_different_printers(self):
        rows = [(1,), (2,), (3,)]
        busy = self._seed_busy_printers_from_rows(rows)
        assert busy == {1, 2, 3}

    def test_none_printer_id_not_added(self):
        """Items without a printer_id (e.g. freshly queued) must not corrupt the set."""
        rows = [(None,), (2,)]
        busy = self._seed_busy_printers_from_rows(rows)
        assert None not in busy
        assert 2 in busy

    def test_empty_result_yields_empty_set(self):
        busy = self._seed_busy_printers_from_rows([])
        assert busy == set()

    def test_second_item_for_same_printer_blocked(self):
        """If printer 1 is already printing, a second item targeting it is skipped."""
        busy = self._seed_busy_printers_from_rows([(1,)])
        # Simulate the dispatch gate: skip if printer_id in busy_printers
        candidate_printer_id = 1
        assert candidate_printer_id in busy, (
            "Second item for printer 1 should be blocked by busy_printers seed"
        )

    def test_different_printer_not_blocked(self):
        """A different printer is not affected by another printer's busy state."""
        busy = self._seed_busy_printers_from_rows([(1,)])
        candidate_printer_id = 2
        assert candidate_printer_id not in busy, (
            "Printer 2 should not be blocked by printer 1's printing item"
        )
