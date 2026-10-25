"""Assigning a spool while the printer prints logs the swap (#3325).

Both inventory backends' assignment routes note the new spool and the layer the
printer is on, so completion can split the print between it and the spool the
tray started with. Outside a print nothing is logged.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.active_print_session import ActivePrintSession
from backend.app.models.printer import Printer
from backend.app.models.spool import Spool
from backend.app.services import spool_swaps


def _status(layer_num: int, log: list):
    status = MagicMock()
    status.raw_data = {"ams": {"ams": []}, "vt_tray": [{"id": "254", "tray_type": "PLA"}]}
    status.nozzles = [MagicMock(nozzle_diameter="0.4")]
    status.ams_extruder_map = None
    status.kprofiles = []
    status.layer_num = layer_num
    status.spool_change_log = log
    return status


def _mqtt():
    client = MagicMock()
    client.ams_set_filament_setting.return_value = True
    client.extrusion_cali_sel.return_value = True
    return client


async def _printer(db_session, serial: str, printing: bool, start_log: list | None = None) -> Printer:
    printer = Printer(name="P2S", serial_number=serial, ip_address="192.168.1.80", access_code="12345678", model="P2S")
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    if printing:
        db_session.add(ActivePrintSession(printer_id=printer.id, started_at=datetime.now(), spool_change_log=start_log))
        await db_session.commit()
    return printer


class TestBuiltInInventory:
    async def _assign(self, async_client, db_session, printing: bool):
        old = Spool(material="PLA", rgba="000000ff", label_weight=1000, weight_used=0)
        new = Spool(material="PLA", rgba="000000ff", label_weight=1000, weight_used=0)
        db_session.add_all([old, new])
        await db_session.commit()
        start = [spool_swaps.entry(spool_swaps.INVENTORY, 255, 0, old.id, 0, start=True)]
        printer = await _printer(db_session, f"P2S3325{int(printing)}", printing, start)
        status = _status(57, list(start) if printing else [])
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.get_client.return_value = _mqtt()
            pm.get_status.return_value = status
            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": new.id, "printer_id": printer.id, "ams_id": 255, "tray_id": 0},
            )
        assert response.status_code == 200
        return printer, status, old, new

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_swap_mid_print_is_logged(self, async_client: AsyncClient, db_session: AsyncSession):
        printer, status, old, new = await self._assign(async_client, db_session, printing=True)

        assert spool_swaps.segments(status.spool_change_log, spool_swaps.INVENTORY, 255, 0) == [
            (old.id, 0),
            (new.id, 57),
        ]
        row = await db_session.get(ActivePrintSession, printer.id)
        await db_session.refresh(row)
        assert row.spool_change_log == status.spool_change_log

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_print_no_log(self, async_client: AsyncClient, db_session: AsyncSession):
        _, status, _, _ = await self._assign(async_client, db_session, printing=False)

        assert status.spool_change_log == []


class TestSpoolman:
    @pytest.fixture
    async def settings(self, db_session):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
        await db_session.commit()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_swap_mid_print_is_logged(self, async_client: AsyncClient, db_session: AsyncSession, settings):
        start = [spool_swaps.entry(spool_swaps.SPOOLMAN, 255, 0, 10, 0, start=True)]
        printer = await _printer(db_session, "P2S3325SM", True, start)
        status = _status(80, list(start))
        spool = {
            "id": 11,
            "filament": {"id": 1, "name": "Black", "material": "PLA", "color_hex": "000000", "weight": 1000},
            "remaining_weight": 1000.0,
            "used_weight": 0.0,
            "archived": False,
            "extra": {},
        }
        client = MagicMock()
        client.has_tag_api = AsyncMock(return_value=False)
        client.add_native_tags = AsyncMock(return_value=0)
        client.unlink_all_native_tags = AsyncMock()
        client.base_url = "http://localhost:7912"
        client.health_check = AsyncMock(return_value=True)
        client.get_spool = AsyncMock(return_value=spool)
        client.get_spools = AsyncMock(return_value=[spool])
        client.merge_spool_extra = AsyncMock(return_value=spool)
        with (
            patch("backend.app.api.routes.spoolman_inventory.printer_manager") as route_pm,
            patch("backend.app.services.printer_manager.printer_manager") as pm,
            patch("backend.app.api.routes.spoolman_inventory.get_spoolman_client", AsyncMock(return_value=client)),
        ):
            route_pm.get_client.return_value = _mqtt()
            route_pm.get_status.return_value = status
            pm.get_status.return_value = status
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={"spoolman_spool_id": 11, "printer_id": printer.id, "ams_id": 255, "tray_id": 0},
            )

        assert response.status_code == 200
        assert spool_swaps.segments(status.spool_change_log, spool_swaps.SPOOLMAN, 255, 0) == [(10, 0), (11, 80)]
