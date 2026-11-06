"""A matched expected print takes its creator record with it (EP-2).

A queued print registers who started it under every name variant. The start
handler removed that record only when the print's archive was still there
and no start notification had gone out; the 2-hour expiry sweeps by the
expected-print entries, which the match had already removed. A leftover
record then named -- and emailed -- that user for an unrelated later print
of the same name on that printer.
"""

import pytest

from backend.app import main
from backend.tests.unit import test_plate_check_chamber_light as harness

pytestmark = pytest.mark.asyncio

# The harness's own autouse fixtures (dict clearing, light count) are not
# collected here, so take them explicitly.
_clear_dicts = harness._clear_dicts
_light_count = harness._light_count


async def test_a_print_whose_archive_is_gone_leaves_no_creator(_clear_dicts, _light_count):
    # The harness's session finds no archive: it was deleted mid-dispatch.
    main.register_expected_print(1, "Benchy.3mf", 999, created_by_id=7)
    assert main._expected_print_creators

    await harness._run_print_start([], check_plate_empty=harness._objects_on_plate())

    # The match took the expected print, and no creator record outlives the
    # entries the expiry sweeps by (a name variant the match did not try
    # keeps both, and the sweep takes them together).
    assert 999 not in {main._expected_prints.get(k) for k in [(1, "Benchy.3mf"), (1, "Benchy")]}
    orphaned = set(main._expected_print_creators) - set(main._expected_print_registered_at)
    assert not orphaned
