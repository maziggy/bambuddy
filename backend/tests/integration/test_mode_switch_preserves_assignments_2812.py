"""Preserved built-in assignments must stay preserved (#2812).

The mode toggle no longer deletes them. That is only worth anything if the rest
of the app leaves them alone while Spoolman mode is active — otherwise they are
destroyed just as completely, only more slowly.

The auto-unlink pass in ``on_ams_change`` is the one that matters: it ends in
``db.delete`` for every assignment whose slot no longer matches the fingerprint
it recorded, which is precisely what happens once the user starts loading
different filament under the other mode.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment


def _status(ams_data):
    status = MagicMock()
    status.raw_data = {"ams": ams_data, "vt_tray": []}
    status.gcode_state = "IDLE"
    return status


async def _run_ams_change(printer_id: int, ams_data: list):
    from backend.app.main import on_ams_change

    status = _status(ams_data)
    with (
        patch("backend.app.main.printer_manager") as pm_main,
        patch("backend.app.services.printer_manager.printer_manager") as pm_inv,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
    ):
        pm_main.get_printer.return_value = MagicMock(name="P", serial_number="SER")
        pm_main.get_status.return_value = status
        pm_main.get_client.return_value = MagicMock()
        pm_main.get_model.return_value = "X1C"
        pm_inv.get_status.return_value = status
        pm_inv.get_client.return_value = MagicMock()
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()
        await on_ams_change(printer_id, ams_data)


class TestAutoUnlinkRespectsTheActiveMode:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_mode_does_not_unlink_built_in_assignments(
        self, async_client: AsyncClient, printer_factory, db_session: AsyncSession
    ):
        """The slot now holds something else entirely — a stale fingerprint by
        every measure. In built-in mode that is a genuine unlink. In Spoolman
        mode these rows are the user's preserved configuration, waiting for
        them to switch back, and nothing here is entitled to delete them."""
        printer = await printer_factory(name="P1S")
        spool = Spool(material="PLA", rgba="FF0000FF")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=0,
                fingerprint_color="FF0000FF",
                fingerprint_type="PLA",
            )
        )
        await db_session.commit()

        await _run_ams_change(
            printer.id,
            [{"id": 0, "tray": [{"id": 0, "tray_type": "PETG", "tray_color": "00FF00FF", "state": 11}]}],
        )

        rows = (
            (await db_session.execute(select(SpoolAssignment).where(SpoolAssignment.printer_id == printer.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_built_in_mode_still_unlinks_a_stale_assignment(
        self, async_client: AsyncClient, printer_factory, db_session: AsyncSession
    ):
        """The guard must not switch the feature off for the mode that owns it."""
        printer = await printer_factory(name="P1S")
        spool = Spool(material="PLA", rgba="FF0000FF")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(Settings(key="spoolman_enabled", value="false"))
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=0,
                fingerprint_color="FF0000FF",
                fingerprint_type="PLA",
            )
        )
        await db_session.commit()

        await _run_ams_change(
            printer.id,
            [{"id": 0, "tray": [{"id": 0, "tray_type": "PETG", "tray_color": "00FF00FF", "state": 11}]}],
        )

        rows = (
            (await db_session.execute(select(SpoolAssignment).where(SpoolAssignment.printer_id == printer.id)))
            .scalars()
            .all()
        )
        assert rows == []
