"""Every run is priced at its start from the spools mapped for it (#3580).

The start estimate priced the archive only on its first run (#1378), and
billing charged a reprint from that. It now also prices the run opened at
each start, the first and every later one, for the plate that run prints,
and keeps the grams it priced with the price.
"""

import asyncio
import zipfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.print_log import INCLUDE_OPEN_RUNS, RUN_OPEN, PrintLogEntry
from backend.app.models.settings import Settings
from backend.app.services import archive_cost_estimate, printer_manager as printer_manager_module
from backend.app.services.archive_cost_estimate import estimate_archive_cost_at_start
from backend.app.services.print_cost_estimate import estimate_queue_source_cost
from backend.tests.unit.services.test_archive_cost_estimate_3261 import (  # noqa: F401
    _assign_spool,
    _printer_manager,
    _setup,
    three_mf,
    ws,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def billing_on(db_session):
    """A run's price is what billing charges: it is worked out with billing on."""
    db_session.add(Settings(key="billing_enabled", value="true"))
    await db_session.commit()


async def _open_run(db_session, archive, printer_id: int, plate_id: int | None = None) -> int:
    """The run a print start opens (``print_log.open_run``)."""
    run = PrintLogEntry(
        archive_id=archive.id,
        printer_id=printer_id,
        status=RUN_OPEN,
        started_at=archive.started_at,
        plate_id=plate_id,
    )
    db_session.add(run)
    await db_session.commit()
    return run.id


async def _planned(db_session, run_id: int) -> tuple[float | None, float | None]:
    """The run's price and the grams it was priced for."""
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(PrintLogEntry.planned_cost, PrintLogEntry.planned_grams)
            .where(PrintLogEntry.id == run_id)
            .execution_options(**{INCLUDE_OPEN_RUNS: True})
        )
    ).one()
    return tuple(row)


async def test_a_reprint_is_priced_on_its_run_and_the_card_keeps_its_cost(
    db_session, printer_factory, archive_factory, three_mf, ws
):
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True)
    run_id = await _open_run(db_session, archive, printer.id)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
    three_mf.append({"slot_id": 1, "used_g": 4.17})

    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    assert await _planned(db_session, run_id) == (7.80, 4.17)
    await db_session.refresh(archive)
    assert archive.cost == 14.18, "a reprint keeps its first run's cost on the card (#1378)"
    ws.assert_not_awaited()


async def test_a_first_run_is_priced_on_its_run_as_on_the_card(
    db_session, printer_factory, archive_factory, three_mf, ws
):
    printer, archive = await _setup(db_session, printer_factory, archive_factory)
    run_id = await _open_run(db_session, archive, printer.id)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
    three_mf.append({"slot_id": 1, "used_g": 4.17})

    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    assert await _planned(db_session, run_id) == (7.80, 4.17)
    await db_session.refresh(archive)
    assert archive.cost == 7.80


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


@pytest.fixture
def two_plates(tmp_path, monkeypatch):
    """A real 3MF of two plates, 80 g and 20 g, read for real."""
    monkeypatch.setattr(archive_cost_estimate.app_settings, "base_dir", tmp_path)
    (tmp_path / "archive").mkdir()
    with zipfile.ZipFile(tmp_path / "archive" / "print.3mf", "w") as zf:
        zf.writestr("Metadata/slice_info.config", TWO_PLATES)


@pytest.mark.parametrize("start_plate", [2, None], ids=["start-names-the-plate", "only-the-archive-knows-it"])
async def test_a_run_is_priced_for_the_plate_it_prints(
    db_session, printer_factory, archive_factory, two_plates, ws, start_plate
):
    """The run prints plate 2, 20 g of the file's 100 g, on a spool at 30/kg:
    0.60. A start at the printer names no plate (``start_plate`` None); the
    archive read it from the G-code path and the run opened carries it. Priced
    at the whole file, the run was charged 3.00 for 20 g."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, grams=100.0)
    archive.plate_id = 2
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)

    await estimate_archive_cost_at_start(
        db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0], plate_id=start_plate
    )

    assert await _planned(db_session, run_id) == (0.60, 20.0)


async def test_the_card_of_a_print_whose_start_names_no_plate_stays_the_whole_files(
    db_session, printer_factory, archive_factory, two_plates, ws
):
    """The run's own figure is worked out beside the card's, not in its place."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, grams=100.0)
    archive.plate_id = 2
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)

    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    await db_session.refresh(archive)
    assert archive.cost == 3.00
    assert await _planned(db_session, run_id) == (0.60, 20.0)


async def test_a_run_no_one_named_a_plate_for_is_priced_for_the_archives_plate(
    db_session, printer_factory, archive_factory, two_plates, ws
):
    """Neither the run opened at the start nor the start names a plate; the
    archive read plate 2 from the G-code path. Priced at the whole file's
    slots, the run was 3.00 for the 20 g it is charged for."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    archive.plate_id = 2
    run_id = await _open_run(db_session, archive, printer.id, plate_id=None)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)

    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    assert await _planned(db_session, run_id) == (0.60, 20.0)


EXTERNAL = 254  # the external spool's tray, as the print dialog and the scheduler store it


@pytest.mark.parametrize("tray", [0, EXTERNAL], ids=["ams", "external-spool"])
async def test_a_queued_reprint_reserves_what_its_run_will_be_charged(
    db_session, printer_factory, archive_factory, two_plates, ws, tray
):
    """The queue reserved the archive's cost for the plate (14.18 x 20/100 =
    2.84) while the run was charged at its spools: a budget could be overrun.
    With a mapping, the reservation is priced as the run is."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)
    if tray == EXTERNAL:
        await _assign_spool(db_session, printer.id, 255, 0, cost_per_kg=30)
    else:
        await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)
    printer_state = _printer_manager(vt_tray=[{"id": EXTERNAL, "tray_type": "PLA", "tray_color": "FFFFFFFF"}])

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=2, ams_mapping=f"[{tray}]", printer_id=printer.id
    )
    await estimate_archive_cost_at_start(
        db_session, printer.id, archive.id, printer_state, ams_mapping=[tray], plate_id=2
    )

    assert reserved == 0.60
    assert (await _planned(db_session, run_id))[0] == reserved


async def test_a_queued_reprint_on_a_spool_priced_zero_reserves_what_it_is_charged(
    db_session, printer_factory, archive_factory, ws, tmp_path, monkeypatch
):
    """A built-in spool priced 0 is free to the start estimate, where the
    reservation put it at the default rate: 15 g on a 30/kg spool and 5 g on
    a free one reserved 0.45 + 0.02 and were charged 0.45."""
    monkeypatch.setattr(archive_cost_estimate.app_settings, "base_dir", tmp_path)
    (tmp_path / "archive").mkdir()
    with zipfile.ZipFile(tmp_path / "archive" / "print.3mf", "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            """<config><plate><metadata key="index" value="1"/>
            <filament id="1" type="PLA" color="#FFFFFF" used_g="15"/>
            <filament id="2" type="PLA" color="#000000" used_g="5"/></plate></config>""",
        )
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=20.0)
    run_id = await _open_run(db_session, archive, printer.id, plate_id=1)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)
    await _assign_spool(db_session, printer.id, 0, 1, cost_per_kg=0)
    trays = [{"id": i, "tray_type": "PLA", "tray_color": "FFFFFFFF"} for i in range(2)]

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=1, ams_mapping="[0, 1]", printer_id=printer.id
    )
    await estimate_archive_cost_at_start(
        db_session, printer.id, archive.id, _printer_manager(trays), ams_mapping=[0, 1], plate_id=1
    )

    assert reserved == 0.45
    assert (await _planned(db_session, run_id))[0] == reserved


async def test_a_reprint_only_on_free_spools_is_priced_and_reserves_zero(
    db_session, printer_factory, archive_factory, two_plates, ws
):
    """A spool priced 0 is free, so a run only on such spools costs 0: that is
    kept on the run and reserved. It got no price and was charged the
    archive's first-run cost (2.84 for the plate), which it also reserved."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=0)

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=2, ams_mapping="[0]", printer_id=printer.id
    )
    await estimate_archive_cost_at_start(
        db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0], plate_id=2
    )

    assert reserved == 0.0
    assert await _planned(db_session, run_id) == (0.0, 20.0)


async def _default_rate(db_session, value: str) -> None:
    setting = await db_session.scalar(select(Settings).where(Settings.key == "default_filament_cost"))
    setting.value = value
    await db_session.commit()


async def test_a_spool_without_a_price_is_not_free_at_a_default_rate_of_zero(
    db_session, printer_factory, archive_factory, two_plates, ws
):
    """A built-in spool with no price is at the default rate. With that set
    to 0 it got the rate 0, a free spool's: the run was priced 0 and reserved
    0. It has no price, so the run has none and is charged from the archive's
    cost, which is what the queue reserves: 14.18 x 20/100."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    await _default_rate(db_session, "0")
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=None)

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=2, ams_mapping="[0]", printer_id=printer.id
    )
    await estimate_archive_cost_at_start(
        db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0], plate_id=2
    )

    assert reserved == 2.84
    assert await _planned(db_session, run_id) == (None, None)


async def test_a_spool_priced_zero_is_free_at_a_default_rate_of_zero_too(
    db_session, printer_factory, archive_factory, two_plates, ws
):
    """A price of 0 is a price, whatever the default rate."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    await _default_rate(db_session, "0")
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=0)

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=2, ams_mapping="[0]", printer_id=printer.id
    )
    await estimate_archive_cost_at_start(
        db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0], plate_id=2
    )

    assert reserved == 0.0
    assert await _planned(db_session, run_id) == (0.0, 20.0)


async def test_a_spool_without_a_price_is_at_the_default_rate(
    db_session, printer_factory, archive_factory, two_plates, ws
):
    """With a default rate above 0 nothing changes: 20 g at 3400/kg, run and
    reservation alike."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=None)

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=2, ams_mapping="[0]", printer_id=printer.id
    )
    await estimate_archive_cost_at_start(
        db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0], plate_id=2
    )

    assert reserved == 68.0
    assert await _planned(db_session, run_id) == (68.0, 20.0)


async def test_a_queued_reprint_reserves_the_grams_no_slot_accounts_for_at_the_default_rate(
    db_session, printer_factory, archive_factory, two_plates, ws
):
    """The archive weighs 102 g, its slots 100 g. A run nobody named a plate
    for is charged the 2 g between them at the default rate (3400/kg: 6.80)
    on top of 100 g at 30/kg, and the reservation's pricing does the same
    with a total above its slots: 9.80, not the slots' 3.00."""
    from backend.app.services.print_cost_estimate import _cost_at_mapped_spools

    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=102.0)
    run_id = await _open_run(db_session, archive, printer.id)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)
    slots = [{"slot_id": 1, "used_g": 80.0}, {"slot_id": 1, "used_g": 20.0}]

    reserved = await _cost_at_mapped_spools(db_session, slots, "[0]", printer.id, total_grams=102.0)
    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    assert reserved == (9.80, True)
    assert await _planned(db_session, run_id) == (9.80, 102.0)


def _one_plate(tmp_path, monkeypatch, *grams: float) -> None:
    """A real 3MF of one plate, a filament of each of ``grams``, as the archive's file."""
    monkeypatch.setattr(archive_cost_estimate.app_settings, "base_dir", tmp_path)
    (tmp_path / "archive").mkdir(exist_ok=True)
    filaments = "".join(
        f'<filament id="{slot}" type="PLA" color="#FFFFFF" used_g="{used}"/>' for slot, used in enumerate(grams, 1)
    )
    with zipfile.ZipFile(tmp_path / "archive" / "print.3mf", "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            f'<config><plate><metadata key="index" value="1"/>{filaments}</plate></config>',
        )


async def test_a_slot_mapped_to_the_external_spool_as_minus_one_reserves_what_it_is_charged(
    db_session, printer_factory, archive_factory, ws, tmp_path, monkeypatch
):
    """The flat mapping writes the external spool as -1. The start estimate
    reads that as the printer's external tray, the reservation read it as no
    tray: 50 g on an external spool at 100/kg and 50 g at 30/kg reserved
    50 g at the default rate + 1.50 and were charged 6.50."""
    _one_plate(tmp_path, monkeypatch, 50, 50)
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    run_id = await _open_run(db_session, archive, printer.id, plate_id=1)
    await _assign_spool(db_session, printer.id, 255, 0, cost_per_kg=100)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)
    state = _printer_manager(vt_tray=[{"id": EXTERNAL, "tray_type": "PLA", "tray_color": "FFFFFFFF"}])

    # The reservation reads the printer's external tray off the printer itself.
    with patch.object(printer_manager_module.printer_manager, "get_status", state.get_status):
        reserved = await estimate_queue_source_cost(
            db_session, archive=archive, plate_id=1, ams_mapping="[-1, 0]", printer_id=printer.id
        )
    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, state, ams_mapping=[-1, 0], plate_id=1)

    assert reserved == 6.50
    assert (await _planned(db_session, run_id))[0] == reserved


async def test_a_queue_item_that_names_no_plate_reserves_the_plate_its_dispatch_prints(
    db_session, printer_factory, archive_factory, two_plates, ws, tmp_path
):
    """A dispatch that names no plate prints the file's default one, plate 1
    (80 g at 30/kg: 2.40). The reservation priced the plate the archive
    printed last, plate 2: 0.60."""
    with zipfile.ZipFile(tmp_path / "archive" / "print.3mf", "a") as zf:
        zf.writestr("Metadata/plate_1.gcode", "G1")
        zf.writestr("Metadata/plate_2.gcode", "G1")
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    archive.plate_id = 2
    # The run the dispatch opens carries the plate it sent.
    run_id = await _open_run(db_session, archive, printer.id, plate_id=1)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)

    reserved = await estimate_queue_source_cost(db_session, archive=archive, ams_mapping="[0]", printer_id=printer.id)
    unmapped = await estimate_queue_source_cost(db_session, archive=archive, printer_id=printer.id)
    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    assert reserved == 2.40
    assert unmapped == 11.34, "the archive's cost for plate 1: 14.18 x 80/100"
    assert (await _planned(db_session, run_id))[0] == reserved


async def test_a_library_file_on_spools_priced_zero_reserves_what_it_is_charged(
    db_session, printer_factory, archive_factory, ws, tmp_path, monkeypatch
):
    """A library file's first print is charged its start estimate, where a
    built-in spool priced 0 is free. Its reservation put that spool at the
    default rate: 20 g reserved 68.00 and were charged nothing."""
    from types import SimpleNamespace

    _one_plate(tmp_path, monkeypatch, 20)
    printer, _archive = await _setup(db_session, printer_factory, archive_factory, grams=20.0)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=0)
    library_file = SimpleNamespace(file_path=str(tmp_path / "archive" / "print.3mf"), file_metadata={})

    reserved = await estimate_queue_source_cost(
        db_session, library_file=library_file, plate_id=1, ams_mapping=[0], printer_id=printer.id
    )

    assert reserved == 0.0


async def test_a_library_file_on_spools_without_a_price_reserves_nothing_at_a_default_rate_of_zero(
    db_session, printer_factory, archive_factory, ws, tmp_path, monkeypatch
):
    """Nothing prices it, a spool without a price included: no estimate, as
    before #3580, not an estimate of 0."""
    from types import SimpleNamespace

    _one_plate(tmp_path, monkeypatch, 20)
    printer, _archive = await _setup(db_session, printer_factory, archive_factory, grams=20.0)
    await _default_rate(db_session, "0")
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=None)
    library_file = SimpleNamespace(file_path=str(tmp_path / "archive" / "print.3mf"), file_metadata={})

    reserved = await estimate_queue_source_cost(
        db_session, library_file=library_file, plate_id=1, ams_mapping=[0], printer_id=printer.id
    )

    assert reserved is None


class _Spoolman:
    """A Spoolman holding ``spools``, each found by the tag in ``extra.tag``."""

    def __init__(self, *spools: dict):
        self.spools = list(spools)
        self.get_spools = AsyncMock(side_effect=lambda: self.spools)

    async def find_spool_by_tag(self, tag: str, cached_spools: list[dict] | None = None) -> dict | None:
        spools = cached_spools if cached_spools is not None else self.spools
        return next((s for s in spools if s["extra"]["tag"] == tag), None)

    async def get_spool(self, spool_id: int) -> dict:
        raise AssertionError("no spool is assigned to a slot")


TAG_A, TAG_B = "A" * 31 + "1", "B" * 31 + "2"


def _tagged_spool(spool_id: int, tag: str, price: float) -> dict:
    return {"id": spool_id, "price": price, "initial_weight": 1000, "filament": {"weight": 1000}, "extra": {"tag": tag}}


@pytest.fixture
def spoolman_by_tag():
    """Spoolman mode with two RFID spools in the AMS, at 90/kg and 40/kg,
    known to Spoolman by their tags and assigned to no slot in Bambuddy."""
    client = _Spoolman(_tagged_spool(7, TAG_A, 90), _tagged_spool(8, TAG_B, 40))
    trays = [
        {"id": 0, "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_uuid": TAG_A},
        {"id": 1, "tray_type": "PLA", "tray_color": "000000FF", "tray_uuid": TAG_B},
    ]
    state = _printer_manager(trays)
    with (
        patch("backend.app.services.spoolman.get_spoolman_client", new=AsyncMock(return_value=client)),
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
            new=AsyncMock(return_value=client),
        ),
        patch("backend.app.services.spoolman_tracking._get_printer_serial", new=AsyncMock(return_value="")),
        patch(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
            new=AsyncMock(return_value=None),
        ),
        # The reservation reads the trays' tags off the printer itself.
        patch.object(printer_manager_module.printer_manager, "get_status", state.get_status),
    ):
        yield client, state


async def test_a_queued_reprint_on_a_tag_matched_spoolman_spool_reserves_what_it_is_charged(
    db_session, printer_factory, archive_factory, two_plates, ws, spoolman_by_tag
):
    """The start estimate finds a Spoolman spool by its RFID tag, the
    reservation only looked at the slot assignments: with none, it reserved
    the archive's cost (2.84) and the run was charged 20 g at 90/kg, 1.80."""
    _client, state = spoolman_by_tag
    printer, archive = await _setup(
        db_session, printer_factory, archive_factory, spoolman=True, with_run=True, grams=100.0
    )
    run_id = await _open_run(db_session, archive, printer.id, plate_id=2)

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=2, ams_mapping="[0]", printer_id=printer.id
    )
    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, state, ams_mapping=[0], plate_id=2)

    assert reserved == 1.80
    assert await _planned(db_session, run_id) == (reserved, 20.0)


async def test_the_reservation_matches_every_tag_against_one_list_of_spools(
    db_session, printer_factory, archive_factory, ws, tmp_path, monkeypatch, spoolman_by_tag
):
    """It runs on every queue add and dispatch: two tagged trays are one
    request for the spools, not one per tray. 15 g at 90/kg and 5 g at 40/kg."""
    monkeypatch.setattr(archive_cost_estimate.app_settings, "base_dir", tmp_path)
    (tmp_path / "archive").mkdir()
    with zipfile.ZipFile(tmp_path / "archive" / "print.3mf", "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            """<config><plate><metadata key="index" value="1"/>
            <filament id="1" type="PLA" color="#FFFFFF" used_g="15"/>
            <filament id="2" type="PLA" color="#000000" used_g="5"/></plate></config>""",
        )
    client, _state = spoolman_by_tag
    printer, archive = await _setup(
        db_session, printer_factory, archive_factory, spoolman=True, with_run=True, grams=20.0
    )

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=1, ams_mapping="[0, 1]", printer_id=printer.id
    )

    assert reserved == 1.55
    client.get_spools.assert_awaited_once()


@pytest.mark.parametrize("mapping", [None, "[1]"], ids=["no-mapping", "no-spool-in-the-mapped-tray"])
async def test_a_queued_reprint_nothing_prices_reserves_the_archives_cost(
    db_session, printer_factory, archive_factory, two_plates, ws, mapping
):
    """As its charge falls back to: the archive's cost, for the plate."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True, grams=100.0)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=30)

    reserved = await estimate_queue_source_cost(
        db_session, archive=archive, plate_id=2, ams_mapping=mapping, printer_id=printer.id
    )

    assert reserved == 2.84


async def test_a_reprint_nobody_mapped_is_not_priced(db_session, printer_factory, archive_factory, three_mf, ws):
    """No print-command or queue mapping and no colour match: the run has no
    price of its own, and billing falls back to the archive's cost."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True)
    run_id = await _open_run(db_session, archive, printer.id)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
    three_mf.append({"slot_id": 1, "used_g": 4.17, "color": "#123456"})

    await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager())

    assert await _planned(db_session, run_id) == (None, None)


async def test_a_run_that_ended_meanwhile_is_left_alone(db_session, printer_factory, archive_factory, three_mf, ws):
    """A run no longer open has been charged: its price is not written late."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True)
    run_id = await _open_run(db_session, archive, printer.id)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
    three_mf.append({"slot_id": 1, "used_g": 4.17})

    real = archive_cost_estimate._internal_rates

    async def ends_during_the_lookup(db, *args):
        run = await db.get(PrintLogEntry, run_id, execution_options={INCLUDE_OPEN_RUNS: True})
        run.status = "completed"
        await db.commit()
        return await real(db, *args)

    with patch.object(archive_cost_estimate, "_internal_rates", side_effect=ends_during_the_lookup):
        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    assert await _planned(db_session, run_id) == (None, None)


async def test_an_archive_printing_on_two_printers_has_an_estimate_for_each(monkeypatch):
    """Tracked by archive alone, the second printer's start took the first's
    place: the first's end then stopped the second's estimate, and its own ran
    on to price the run after its charge."""
    release = asyncio.Event()
    priced: list[int] = []

    async def slow_estimate(_db, printer_id, *_args):
        await release.wait()
        priced.append(printer_id)

    @asynccontextmanager
    async def fake_session():
        yield object()

    monkeypatch.setattr(archive_cost_estimate, "estimate_archive_cost_at_start", slow_estimate)
    first = archive_cost_estimate.schedule_archive_cost_estimate(3, 42, "pm", session_factory=fake_session)
    second = archive_cost_estimate.schedule_archive_cost_estimate(4, 42, "pm", session_factory=fake_session)
    await asyncio.sleep(0)

    # The print on printer 3 ends.
    await archive_cost_estimate.settle_archive_cost_estimate(3, 42)
    release.set()
    await asyncio.gather(first, second, return_exceptions=True)

    assert first.cancelled(), "its own estimate is stopped before its charge"
    assert priced == [4], "the other printer's still prices its run"


async def test_spoolman_not_listing_its_spools_still_prices_the_slot_assigned_ones(
    db_session, printer_factory, archive_factory, ws, tmp_path, monkeypatch, spoolman_by_tag
):
    """The list of spools the tags are matched against cannot be fetched. It
    was asked for again for every tray, and its failure skipped the slot
    assignment too: spools assigned to their slots were left unpriced and the
    job reserved the archive's cost (14.18), not 20 g at 90/kg."""
    _one_plate(tmp_path, monkeypatch, 15, 5)
    client, _state = spoolman_by_tag
    client.get_spools = AsyncMock(side_effect=RuntimeError("Spoolman timed out"))
    client.get_spool = AsyncMock(return_value=_tagged_spool(7, TAG_A, 90))
    printer, archive = await _setup(
        db_session, printer_factory, archive_factory, spoolman=True, with_run=True, grams=20.0
    )

    with patch(
        "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
        new=AsyncMock(return_value=7),
    ):
        reserved = await estimate_queue_source_cost(
            db_session, archive=archive, plate_id=1, ams_mapping="[0, 1]", printer_id=printer.id
        )

    assert reserved == 1.80
    client.get_spools.assert_awaited_once()


async def test_estimates_sharing_a_request_ask_spoolman_for_its_spools_once(
    db_session, printer_factory, archive_factory, ws, tmp_path, monkeypatch, spoolman_by_tag
):
    """A bulk edit prices every item it changes: three items were three
    health checks and three downloads of the spool list."""
    from backend.app.services import spoolman_tracking
    from backend.app.services.archive_cost_estimate import SpoolmanOnce

    _one_plate(tmp_path, monkeypatch, 15, 5)
    client, _state = spoolman_by_tag
    printer, archive = await _setup(
        db_session, printer_factory, archive_factory, spoolman=True, with_run=True, grams=20.0
    )

    once = SpoolmanOnce()
    reserved = [
        await estimate_queue_source_cost(
            db_session, archive=archive, plate_id=1, ams_mapping="[0, 1]", printer_id=printer.id, spoolman_once=once
        )
        for _item in range(3)
    ]

    assert reserved == [1.55, 1.55, 1.55]
    client.get_spools.assert_awaited_once()
    spoolman_tracking._get_spoolman_client_with_fallback.assert_awaited_once()


async def test_with_billing_off_a_reprint_asks_its_spools_nothing(
    db_session, printer_factory, archive_factory, three_mf, ws
):
    """A run's price is only what billing charges. With billing off a reprint
    looked its spools up all the same, in Spoolman mode a request per tray at
    every print start."""
    from sqlalchemy import delete

    await db_session.execute(delete(Settings).where(Settings.key == "billing_enabled"))
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True)
    run_id = await _open_run(db_session, archive, printer.id)
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
    three_mf.append({"slot_id": 1, "used_g": 4.17})

    with patch.object(archive_cost_estimate, "_internal_rates", new=AsyncMock(return_value={})) as rates:
        await estimate_archive_cost_at_start(db_session, printer.id, archive.id, _printer_manager(), ams_mapping=[0])

    rates.assert_not_awaited()
    assert await _planned(db_session, run_id) == (None, None)


def _on(db_session):
    """A session factory handing out the test's session."""

    @asynccontextmanager
    async def factory():
        yield db_session

    return factory


async def test_a_run_its_start_did_not_price_is_priced_at_its_end(
    db_session, printer_factory, archive_factory, three_mf, ws
):
    """The start's estimate never wrote the run's price (a failure within
    seconds, a slow Spoolman, a restart). The run is priced when the print
    ends, from the mapping its queue item kept, though that item is no longer
    printing; a first run's card is not this one's to write."""
    from backend.app.models.print_queue import PrintQueueItem

    printer, archive = await _setup(db_session, printer_factory, archive_factory)
    item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="completed", ams_mapping="[0]")
    db_session.add(item)
    await db_session.commit()
    run_id = await _open_run(db_session, archive, printer.id)
    run = await db_session.get(PrintLogEntry, run_id, execution_options={INCLUDE_OPEN_RUNS: True})
    run.queue_item_id = item.id
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
    three_mf.append({"slot_id": 1, "used_g": 4.17})

    await archive_cost_estimate.price_unpriced_run(
        printer.id, archive.id, _printer_manager(), session_factory=_on(db_session)
    )

    assert await _planned(db_session, run_id) == (7.80, 4.17)
    await db_session.refresh(archive)
    assert archive.cost == 14.18
    ws.assert_not_awaited()


async def test_a_run_that_has_its_price_is_not_priced_again(db_session, printer_factory, archive_factory, three_mf, ws):
    """Priced at its start: neither its end nor a restart resuming the print
    looks its spools up again."""
    printer, archive = await _setup(db_session, printer_factory, archive_factory, with_run=True)
    run_id = await _open_run(db_session, archive, printer.id)
    run = await db_session.get(PrintLogEntry, run_id, execution_options={INCLUDE_OPEN_RUNS: True})
    run.planned_cost, run.planned_grams = 1.23, 4.17
    await _assign_spool(db_session, printer.id, 0, 0, cost_per_kg=1870)
    three_mf.append({"slot_id": 1, "used_g": 4.17})

    with patch.object(archive_cost_estimate, "_internal_rates", new=AsyncMock(return_value={})) as rates:
        await archive_cost_estimate.price_unpriced_run(
            printer.id, archive.id, _printer_manager(), ams_mapping=[0], session_factory=_on(db_session)
        )

    rates.assert_not_awaited()
    assert await _planned(db_session, run_id) == (1.23, 4.17)


async def test_pricing_a_run_at_its_end_waits_no_longer_than_its_timeout(monkeypatch):
    """A Spoolman that does not answer must not hold the print's end up: the
    charge then falls back to the archive's cost."""

    async def never(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(archive_cost_estimate, "_estimate", never)

    await asyncio.wait_for(
        archive_cost_estimate.price_unpriced_run(3, 42, "pm", session_factory=_on(object()), timeout=0.01), 5
    )


async def test_a_print_seen_again_does_not_start_a_second_estimate_beside_its_starts(monkeypatch):
    """A repeated start report while the start's estimate still runs: a second
    one would take its place, and the print's end would stop only that one."""
    release = asyncio.Event()

    async def slow_estimate(*_args):
        await release.wait()

    priced_again = AsyncMock()
    monkeypatch.setattr(archive_cost_estimate, "estimate_archive_cost_at_start", slow_estimate)
    monkeypatch.setattr(archive_cost_estimate, "price_run_without_a_price", priced_again)

    first = archive_cost_estimate.schedule_archive_cost_estimate(3, 42, "pm", session_factory=_on(object()))
    await asyncio.sleep(0)
    again = archive_cost_estimate.schedule_archive_cost_estimate(
        3, 42, "pm", session_factory=_on(object()), run_only=True
    )
    release.set()
    await first

    assert again is first
    priced_again.assert_not_awaited()

    # After a restart nothing is running: the run is priced, and only the run.
    resumed = archive_cost_estimate.schedule_archive_cost_estimate(
        3, 42, "pm", ams_mapping=[0], session_factory=_on(object()), run_only=True
    )
    await resumed
    priced_again.assert_awaited_once()
