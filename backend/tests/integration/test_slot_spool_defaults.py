"""GET /printers/{id}/slots/{ams}/{tray}/spool-defaults

What the Configure AMS Slot dialog opens with. The slot usually already holds
an assigned spool, and that spool carries a filament preset per printer model
and a K profile per hotend -- the values the user set for exactly this
situation. Before this endpoint the dialog defaulted to the slot's last manual
configuration or the tray's RFID data and ignored them.

Everything is resolved for the nozzle THIS slot feeds, so the answer differs
between the two hotends of a dual-nozzle machine.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

URL = "/api/v1/printers/{pid}/slots/{ams}/{tray}/spool-defaults"

RIGHT, LEFT = 0, 1


class _Nozzle:
    def __init__(self, diameter):
        self.nozzle_diameter = diameter


class _State:
    """Dual-nozzle, 0.4 on the right and 0.2 on the left, AMS 0 -> left."""

    def __init__(self, diameters=("0.4", "0.2")):
        self.nozzles = [_Nozzle(d) for d in diameters]
        self.ams_extruder_map = {"0": LEFT, "1": RIGHT}
        self.ams_switch_inlet = None
        self.raw_data = {}


@pytest.fixture
def dual_nozzle_printer_state():
    with patch("backend.app.api.routes.printers.printer_manager") as manager:
        manager.get_status = MagicMock(return_value=_State())
        manager.get_model = MagicMock(return_value="H2D")
        yield manager


@pytest.fixture
async def assigned_spool(db_session, printer_factory):
    """A spool in AMS 0 tray 0, with a per-model preset and both hotends calibrated."""
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment
    from backend.app.models.spool_filament_preset import SpoolFilamentPreset
    from backend.app.models.spool_k_profile import SpoolKProfile

    printer = await printer_factory(model="H2D")
    spool = Spool(
        brand="Bambu",
        material="PLA",
        color_name="Black",
        slicer_filament="GFSA00",
        slicer_filament_name="Bambu PLA Basic @BBL X1C",
    )
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)

    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0))
    db_session.add(
        SpoolFilamentPreset(
            spool_id=spool.id,
            printer_model="H2D",
            nozzle_diameter="0.2",
            slicer_filament="GFSA21",
            slicer_filament_name="Bambu PLA Basic @BBL H2D 0.2 nozzle",
        )
    )
    db_session.add_all(
        [
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=LEFT,
                nozzle_diameter="0.2",
                k_value=0.018,
                cali_idx=16,
                name="PLA left",
            ),
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=RIGHT,
                nozzle_diameter="0.4",
                k_value=0.020,
                cali_idx=15,
                name="PLA right",
            ),
        ]
    )
    await db_session.commit()
    return printer, spool


@pytest.mark.integration
class TestSlotSpoolDefaults:
    @pytest.mark.asyncio
    async def test_answers_for_the_hotend_this_slot_feeds(
        self, async_client: AsyncClient, assigned_spool, dual_nozzle_printer_state
    ):
        printer, _ = assigned_spool
        response = await async_client.get(URL.format(pid=printer.id, ams=0, tray=0))
        assert response.status_code == 200, response.text
        body = response.json()

        # AMS 0 feeds the LEFT hotend, which has the 0.2 fitted.
        assert body["extruder"] == LEFT
        assert body["nozzle_diameter"] == "0.2"
        # So the 0.2 preset override, not the spool's own X1C one...
        assert body["slicer_filament"] == "GFSA21"
        # ...and the profile calibrated on that hotend, not the other's.
        assert body["cali_idx"] == 16
        assert body["k_value"] == pytest.approx(0.018)
        assert body["profile_name"] == "PLA left"

    @pytest.mark.asyncio
    async def test_a_slot_with_no_spool_answers_nulls_not_404(
        self, async_client: AsyncClient, assigned_spool, dual_nozzle_printer_state
    ):
        """ "Nothing configured" is an ordinary answer -- the dialog falls back
        to what it did before rather than treating it as an error."""
        printer, _ = assigned_spool
        response = await async_client.get(URL.format(pid=printer.id, ams=1, tray=3))
        assert response.status_code == 200
        body = response.json()
        assert body["slicer_filament"] is None
        assert body["cali_idx"] is None
        # The nozzle is still resolved -- AMS 1 is the right hotend.
        assert body["extruder"] == RIGHT
        assert body["nozzle_diameter"] == "0.4"

    @pytest.mark.asyncio
    async def test_falls_back_to_the_spools_own_preset_without_an_override(
        self, async_client: AsyncClient, assigned_spool, dual_nozzle_printer_state, db_session
    ):
        from backend.app.models.spool_assignment import SpoolAssignment

        printer, spool = assigned_spool
        # Same spool in a slot on the RIGHT hotend, which has no 0.4 override.
        db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=1, tray_id=0))
        await db_session.commit()

        body = (await async_client.get(URL.format(pid=printer.id, ams=1, tray=0))).json()

        assert body["slicer_filament"] == "GFSA00"
        assert body["cali_idx"] == 15

    @pytest.mark.asyncio
    async def test_an_offline_printer_still_answers(self, async_client: AsyncClient, assigned_spool):
        """Opening the dialog on a disconnected printer must not 500 -- it just
        cannot say which hotend the slot feeds."""
        printer, _ = assigned_spool
        with patch("backend.app.api.routes.printers.printer_manager") as manager:
            manager.get_status = MagicMock(return_value=None)
            manager.get_model = MagicMock(return_value="H2D")
            response = await async_client.get(URL.format(pid=printer.id, ams=0, tray=0))

        assert response.status_code == 200
        body = response.json()
        assert body["extruder"] is None
        assert body["nozzle_diameter"] == "0.4"
