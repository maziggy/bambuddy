"""Cross-model queue items — one job, several sliced files (#671).

The reporter has an H2S and an H2C and does not care which one runs the job.
He slices it twice; both slices become variants of a single queue item, and the
scheduler takes the first whose model has an idle printer.

The design constraint that shapes everything here: the many-to-many must never
escape the selection loop. Once a candidate wins, its file and settings are
folded onto the queue row, so the upload, archive creation, print history and
reprint paths keep seeing an ordinary single-file item. These tests assert both
halves — that the right candidate is picked, and that the row afterwards looks
like it was queued for that file all along.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import (
    PrintScheduler,
    _candidate_model_label,
    _candidates_for,
    _collapse_waiting_reasons,
)

# ---------------------------------------------------------------------------
# Candidate ordering — pure
# ---------------------------------------------------------------------------


def _fake_variant(*, vid, position, model, attempts=0, trashed=False, file_missing=False):
    return SimpleNamespace(
        id=vid,
        position=position,
        target_model=model,
        attempt_count=attempts,
        library_file=None
        if file_missing
        else SimpleNamespace(
            file_metadata={"sliced_for_model": model},
            deleted_at="2026-01-01" if trashed else None,
        ),
        required_filament_types=None,
        filament_overrides=None,
    )


def _fake_item(variants):
    return SimpleNamespace(
        variants=variants,
        target_model=None,
        archive=None,
        archive_id=None,
        library_file=None,
        library_file_id=None,
        required_filament_types=None,
        filament_overrides=None,
    )


def test_no_variants_yields_the_items_own_columns():
    """The pre-#671 path must be provably unchanged: one candidate, built from
    the item itself."""
    item = SimpleNamespace(
        variants=[],
        target_model="H2D",
        archive=None,
        archive_id=None,
        library_file_id=7,
        library_file=SimpleNamespace(file_metadata={"sliced_for_model": "H2D"}),
        required_filament_types='["PLA"]',
        filament_overrides=None,
    )
    candidates = _candidates_for(item)
    assert len(candidates) == 1
    assert candidates[0].target_model == "H2D"
    assert candidates[0].sliced_for == "H2D"
    assert candidates[0].required_filament_types == '["PLA"]'
    assert candidates[0].variant is None


def test_variants_come_back_in_user_priority_order():
    item = _fake_item(
        [
            _fake_variant(vid=2, position=1, model="H2C"),
            _fake_variant(vid=1, position=0, model="H2S"),
        ]
    )
    assert [c.target_model for c in _candidates_for(item)] == ["H2S", "H2C"]


def test_least_attempted_candidate_is_tried_first():
    """A printer that accepts the file and never starts must not eat the item's
    whole retry budget — the alternative gets the next lap."""
    item = _fake_item(
        [
            _fake_variant(vid=1, position=0, model="H2S", attempts=1),
            _fake_variant(vid=2, position=1, model="H2C", attempts=0),
        ]
    )
    assert [c.target_model for c in _candidates_for(item)] == ["H2C", "H2S"]


def test_trashed_candidate_is_skipped():
    """Library deletes are soft: the row survives with deleted_at set, which no
    foreign key can express. Dispatching a file the user put in the bin would be
    a genuine surprise."""
    item = _fake_item(
        [
            _fake_variant(vid=1, position=0, model="H2S", trashed=True),
            _fake_variant(vid=2, position=1, model="H2C"),
        ]
    )
    assert [c.target_model for c in _candidates_for(item)] == ["H2C"]


def test_orphaned_candidate_is_skipped():
    """SQLite runs with PRAGMA foreign_keys off, so a hard delete can leave a
    candidate row pointing at nothing."""
    item = _fake_item(
        [
            _fake_variant(vid=1, position=0, model="H2S", file_missing=True),
            _fake_variant(vid=2, position=1, model="H2C"),
        ]
    )
    assert [c.target_model for c in _candidates_for(item)] == ["H2C"]


def test_item_with_no_usable_candidates_yields_none():
    item = _fake_item([_fake_variant(vid=1, position=0, model="H2S", trashed=True)])
    assert _candidates_for(item) == []


def test_equal_attempts_fall_back_to_priority():
    """Once every candidate has failed equally often they cycle in the user's
    order, so the item still reaches its DISPATCH_MAX_ATTEMPTS ceiling."""
    item = _fake_item(
        [
            _fake_variant(vid=1, position=0, model="H2S", attempts=2),
            _fake_variant(vid=2, position=1, model="H2C", attempts=2),
        ]
    )
    assert [c.target_model for c in _candidates_for(item)] == ["H2S", "H2C"]


# ---------------------------------------------------------------------------
# Waiting reasons — pure
# ---------------------------------------------------------------------------


def test_single_candidate_reason_is_unprefixed():
    """One candidate means the card already shows the model; prefixing it would
    just be noise."""
    assert _collapse_waiting_reasons([("H2D", "Busy: H2D-1 (Printing)")]) == "Busy: H2D-1 (Printing)"


def test_identical_reasons_collapse_to_one_clause():
    collapsed = _collapse_waiting_reasons([("H2S", "Busy: shared-1 (Printing)"), ("H2C", "Busy: shared-1 (Printing)")])
    assert collapsed == "Busy: shared-1 (Printing)"


def test_all_busy_stays_busy_only_so_no_notification_fires():
    """Two models busy on differently-named printers still has to read as
    busy-only. Labelling the clauses would make every pass over a cross-model
    item look like it needs the user, when it just needs a printer to finish."""
    scheduler = PrintScheduler()
    collapsed = _collapse_waiting_reasons([("H2S", "Busy: H2S-1 (Printing)"), ("H2C", "Busy: H2C-1 (Printing)")])
    assert collapsed == "Busy: H2S-1 (Printing) | Busy: H2C-1 (Printing)"
    assert scheduler._is_busy_only(collapsed)


def test_differing_reasons_are_labelled_by_model():
    collapsed = _collapse_waiting_reasons([("H2S", "No PETG loaded"), ("H2C", "Busy: H2C-1 (Printing)")])
    assert collapsed == "H2S: No PETG loaded; H2C: Busy: H2C-1 (Printing)"
    assert not PrintScheduler._is_busy_only(collapsed), "a real blocker must still notify"


def test_empty_reasons_are_dropped():
    assert _collapse_waiting_reasons([("H2S", "")]) is None
    assert _collapse_waiting_reasons([]) is None


def test_model_label_names_every_candidate():
    item = _fake_item(
        [
            _fake_variant(vid=1, position=0, model="H2S"),
            _fake_variant(vid=2, position=1, model="H2C"),
        ]
    )
    assert _candidate_model_label(_candidates_for(item)) == "H2S or H2C"


# ---------------------------------------------------------------------------
# Scheduler behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
async def queue_db():
    """In-memory DB with one H2S and one H2C."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add_all(
            [
                Printer(
                    id=1,
                    name="H2S-1",
                    serial_number="H2S0001",
                    ip_address="10.0.0.1",
                    access_code="x",
                    model="H2S",
                    is_active=True,
                ),
                Printer(
                    id=2,
                    name="H2C-1",
                    serial_number="H2C0001",
                    ip_address="10.0.0.2",
                    access_code="x",
                    model="H2C",
                    is_active=True,
                ),
            ]
        )
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _add_variant_item(ctx, specs):
    """Seed one pending queue item with a variant per (model, overrides) spec."""
    async with ctx.session_maker() as db:
        item = PrintQueueItem(
            status="pending",
            position=1,
            target_model=specs[0]["model"],
        )
        db.add(item)
        await db.flush()

        for position, spec in enumerate(specs):
            lib = LibraryFile(
                filename=f"job_{spec['model']}.gcode.3mf",
                file_path=f"/library/job_{spec['model']}.gcode.3mf",
                file_size=10,
                file_type="gcode.3mf",
                file_metadata={"sliced_for_model": spec.get("sliced_for", spec["model"])},
            )
            db.add(lib)
            await db.flush()
            db.add(
                PrintQueueVariant(
                    queue_item_id=item.id,
                    position=position,
                    library_file_id=lib.id,
                    target_model=spec["model"],
                    plate_id=spec.get("plate_id"),
                    ams_mapping=spec.get("ams_mapping"),
                    nozzle_mapping=spec.get("nozzle_mapping"),
                    print_time_seconds=spec.get("print_time_seconds"),
                    attempt_count=spec.get("attempts", 0),
                )
            )
        await db.commit()
        return item.id


async def _run_check_queue(ctx, scheduler, finder, waiting_notification=None):
    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            waiting_notification or AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
            AsyncMock(),
        ),
        patch.object(scheduler, "_find_idle_printer_for_model", finder),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        # Selection is what's under test — keep AMS recomputation and the
        # filament-deficit probe out of the way, and never actually dispatch.
        # None is the mapping-resolved answer; a bare AsyncMock returns a truthy
        # sentinel, which the unmappable guard (#2771) reads as "this job can
        # never print" and fails the item on.
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_launch_uploads", MagicMock()),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await scheduler.check_queue()


async def _get_item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


def _finder_for(available: dict[str, int]):
    """Matcher that offers a printer only for the listed models."""

    async def _find(db, model, exclude_ids, *args, **kwargs):
        if model in available:
            return available[model], None
        return None, f"No idle {model} printer"

    return AsyncMock(side_effect=_find)


@pytest.mark.asyncio
async def test_first_matching_variant_wins_and_is_folded_onto_the_row(queue_db):
    """The H2C is free, the H2S is not — the item runs the H2C slice, and every
    downstream consumer sees a plain single-file item pointing at it."""
    item_id = await _add_variant_item(
        queue_db,
        [
            {"model": "H2S", "plate_id": 1, "ams_mapping": "[1]", "print_time_seconds": 900},
            {
                "model": "H2C",
                "plate_id": 3,
                "ams_mapping": "[4, 5]",
                "nozzle_mapping": "[0, 1]",
                "print_time_seconds": 1200,
            },
        ],
    )
    scheduler = PrintScheduler()

    await _run_check_queue(queue_db, scheduler, _finder_for({"H2C": 2}))

    item = await _get_item(queue_db, item_id)
    assert item.printer_id == 2, "assigned to the H2C"
    assert item.target_model == "H2C"
    assert item.plate_id == 3
    assert item.ams_mapping == "[4, 5]"
    assert item.nozzle_mapping == "[0, 1]"
    assert item.print_time_seconds == 1200, "the estimate now describes what will actually run"
    assert item.waiting_reason is None
    assert item.archive_id is None

    async with queue_db.session_maker() as db:
        chosen = (
            await db.execute(select(PrintQueueVariant).where(PrintQueueVariant.target_model == "H2C"))
        ).scalar_one()
        assert item.library_file_id == chosen.library_file_id


@pytest.mark.asyncio
async def test_priority_order_decides_when_both_are_free(queue_db):
    """Both printers idle in the same pass: the user's first choice runs, so the
    outcome is reproducible rather than whichever match came back first."""
    item_id = await _add_variant_item(queue_db, [{"model": "H2S"}, {"model": "H2C"}])
    scheduler = PrintScheduler()

    await _run_check_queue(queue_db, scheduler, _finder_for({"H2S": 1, "H2C": 2}))

    item = await _get_item(queue_db, item_id)
    assert item.printer_id == 1
    assert item.target_model == "H2S"


@pytest.mark.asyncio
async def test_cross_model_gate_is_applied_per_candidate(queue_db):
    """A variant whose file disagrees with its own model is skipped, and the
    other one still runs — the gate must not condemn the whole item."""
    item_id = await _add_variant_item(
        queue_db,
        [
            {"model": "H2S", "sliced_for": "X1C"},
            {"model": "H2C"},
        ],
    )
    scheduler = PrintScheduler()
    finder = _finder_for({"H2S": 1, "H2C": 2})

    await _run_check_queue(queue_db, scheduler, finder)

    assert [c.args[1] for c in finder.await_args_list] == ["H2C"], "the mismatched variant never reaches the matcher"
    item = await _get_item(queue_db, item_id)
    assert item.printer_id == 2
    assert item.target_model == "H2C"


@pytest.mark.asyncio
async def test_no_match_reports_every_model_it_tried(queue_db):
    """Nothing is free: the user must be able to tell which machines were
    considered, not just that "a printer" was unavailable."""
    item_id = await _add_variant_item(queue_db, [{"model": "H2S"}, {"model": "H2C"}])
    scheduler = PrintScheduler()
    waiting = AsyncMock()

    await _run_check_queue(queue_db, scheduler, _finder_for({}), waiting)

    item = await _get_item(queue_db, item_id)
    assert item.printer_id is None
    assert item.status == "pending"
    assert "H2S: No idle H2S printer" in item.waiting_reason
    assert "H2C: No idle H2C printer" in item.waiting_reason
    assert waiting.await_args.kwargs["target_model"] == "H2S or H2C"
    # The item holds no file of its own yet — the alert still has to name the job.
    assert waiting.await_args.kwargs["job_name"] == "job_H2S"


@pytest.mark.asyncio
async def test_item_with_no_files_left_is_held_with_an_actionable_reason(queue_db):
    """Deleting a library file takes its variant with it. An item stripped of
    every candidate used to sail into dispatch and die there on "No archive_id
    or library_file_id"; hold it where the user can see why."""
    async with queue_db.session_maker() as db:
        db.add(PrintQueueItem(status="pending", position=1, target_model="H2S"))
        await db.commit()
    scheduler = PrintScheduler()
    finder = _finder_for({"H2S": 1})

    await _run_check_queue(queue_db, scheduler, finder)

    finder.assert_not_awaited()
    async with queue_db.session_maker() as db:
        item = (await db.execute(select(PrintQueueItem))).scalar_one()
    assert item.status == "pending"
    assert item.printer_id is None
    assert "has been deleted" in item.waiting_reason


@pytest.mark.asyncio
async def test_plain_model_based_item_is_untouched(queue_db):
    """Regression guard: an item with no variants takes exactly the path it took
    before variants existed."""
    async with queue_db.session_maker() as db:
        lib = LibraryFile(
            filename="job.gcode.3mf",
            file_path="/library/job.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"sliced_for_model": "H2S"},
        )
        db.add(lib)
        await db.flush()
        db.add(
            PrintQueueItem(
                status="pending",
                position=1,
                target_model="H2S",
                library_file_id=lib.id,
                plate_id=2,
            )
        )
        await db.commit()
    scheduler = PrintScheduler()

    await _run_check_queue(queue_db, scheduler, _finder_for({"H2S": 1}))

    async with queue_db.session_maker() as db:
        item = (await db.execute(select(PrintQueueItem))).scalar_one()
    assert item.printer_id == 1
    assert item.target_model == "H2S"
    assert item.plate_id == 2, "nothing overwrote the item's own settings"


@pytest.mark.asyncio
async def test_failed_candidate_steps_aside_for_the_alternative(queue_db):
    """The H2S burned an attempt on the last lap. Both are free now — the H2C
    goes first, which is the entire point of queueing an alternative."""
    item_id = await _add_variant_item(
        queue_db,
        [
            {"model": "H2S", "attempts": 1},
            {"model": "H2C", "attempts": 0},
        ],
    )
    scheduler = PrintScheduler()

    await _run_check_queue(queue_db, scheduler, _finder_for({"H2S": 1, "H2C": 2}))

    item = await _get_item(queue_db, item_id)
    assert item.printer_id == 2
    assert item.target_model == "H2C"
