"""A dispatch that never sends the print command must leave no expectation.

``register_expected_print`` has to run *before* the MQTT command, because the
printer can report the print before the send returns. So any path that
registers and then fails to send leaves Bambuddy expecting a print that will
never arrive: a cancel winning the #1853 CAS race, ``start_print()`` returning
False, or an exception in between — a PostgreSQL connection failure mid-dispatch
is the case that surfaced this (#2702 follow-up).

The two-hour TTL sweep does eventually evict such an entry, but two hours is far
longer than it takes someone to react to a failed dispatch by pressing print
again. That reprint would be folded into the *old* archive and inherit its
``ams_mapping`` and ``plate_id`` instead of creating a fresh one.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def expected_print_tables():
    """The module-level registries, emptied around each test."""
    from backend.app import main

    names = (
        "_expected_prints",
        "_expected_print_creators",
        "_expected_print_registered_at",
        "_print_ams_mappings",
        "_print_plate_ids",
    )
    saved = {n: dict(getattr(main, n)) for n in names}
    for n in names:
        getattr(main, n).clear()
    yield main
    for n in names:
        getattr(main, n).clear()
        getattr(main, n).update(saved[n])


# ---------------------------------------------------------------------------
# unregister_expected_print is the exact inverse of register_expected_print
# ---------------------------------------------------------------------------


def test_unregister_leaves_every_registry_as_it_found_them(expected_print_tables):
    """The strongest form: register then unregister is a round trip to empty."""
    main = expected_print_tables

    main.register_expected_print(1, "widget.3mf", 298, ams_mapping=[3, 6], created_by_id=7, plate_id=1)
    assert main._expected_prints, "nothing registered — the test proves nothing"

    main.unregister_expected_print(1, "widget.3mf", 298)

    assert main._expected_prints == {}
    assert main._expected_print_creators == {}
    assert main._expected_print_registered_at == {}
    assert main._print_ams_mappings == {}
    assert main._print_plate_ids == {}


def test_unregister_clears_the_filename_variants_too(expected_print_tables):
    """Registration stores the name three ways; a partial undo still matches."""
    main = expected_print_tables

    main.register_expected_print(1, "widget.3mf", 298)
    main.unregister_expected_print(1, "widget.3mf", 298)

    for key in ((1, "widget.3mf"), (1, "widget"), (1, "widget.gcode")):
        assert key not in main._expected_prints, f"{key} survived"


def test_unregister_does_not_touch_another_printers_expectation(expected_print_tables):
    main = expected_print_tables

    main.register_expected_print(1, "widget.3mf", 298)
    main.register_expected_print(2, "widget.3mf", 299)

    main.unregister_expected_print(1, "widget.3mf", 298)

    assert main._expected_prints[(2, "widget.3mf")] == 299


def test_archive_keyed_tables_survive_while_another_file_still_points_at_them(
    expected_print_tables,
):
    """Mirrors the TTL sweep's rule, which is the easy thing to get wrong.

    ``_print_ams_mappings`` and ``_print_plate_ids`` are keyed by archive, not
    by file. Two files can be registered against one archive, so dropping them
    on the first unregister would strip usage-tracking data from a print that is
    still expected.
    """
    main = expected_print_tables

    main.register_expected_print(1, "plate1.3mf", 298, ams_mapping=[3], plate_id=1)
    main.register_expected_print(1, "plate2.3mf", 298, ams_mapping=[3], plate_id=2)

    main.unregister_expected_print(1, "plate1.3mf", 298)

    assert main._print_ams_mappings.get(298) == [3]
    assert 298 in main._print_plate_ids


def test_unregistering_an_unknown_print_is_a_no_op(expected_print_tables):
    """Runs from a ``finally``, so it must tolerate having nothing to do."""
    main = expected_print_tables

    main.unregister_expected_print(99, "never-registered.3mf", 1234)

    assert main._expected_prints == {}


# ---------------------------------------------------------------------------
# The scheduler's rollback hook
# ---------------------------------------------------------------------------


def test_scheduler_rollback_undoes_a_recorded_registration(expected_print_tables):
    main = expected_print_tables
    from backend.app.services.print_scheduler import PrintScheduler

    sched = PrintScheduler()
    main.register_expected_print(1, "widget.3mf", 298, ams_mapping=[3, 6], plate_id=1)
    sched._unconfirmed_expected_print[597] = (1, "widget.3mf", 298)

    sched._rollback_unconfirmed_expected_print(597)

    assert main._expected_prints == {}
    assert sched._unconfirmed_expected_print == {}


def test_scheduler_rollback_is_a_no_op_after_a_confirmed_send(expected_print_tables):
    """A sent print's expectation must survive — the callback needs it."""
    main = expected_print_tables
    from backend.app.services.print_scheduler import PrintScheduler

    sched = PrintScheduler()
    main.register_expected_print(1, "widget.3mf", 298, ams_mapping=[3, 6])
    sched._unconfirmed_expected_print[597] = (1, "widget.3mf", 298)
    # What `_start_print` does once start_print() returns True.
    sched._unconfirmed_expected_print.pop(597, None)

    sched._rollback_unconfirmed_expected_print(597)

    assert main._expected_prints[(1, "widget.3mf")] == 298
    assert main._print_ams_mappings[298] == [3, 6]


def test_scheduler_rollback_never_raises(expected_print_tables, monkeypatch):
    """It runs in the ``finally`` of dispatch, usually with an exception already
    propagating — it must not replace it with one of its own."""
    from backend.app.services.print_scheduler import PrintScheduler

    sched = PrintScheduler()
    sched._unconfirmed_expected_print[597] = (1, "widget.3mf", 298)
    monkeypatch.setattr(
        expected_print_tables,
        "unregister_expected_print",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    sched._rollback_unconfirmed_expected_print(597)  # must not raise

    assert sched._unconfirmed_expected_print == {}, "entry must be dropped even on failure"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_dispatch_withdraws_the_expectation_when_start_print_raises(expected_print_tables):
    """End to end through `_dispatch_one`, on the reported failure.

    A database error inside `_start_print` must leave no expectation behind, must
    still release the claim, and must not be swallowed — the background-task
    runner logs it, and hiding it here would turn a loud failure into a silent
    one.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    main = expected_print_tables
    from backend.app.services.print_scheduler import PrintScheduler

    sched = PrintScheduler()

    async def fake_start_print(db, item):
        # What `_start_print` does before the point the real one died.
        main.register_expected_print(1, "widget.3mf", 298, ams_mapping=[3, 6], plate_id=1)
        sched._unconfirmed_expected_print[597] = (1, "widget.3mf", 298)
        raise RuntimeError("remaining connection slots are reserved for roles with the SUPERUSER attribute")

    db = MagicMock()
    db.get = AsyncMock(return_value=MagicMock(id=597))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.app.services.print_scheduler.async_session", return_value=ctx),
        patch.object(sched, "_claim_for_dispatch", AsyncMock(return_value=True)),
        patch.object(sched, "_start_print", side_effect=fake_start_print),
        patch.object(sched, "_clear_dispatch_claim", AsyncMock()) as clear,
        pytest.raises(RuntimeError),
    ):
        await sched._dispatch_one(597)

    assert main._expected_prints == {}, "expectation survived a dispatch that never sent a print"
    assert main._print_ams_mappings == {}
    assert main._print_plate_ids == {}
    assert sched._unconfirmed_expected_print == {}
    clear.assert_awaited_once_with(db, 597)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_dispatch_keeps_the_expectation_when_the_print_was_sent(expected_print_tables):
    """The mirror image: a confirmed send must survive dispatch teardown, or the
    print-complete callback would create a duplicate archive."""
    from unittest.mock import AsyncMock, MagicMock, patch

    main = expected_print_tables
    from backend.app.services.print_scheduler import PrintScheduler

    sched = PrintScheduler()

    async def fake_start_print(db, item):
        main.register_expected_print(1, "widget.3mf", 298, ams_mapping=[3, 6])
        sched._unconfirmed_expected_print[597] = (1, "widget.3mf", 298)
        sched._unconfirmed_expected_print.pop(597, None)  # start_print() returned True

    db = MagicMock()
    db.get = AsyncMock(return_value=MagicMock(id=597))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.app.services.print_scheduler.async_session", return_value=ctx),
        patch.object(sched, "_claim_for_dispatch", AsyncMock(return_value=True)),
        patch.object(sched, "_start_print", side_effect=fake_start_print),
        patch.object(sched, "_clear_dispatch_claim", AsyncMock()),
    ):
        await sched._dispatch_one(597)

    assert main._expected_prints[(1, "widget.3mf")] == 298
    assert main._print_ams_mappings[298] == [3, 6]


def test_rollback_entries_are_per_item(expected_print_tables):
    """Two dispatches in flight must not roll back each other's registration."""
    main = expected_print_tables
    from backend.app.services.print_scheduler import PrintScheduler

    sched = PrintScheduler()
    main.register_expected_print(1, "a.3mf", 1)
    main.register_expected_print(2, "b.3mf", 2)
    sched._unconfirmed_expected_print[10] = (1, "a.3mf", 1)
    sched._unconfirmed_expected_print[11] = (2, "b.3mf", 2)

    sched._rollback_unconfirmed_expected_print(10)

    assert (1, "a.3mf") not in main._expected_prints
    assert main._expected_prints[(2, "b.3mf")] == 2
