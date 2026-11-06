"""Which drying cycles mark their spools as dried, and for how long (#2863)."""

import pytest

from backend.app.services.bambu_mqtt import DryingCycleEnd
from backend.app.services.spool_drying import drying_record


def _cycle(remaining: int, peak: int, *, start_seen: bool = True, temp: int | None = None, hours: int | None = None):
    return DryingCycleEnd(
        ams_id=0,
        remaining_minutes=remaining,
        peak_minutes=peak,
        start_seen=start_seen,
        target_temp=temp,
        target_hours=hours,
    )


def test_full_cycle_started_by_bambuddy():
    record = drying_record(_cycle(1, 480, temp=55, hours=8))
    assert record is not None
    assert record.temp == 55
    assert record.hours == 8.0


@pytest.mark.parametrize(
    ("remaining", "counts"),
    [
        (240, True),  # exactly half of 8 hours ran
        (241, False),  # one minute short of half
        (470, False),  # stopped ten minutes in
    ],
)
def test_half_the_length_must_have_run(remaining, counts):
    assert (drying_record(_cycle(remaining, 480, hours=8)) is not None) is counts


def test_hours_are_the_time_actually_spent():
    record = drying_record(_cycle(180, 480, temp=55, hours=8))
    assert record is not None
    assert record.hours == 5.0


def test_requested_length_wins_over_a_late_peak():
    """Bambuddy started the cycle but its first countdown push arrived late."""
    record = drying_record(_cycle(1, 300, hours=8))
    assert record is not None
    assert record.hours == 8.0


def test_cycle_started_on_the_printer_keeps_its_hours():
    """Watched from its first minute, so the peak is the real length."""
    record = drying_record(_cycle(0, 360))
    assert record is not None
    assert record.temp is None
    assert record.hours == 6.0


def test_unwatched_start_leaves_hours_unknown():
    """Bambuddy came up mid-cycle: the peak understates the length."""
    record = drying_record(_cycle(2, 200, start_seen=False))
    assert record is not None
    assert record.hours is None


def test_unwatched_cycle_that_ran_to_term_counts_even_with_one_sighting():
    assert drying_record(_cycle(3, 3, start_seen=False)) is not None


def test_unwatched_cycle_stopped_early_does_not_count():
    assert drying_record(_cycle(150, 200, start_seen=False)) is None
