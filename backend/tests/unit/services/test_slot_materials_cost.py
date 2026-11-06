"""The print dialog's estimate prices each slot at the rate its print is charged.

The dialog priced slots from the built-in assignment table in both inventory
modes. In Spoolman mode that table holds nothing, or rows from before the
switch, so the estimate used the default rate or a spool the printer is not
drawing on, while the print was charged the Spoolman spool's price. The
mode-aware slot payload now carries each slot's rate.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.filament_deficit import build_slot_materials
from backend.tests.unit.services import test_filament_deficit as _deficit_tests

pytestmark = pytest.mark.asyncio


async def _slots(db_session, printer_id: int, client=None):
    patches = _deficit_tests.TestFilamentDeficitBackupAware._patch_status(
        printer_id=printer_id, backup_on=False, model="X1C"
    )
    for p in patches:
        p.start()
    try:
        with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
            return {s.global_tray_id: s for s in await build_slot_materials(db_session, printer_id)}
    finally:
        for p in patches:
            p.stop()


async def test_spoolman_slots_carry_the_charged_rate(db_session, printer_factory):
    printer = await printer_factory(model="X1C")
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    for tray_id in range(3):
        db_session.add(
            SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=tray_id, spoolman_spool_id=10 + tray_id)
        )
    await db_session.commit()
    spools = {
        # The spool's own price, over its own net weight.
        10: {"id": 10, "remaining_weight": 200.0, "price": 10.0, "initial_weight": 250.0, "filament": {"id": 1}},
        # No spool price: the filament's, over the filament's weight.
        11: {"id": 11, "remaining_weight": 900.0, "filament": {"id": 2, "price": 30.0, "weight": 1000.0}},
        # Unpriced: no rate, so the dialog uses the default as the charge does.
        12: {"id": 12, "remaining_weight": 500.0, "filament": {"id": 3}},
    }
    client = AsyncMock()
    client.get_spool = AsyncMock(side_effect=lambda sid: spools[sid])

    slots = await _slots(db_session, printer.id, client)

    assert slots[0].cost_per_kg == pytest.approx(40.0)
    assert slots[1].cost_per_kg == pytest.approx(30.0)
    assert slots[2].cost_per_kg is None
    assert slots[0].to_dict()["cost_per_kg"] == pytest.approx(40.0)


async def test_built_in_slots_carry_the_spool_s_rate(db_session, printer_factory):
    printer = await printer_factory(model="X1C")
    priced = Spool(material="PLA", label_weight=1000, weight_used=0.0, cost_per_kg=22.5)
    unpriced = Spool(material="PLA", label_weight=1000, weight_used=0.0)
    db_session.add_all([priced, unpriced])
    await db_session.commit()
    for tray_id, spool in enumerate((priced, unpriced)):
        db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=tray_id))
    await db_session.commit()

    slots = await _slots(db_session, printer.id)

    assert slots[0].cost_per_kg == 22.5
    assert slots[1].cost_per_kg is None
