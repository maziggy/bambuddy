"""Tests for the injected-End-G-code flag the finish photo depends on (#2547).

The flag decides whether the finish photo comes from the camera (the print is
still on the plate) or from the in-print frame bank (a SwapMod snippet ejected
it — #1867). Getting it wrong in either direction ships the wrong photo, so the
two-step pending/adopt handoff exists to guarantee the flag can never outlive
the print it was recorded for.
"""

import pytest

from backend.app.services import print_dispatch_context


@pytest.fixture(autouse=True)
def _clean():
    for printer_id in (1, 2):
        print_dispatch_context.clear(printer_id)
    yield
    for printer_id in (1, 2):
        print_dispatch_context.clear(printer_id)


def test_unknown_printer_reports_no_injection():
    assert print_dispatch_context.end_gcode_injected(1) is False


def test_pending_flag_only_counts_once_the_print_starts():
    """Dispatch can fail between upload and start. Until the printer confirms a
    print running, the flag must not affect anything."""
    print_dispatch_context.mark_pending(1)

    assert print_dispatch_context.end_gcode_injected(1) is False

    assert print_dispatch_context.adopt(1) is True
    assert print_dispatch_context.end_gcode_injected(1) is True


def test_adopting_consumes_the_pending_flag():
    """A second print must not inherit the first print's snippet."""
    print_dispatch_context.mark_pending(1)
    print_dispatch_context.adopt(1)

    assert print_dispatch_context.adopt(1) is False
    assert print_dispatch_context.end_gcode_injected(1) is False


def test_a_print_we_did_not_dispatch_clears_the_previous_flag():
    """The failure this two-step design exists to prevent: a print started from
    the slicer or SD card right after a SwapMod job would otherwise inherit its
    flag and get a mid-print banked frame instead of its own finish photo."""
    print_dispatch_context.mark_pending(1)
    print_dispatch_context.adopt(1)
    assert print_dispatch_context.end_gcode_injected(1) is True

    # Next print start, with nothing pending — i.e. Bambuddy didn't send it.
    assert print_dispatch_context.adopt(1) is False
    assert print_dispatch_context.end_gcode_injected(1) is False


def test_printers_do_not_share_flags():
    print_dispatch_context.mark_pending(1)
    print_dispatch_context.adopt(1)

    assert print_dispatch_context.end_gcode_injected(1) is True
    assert print_dispatch_context.end_gcode_injected(2) is False


def test_clear_forgets_pending_and_active():
    print_dispatch_context.mark_pending(1)
    print_dispatch_context.adopt(1)
    print_dispatch_context.mark_pending(1)

    print_dispatch_context.clear(1)

    assert print_dispatch_context.end_gcode_injected(1) is False
    assert print_dispatch_context.adopt(1) is False
