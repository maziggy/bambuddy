"""A reprint is charged at the spools it prints with (#3580).

The archive keeps its first run's cost (#1378), and billing charged every
later print of it from that figure: a reprint on other or re-priced spools
was billed at the old price. Each run now carries what it was priced at when
it started (``PrintLogEntry.planned_cost``) and is charged from that; a run
without one is charged from the archive's cost, as before.
"""

from __future__ import annotations

import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.core.config import settings as app_settings
from backend.app.main import register_expected_print
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, RUN_OPEN, PrintLogEntry
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.archive_cost_estimate import schedule_archive_cost_estimate
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    _clear_print_state,
    _drive_print_start,
)
from backend.tests.integration.test_run_lifecycle import (  # noqa: F401
    FILE,
    SUBTASK,
    _charges,
    _clean,
    _complete,
    _run,
    farm,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _reprint(
    test_engine,
    db_session,
    farm,
    *,
    planned_cost: float | None,
    planned_grams: float | None = None,
    status: str = "completed",
    extra=None,
):
    """A print of the archive started at the printer, to its end. Its start
    priced the run at ``planned_cost`` (None: it could not).

    The start's own estimate is a background task reading a 3MF the test has
    none of; what it writes on the run is written here in its place."""
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1, cost_center_id=farm.center)
    mocks = await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        extra_patches=[patch("backend.app.main._schedule_archive_cost_estimate")],
    )
    assert mocks["_schedule_archive_cost_estimate"].call_args.args[:2] == (farm.printer, farm.archive)
    db_session.expire_all()
    run = await db_session.scalar(
        select(PrintLogEntry)
        .where(PrintLogEntry.archive_id == farm.archive, PrintLogEntry.status == RUN_OPEN)
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )
    assert run is not None, "the print's start opens its run"
    run.planned_cost = planned_cost
    run.planned_grams = planned_grams
    await db_session.commit()
    await _complete(test_engine, farm.printer, status, name=SUBTASK, extra=extra)


async def test_a_reprint_is_charged_what_its_own_run_was_priced_at(test_engine, db_session, farm):
    """First printed at 5.00; printed again on a spool that prices it at 8.40."""
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")

    await _reprint(test_engine, db_session, farm, planned_cost=8.4)

    assert [c.amount for c in await _charges(db_session)] == [-5.0, -8.4]
    archive = await db_session.get(PrintArchive, farm.archive)
    assert archive.cost == 5.0, "the card keeps its first run's cost (#1378)"


async def test_a_reprint_is_logged_at_what_it_was_charged(test_engine, db_session, farm):
    """The Print Log and Statistics read the run's cost. With nothing measured
    it was the archive's, the first run's 5.00, for a reprint charged 8.40."""
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")

    await _reprint(test_engine, db_session, farm, planned_cost=8.4)

    db_session.expire_all()
    runs = (
        await db_session.scalars(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == farm.archive).order_by(PrintLogEntry.id)
        )
    ).all()
    assert [run.cost for run in runs] == [5.0, 8.4]


async def test_a_first_print_is_logged_at_what_it_was_charged(test_engine, db_session, farm):
    """A first print is charged from its run's price too. With nothing
    measured it logged the archive's cost, 5.00, for a print charged 8.40
    (in Spoolman mode, one plate of a multi-plate file: the card's cost
    scaled to the plate)."""
    await _reprint(test_engine, db_session, farm, planned_cost=8.4)

    assert [c.amount for c in await _charges(db_session)] == [-8.4]
    db_session.expire_all()
    run = await db_session.scalar(select(PrintLogEntry).where(PrintLogEntry.archive_id == farm.archive))
    assert run.cost == 8.4
    archive = await db_session.get(PrintArchive, farm.archive)
    assert archive.cost == 5.0, "the card is not the run's to write"


async def test_a_reprint_nothing_priced_is_charged_the_archives_cost(test_engine, db_session, farm):
    """No mapping, no estimate: the archive's cost, as before."""
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")

    await _reprint(test_engine, db_session, farm, planned_cost=None)

    assert [c.amount for c in await _charges(db_session)] == [-5.0, -5.0]


async def test_a_failed_reprint_is_charged_its_share_of_its_own_price(test_engine, db_session, farm):
    """The proportional charge of a run that did not finish starts from the
    run's price too: half the filament of a 8.40 print is 4.20."""
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.filament_used_grams = 100.0
    await db_session.commit()
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")

    await _reprint(test_engine, db_session, farm, planned_cost=8.4, status="failed", extra={"last_progress": 50})

    reprint = (await _charges(db_session))[-1]
    assert reprint.amount == -4.2
    assert "[failed: 50.0% filament (50.0g/100.0g)]" in reprint.description


async def test_a_failed_reprint_pays_its_share_of_the_grams_it_was_priced_for(test_engine, db_session, farm):
    """The price and its grams are the run's, kept together: 8.40 was for
    40 g, whatever the archive's figure reads by the end."""
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.filament_used_grams = 100.0
    await db_session.commit()
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")

    await _reprint(
        test_engine,
        db_session,
        farm,
        planned_cost=8.4,
        planned_grams=40.0,
        status="failed",
        extra={"last_progress": 50},
    )

    reprint = (await _charges(db_session))[-1]
    assert reprint.amount == -4.2
    assert "[failed: 50.0% filament (20.0g/40.0g)]" in reprint.description


# --- The start prices the run it opened, and the end charges that -----------
#
# Nothing of the estimate is stubbed below: a real start opens the run and
# schedules the estimate, the estimate reads a real two-plate 3MF and the
# spool in the tray, and the real end charges what it wrote.

TWO_PLATES = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <filament id="1" type="PLA" color="#FFFFFF" used_m="27" used_g="80"/>
  </plate>
  <plate>
    <metadata key="index" value="2"/>
    <filament id="1" type="PLA" color="#FFFFFF" used_m="7" used_g="20"/>
  </plate>
</config>
"""
TRAYS = {"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FFFFFFFF"}]}], "vt_tray": []}


@pytest.fixture
async def two_plates(db_session, farm, tmp_path, monkeypatch):
    """The farm's archive is a file of two plates, 80 g and 20 g, whose plate 2
    is the one printed; the tray it prints from holds a spool at 30 per kg."""
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    archive = await db_session.get(PrintArchive, farm.archive)
    path = tmp_path / archive.file_path
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", TWO_PLATES)
    archive.filament_used_grams = 100.0
    archive.plate_id = 2  # read from the G-code path the printer reported
    spool = Spool(material="PLA", cost_per_kg=30)
    db_session.add_all([spool, Settings(key="default_filament_cost", value="25")])
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=farm.printer, ams_id=0, tray_id=0))
    await db_session.commit()
    return farm


async def _start_and_price(test_engine, db_session, farm) -> PrintLogEntry:
    """A print of the archive started at the printer, which names no plate,
    and the start's own estimate run to its end. Returns the open run.

    The estimate is a background task of the start; it is held back until the
    start is through and then run for real, with what the start passed it, so
    that it does not write while the start still does (the SQLite test
    database is one connection). What it finds is what the start committed
    before scheduling it, which ``order`` pins."""
    register_expected_print(farm.printer, FILE, farm.archive, ams_mapping=[0], cost_center_id=farm.center)
    order: list[str] = []
    scheduled: list[tuple] = []
    open_run_for = main_module._open_run_for

    async def opens_the_run(*args, **kwargs):
        await open_run_for(*args, **kwargs)
        order.append("run opened")

    def schedules_the_estimate(*args, **kwargs):
        order.append("estimate scheduled")
        scheduled.append((args, kwargs))

    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        state_fields={"raw_data": TRAYS},
        extra_patches=[
            patch("backend.app.main._open_run_for", side_effect=opens_the_run),
            patch("backend.app.main._schedule_archive_cost_estimate", side_effect=schedules_the_estimate),
        ],
    )
    # The estimate prices the run open on the printer: scheduled before the
    # run is opened and committed, it finds none and the run is never priced.
    assert order == ["run opened", "estimate scheduled"]
    (args, kwargs) = scheduled[0]
    assert args[:2] == (farm.printer, farm.archive)
    assert kwargs["plate_id"] is None, "a start at the printer names no plate"
    kwargs["session_factory"] = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    await schedule_archive_cost_estimate(*args, **kwargs)

    db_session.expire_all()
    run = await db_session.scalar(
        select(PrintLogEntry)
        .where(PrintLogEntry.archive_id == farm.archive, PrintLogEntry.status == RUN_OPEN)
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )
    assert run is not None
    return run


async def test_a_print_of_one_plate_started_at_the_printer_is_charged_that_plate(test_engine, db_session, two_plates):
    """The start does not know the plate, the archive does. The run was priced
    at the whole file (100 g: 3.00) and charged that for its 20 g."""
    run = await _start_and_price(test_engine, db_session, two_plates)
    assert (run.plate_id, run.planned_grams, run.planned_cost) == (2, 20.0, 0.6)

    await _complete(test_engine, two_plates.printer, "completed", name=SUBTASK)

    assert [c.amount for c in await _charges(db_session)] == [-0.6]


async def test_a_print_only_on_free_spools_is_charged_nothing(test_engine, db_session, two_plates):
    """The spool in the tray is priced 0, which is free: the run is priced 0
    and charged that. It got no price and was charged from the archive's
    first-run cost."""
    spool = await db_session.scalar(select(Spool))
    spool.cost_per_kg = 0
    await db_session.commit()

    run = await _start_and_price(test_engine, db_session, two_plates)
    assert (run.planned_grams, run.planned_cost) == (20.0, 0.0)

    await _complete(test_engine, two_plates.printer, "completed", name=SUBTASK)

    assert await _charges(db_session) == []
    # Logged as charged: it logged the archive's cost for the plate, 1.00.
    db_session.expire_all()
    logged = await db_session.scalar(select(PrintLogEntry).where(PrintLogEntry.archive_id == two_plates.archive))
    assert logged.cost == 0.0


async def test_a_print_on_spools_without_a_price_is_not_free_at_a_default_rate_of_zero(
    test_engine, db_session, two_plates
):
    """The spool in the tray has no price and the default rate is 0. That
    priced the run 0, and the print was charged nothing. A spool without a
    price is not a free one (priced 0): the run gets no price and is charged
    from the archive's cost, 5.00 x 20/100."""
    spool = await db_session.scalar(select(Spool))
    spool.cost_per_kg = None
    (await db_session.scalar(select(Settings).where(Settings.key == "default_filament_cost"))).value = "0"
    await db_session.commit()

    run = await _start_and_price(test_engine, db_session, two_plates)
    assert (run.planned_grams, run.planned_cost) == (None, None)

    await _complete(test_engine, two_plates.printer, "completed", name=SUBTASK)

    assert [c.amount for c in await _charges(db_session)] == [-1.0]


async def test_a_print_that_costs_nothing_passes_the_budget_check(db_session, farm):
    """A reprint only on free spools reserves 0 (#3580), which the budget
    check refused as no estimate at all. No estimate is still refused."""
    from fastapi import HTTPException

    from backend.app.models.finance import BudgetReservation
    from backend.app.services.finance_budget import create_budget_reservation, validate_print_budget

    reservation = await create_budget_reservation(
        db_session,
        cost_center_id=farm.center,
        estimated_cost=0.0,
        current_user=None,
        source_type="print_queue",
        source_id=1,
    )

    assert isinstance(reservation, BudgetReservation) and reservation.amount == 0.0
    with pytest.raises(HTTPException, match="Estimated cost is required"):
        await validate_print_budget(db_session, cost_center_id=farm.center, estimated_cost=None, current_user=None)


async def test_a_failed_print_of_one_plate_pays_its_share_of_that_plate(test_engine, db_session, two_plates):
    """Half of plate 2 is 10 g of 20 g, at half of the plate's 0.60."""
    await _start_and_price(test_engine, db_session, two_plates)

    await _complete(test_engine, two_plates.printer, "failed", name=SUBTASK, extra={"last_progress": 50})

    charge = (await _charges(db_session))[-1]
    assert charge.amount == -0.3
    assert "[failed: 50.0% filament (10.0g/20.0g)]" in charge.description


async def test_a_print_started_at_the_printer_opens_its_run_before_it_is_priced(
    test_engine, db_session, printer_factory
):
    """The other start path, of a print Bambuddy did not dispatch: its run is
    open and committed when the estimate is scheduled."""
    printer = await printer_factory(name="Walk-up")
    order: list[str] = []
    open_run_for = main_module._open_run_for

    async def opens_the_run(*args, **kwargs):
        await open_run_for(*args, **kwargs)
        order.append("run opened")

    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=printer.id),
        download_ok=True,  # its 3MF is fetched and archived: the start that prices
        extra_patches=[
            patch("backend.app.main._open_run_for", side_effect=opens_the_run),
            patch(
                "backend.app.main._schedule_archive_cost_estimate",
                side_effect=lambda *a, **k: order.append("estimate scheduled"),
            ),
        ],
    )

    assert order == ["run opened", "estimate scheduled"]
    run = await db_session.scalar(
        select(PrintLogEntry)
        .where(PrintLogEntry.printer_id == printer.id, PrintLogEntry.status == RUN_OPEN)
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )
    assert run is not None


# --- A run its start did not price ------------------------------------------


async def _start_unpriced(test_engine, db_session, farm) -> dict:
    """A print of the archive started at the printer whose start estimate
    never got to write: scheduled, and gone before it ran."""
    register_expected_print(farm.printer, FILE, farm.archive, ams_mapping=[0], cost_center_id=farm.center)
    return await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        state_fields={"raw_data": TRAYS},
        extra_patches=[patch("backend.app.main._schedule_archive_cost_estimate")],
    )


async def test_a_run_its_start_did_not_price_is_priced_and_charged_at_its_end(test_engine, db_session, two_plates):
    """A print that ends before its start estimate wrote (a failure within
    seconds, a slow Spoolman, a restart) was charged the archive's first-run
    cost for its plate, 5.00 x 20/100, without a word. Its end prices it:
    plate 2's 20 g at 30/kg."""
    await _start_unpriced(test_engine, db_session, two_plates)

    await _complete(test_engine, two_plates.printer, "completed", name=SUBTASK)

    assert [c.amount for c in await _charges(db_session)] == [-0.6]
    db_session.expire_all()
    run = await db_session.scalar(select(PrintLogEntry).where(PrintLogEntry.archive_id == two_plates.archive))
    assert (run.planned_cost, run.planned_grams) == (0.6, 20.0)


async def test_a_print_resumed_after_a_restart_has_its_run_priced(test_engine, db_session, two_plates):
    """A restart takes the start's estimate with it. The print seen again
    opened its run and scheduled nothing: the run is priced now, and only the
    run, the card being its start's to write."""
    await _start_unpriced(test_engine, db_session, two_plates)

    mocks = await _drive_print_start(
        test_engine,
        SimpleNamespace(id=two_plates.printer),
        download_ok=False,
        state_fields={"raw_data": TRAYS, "progress": 40},
        extra_patches=[patch("backend.app.main._schedule_archive_cost_estimate")],
    )

    resumed = mocks["_schedule_archive_cost_estimate"].call_args
    assert resumed.args[:2] == (two_plates.printer, two_plates.archive)
    assert resumed.kwargs["run_only"] is True


async def test_a_print_resumed_by_its_job_id_has_its_run_priced(test_engine, db_session, two_plates, caplog):
    """The other way a print is seen again: a cloud print reports the job id
    its run was opened with, and is resumed on that. Its run is priced there
    as well."""
    job = {"raw_data": TRAYS, "subtask_id": "4711"}
    register_expected_print(
        two_plates.printer, FILE, two_plates.archive, ams_mapping=[0], cost_center_id=two_plates.center
    )
    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=two_plates.printer),
        download_ok=False,
        state_fields=job,
        raw_data={"subtask_id": "4711"},
        extra_patches=[patch("backend.app.main._schedule_archive_cost_estimate")],
    )

    with caplog.at_level("INFO", logger="backend.app.main"):
        mocks = await _drive_print_start(
            test_engine,
            SimpleNamespace(id=two_plates.printer),
            download_ok=False,
            state_fields={**job, "progress": 40},
            raw_data={"subtask_id": "4711"},
            extra_patches=[patch("backend.app.main._schedule_archive_cost_estimate")],
        )

    assert "on subtask_id match (4711)" in caplog.text, "resumed by its job id"
    resumed = mocks["_schedule_archive_cost_estimate"].call_args
    assert resumed is not None, "the resumed print's run is priced"
    assert resumed.args[:2] == (two_plates.printer, two_plates.archive)
    assert resumed.kwargs["run_only"] is True


# --- The queue's estimate, at the routes ------------------------------------


@pytest.fixture
async def center(db_session):
    from backend.app.models.finance import CostCenter

    db_session.add(Settings(key="billing_enabled", value="true"))
    cost_center = CostCenter(name="Lab", is_active=True, is_private=False)
    db_session.add(cost_center)
    await db_session.commit()
    return cost_center.id


async def test_a_reprint_is_queued_at_the_price_of_the_mapping_it_is_stored_with(
    async_client, db_session, printer_factory, archive_factory, center
):
    """With no mapping in the request the item takes the slicer's saved one,
    and its print is charged at that. The budget was checked with the
    request's, none: the archive's cost."""
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id, extra_data={"slicer_ams_mapping": {"mapping": [5, -1, 2, -1], "printer_id": printer.id}}
    )

    with patch(
        "backend.app.api.routes.print_queue.estimate_queue_source_cost", new=AsyncMock(return_value=1.5)
    ) as estimate:
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "cost_center_id": center}
        )

    assert response.status_code == 200, response.text
    assert response.json()["ams_mapping"] == [5, -1, 2, -1]
    assert json.loads(estimate.await_args.kwargs["ams_mapping"]) == [5, -1, 2, -1]


async def test_with_billing_off_the_queue_prices_nothing(async_client, db_session, printer_factory, archive_factory):
    """The estimate is what the budget check reads. With billing off it was
    worked out all the same, in Spoolman mode with requests to Spoolman, on
    every queue add and edit."""
    printer = await printer_factory()
    archive = await archive_factory(printer.id)

    with patch("backend.app.api.routes.print_queue.estimate_queue_source_cost", new=AsyncMock()) as estimate:
        added = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "ams_mapping": [0]}
        )
        assert added.status_code == 200, added.text
        edited = await async_client.patch(f"/api/v1/queue/{added.json()['id']}", json={"ams_mapping": [1]})
        assert edited.status_code == 200, edited.text

    estimate.assert_not_awaited()
    assert edited.json()["estimated_cost"] is None


async def test_a_bulk_edit_asks_spoolman_for_its_spools_once(
    async_client, db_session, printer_factory, archive_factory, center, tmp_path, monkeypatch
):
    """Moving three items to a cost centre prices each of them: three
    downloads of Spoolman's spool list, where one serves them all."""
    from backend.app.models.finance import CostCenter
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.services import printer_manager as printer_manager_module

    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    (tmp_path / "archive").mkdir()
    with zipfile.ZipFile(tmp_path / "archive" / "print.3mf", "w") as zf:
        zf.writestr("Metadata/slice_info.config", TWO_PLATES)
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    other = CostCenter(name="Workshop", is_active=True, is_private=False)
    db_session.add(other)
    printer = await printer_factory()
    archive = await archive_factory(printer.id, file_path="archive/print.3mf", filament_used_grams=100.0, cost=5.0)
    items = [
        PrintQueueItem(
            printer_id=printer.id,
            archive_id=archive.id,
            status="pending",
            position=position,
            plate_id=2,
            ams_mapping="[0]",
            cost_center_id=center,
        )
        for position in range(1, 4)
    ]
    db_session.add_all(items)
    await db_session.commit()

    tag = "A" * 31 + "1"
    spool = {"id": 7, "price": 90, "initial_weight": 1000, "filament": {"weight": 1000}, "extra": {"tag": tag}}
    client = MagicMock()
    client.get_spools = AsyncMock(return_value=[spool])
    client.find_spool_by_tag = AsyncMock(return_value=spool)
    trays = {"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_uuid": tag}]}], "vt_tray": []}
    with (
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
            new=AsyncMock(return_value=client),
        ) as health_checked,
        patch("backend.app.services.spoolman_tracking._get_printer_serial", new=AsyncMock(return_value="")),
        patch.object(
            printer_manager_module.printer_manager, "get_status", lambda _printer_id: SimpleNamespace(raw_data=trays)
        ),
    ):
        response = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [item.id for item in items], "cost_center_id": other.id}
        )

    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 3
    for item in items:
        await db_session.refresh(item)
    assert [item.estimated_cost for item in items] == [1.8, 1.8, 1.8], "20 g at 90/kg, from the tagged spool"
    client.get_spools.assert_awaited_once()
    health_checked.assert_awaited_once()


async def test_a_bulk_edit_to_another_printer_prices_the_item_without_its_old_mapping(
    async_client, db_session, printer_factory, archive_factory, center, tmp_path, monkeypatch
):
    """An item mapped to tray 0 of its printer is moved to another printer,
    and to another cost centre, in one bulk edit. The mapping goes with the
    old printer, but the item was priced with it first: at the spool in tray
    0 of the new printer, a free one here, so it reserved 0. It is priced as
    it is stored, with no mapping: the archive's cost for plate 2, 5.00 x
    20/100."""
    from backend.app.models.finance import CostCenter
    from backend.app.models.print_queue import PrintQueueItem

    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    (tmp_path / "archive").mkdir()
    with zipfile.ZipFile(tmp_path / "archive" / "print.3mf", "w") as zf:
        zf.writestr("Metadata/slice_info.config", TWO_PLATES)
    other = CostCenter(name="Workshop", is_active=True, is_private=False)
    free_spool = Spool(material="PLA", cost_per_kg=0)
    db_session.add_all([other, free_spool])
    old_printer = await printer_factory(name="Old")
    new_printer = await printer_factory(name="New")
    archive = await archive_factory(old_printer.id, file_path="archive/print.3mf", filament_used_grams=100.0, cost=5.0)
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=free_spool.id, printer_id=new_printer.id, ams_id=0, tray_id=0))
    item = PrintQueueItem(
        printer_id=old_printer.id,
        archive_id=archive.id,
        status="pending",
        position=1,
        plate_id=2,
        ams_mapping="[0]",
        cost_center_id=center,
    )
    db_session.add(item)
    await db_session.commit()

    response = await async_client.patch(
        "/api/v1/queue/bulk", json={"item_ids": [item.id], "printer_id": new_printer.id, "cost_center_id": other.id}
    )

    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 1
    await db_session.refresh(item)
    assert (item.printer_id, item.ams_mapping) == (new_printer.id, None)
    assert item.estimated_cost == 1.0


async def test_with_billing_off_a_dispatch_prices_nothing(db_session, printer_factory, archive_factory):
    """The dispatch prices the item again for its budget check. With billing
    off nothing reads that, and it was worked out all the same, in Spoolman
    mode with requests to Spoolman, for every job sent."""
    from fastapi import HTTPException

    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.services.print_scheduler import PrintScheduler

    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    item = PrintQueueItem(
        printer_id=printer.id,
        archive_id=archive.id,
        status="pending",
        position=1,
        ams_mapping="[0]",
        estimated_cost=3.0,
    )
    db_session.add(item)
    await db_session.commit()
    scheduler = PrintScheduler()

    # The dispatch is stopped at its budget check: what is tested is before it.
    with (
        patch(
            "backend.app.services.print_scheduler.estimate_queue_source_cost", new=AsyncMock(return_value=9.0)
        ) as estimate,
        patch(
            "backend.app.services.print_scheduler.validate_print_budget",
            new=AsyncMock(side_effect=HTTPException(400, "stop here")),
        ) as budget_check,
        patch.object(scheduler, "_fail_dispatch", new=AsyncMock(return_value=False)),
    ):
        await scheduler._start_print(db_session, item)

    budget_check.assert_awaited_once()
    estimate.assert_not_awaited()
    assert item.estimated_cost is None
