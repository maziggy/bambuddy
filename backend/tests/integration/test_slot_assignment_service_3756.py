"""The slot-assignment service works without a request (#3756).

``POST /inventory/assignments`` and ``POST /spoolman/inventory/slot-assignments``
call ``services.slot_assignment``, and their own tests cover what it does.
These call it the way code without a request does: no route, and the real
printer manager, websocket manager and helpers in place of the ones the
routes pass in.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.core.printer_scope import ALL_PRINTERS
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.ams_assignment_lock import ams_assignment_locks, spool_move_locks
from backend.app.services.slot_assignment import assign_local_spool, assign_spoolman_spool

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

SPOOLMAN_SPOOL = {
    "id": 10,
    "filament": {
        "id": 1,
        "name": "PLA Basic",
        "material": "PLA",
        "color_hex": "FF0000",
        "weight": 1000,
        "vendor": {"id": 1, "name": "Test Brand"},
    },
    "remaining_weight": 800.0,
    "used_weight": 200.0,
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "extra": {},
}


@pytest.fixture(autouse=True)
def _fresh_locks():
    ams_assignment_locks.clear()
    spool_move_locks.clear()
    yield
    ams_assignment_locks.clear()
    spool_move_locks.clear()


@pytest.fixture
def broadcast():
    """The real websocket manager's broadcast, which the service uses when none is passed."""
    with patch("backend.app.core.websocket.ws_manager.broadcast", AsyncMock()) as mock:
        yield mock


def _announced(broadcast: AsyncMock) -> list[tuple[int, int, int]]:
    return [
        (c.args[0]["printer_id"], c.args[0]["ams_id"], c.args[0]["tray_id"])
        for c in broadcast.await_args_list
        if c.args[0]["type"] == "spool_assignment_changed"
    ]


class TestBuiltInInventory:
    @pytest.fixture
    def offline(self):
        with (
            patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=None)),
            patch("backend.app.services.printer_manager.printer_manager.get_client", MagicMock(return_value=None)),
        ):
            yield

    async def _spool(self, db_session) -> Spool:
        spool = Spool(material="PLA", label_weight=1000, weight_used=0)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    async def test_assigns_and_announces_the_slot(self, db_session, printer_factory, offline, broadcast):
        printer = await printer_factory()
        spool = await self._spool(db_session)

        outcome = await assign_local_spool(
            db_session,
            spool_id=spool.id,
            printer_id=printer.id,
            ams_id=0,
            tray_id=2,
            move=False,
            printer_scope=ALL_PRINTERS,
            preset_user=None,
        )

        assert (outcome.assignment.spool_id, outcome.assignment.printer_id) == (spool.id, printer.id)
        assert outcome.assignment.spool.id == spool.id
        # The printer is offline, so nothing was pushed and the slot waits for it.
        assert outcome.configured is False
        assert outcome.pending_config is True
        assert outcome.printer_offline is True
        assert _announced(broadcast) == [(printer.id, 0, 2)]

    async def test_move_takes_the_spool_out_of_its_old_slot(self, db_session, printer_factory, offline, broadcast):
        printer = await printer_factory()
        spool = await self._spool(db_session)
        db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0))
        await db_session.commit()

        await assign_local_spool(
            db_session,
            spool_id=spool.id,
            printer_id=printer.id,
            ams_id=1,
            tray_id=3,
            move=True,
            printer_scope=ALL_PRINTERS,
            preset_user=None,
        )

        rows = (await db_session.execute(select(SpoolAssignment).execution_options(populate_existing=True))).scalars()
        assert {(r.spool_id, r.ams_id, r.tray_id) for r in rows} == {(spool.id, 1, 3)}
        assert _announced(broadcast) == [(printer.id, 0, 0), (printer.id, 1, 3)]


class TestSpoolmanInventory:
    @pytest.fixture
    def spoolman(self):
        client = MagicMock()
        client.get_spool = AsyncMock(return_value=SPOOLMAN_SPOOL)
        client.get_spools = AsyncMock(return_value=[])
        client.merge_spool_extra = AsyncMock(return_value={"id": 0, "extra": {}})
        return client

    @pytest.fixture
    def mqtt(self):
        """A connected printer, reached through the real printer manager."""
        client = MagicMock()
        with (
            patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=None)),
            patch("backend.app.services.printer_manager.printer_manager.get_client", MagicMock(return_value=client)),
            patch("backend.app.services.printer_manager.printer_manager.get_model", MagicMock(return_value="X1C")),
        ):
            yield client

    async def test_assigns_pushes_and_announces_the_slot(self, db_session, printer_factory, spoolman, mqtt, broadcast):
        printer = await printer_factory()

        outcome = await assign_spoolman_spool(
            db_session,
            spoolman,
            spoolman_spool_id=10,
            printer_id=printer.id,
            ams_id=0,
            tray_id=1,
            move=False,
            printer_scope=ALL_PRINTERS,
            current_user=None,
        )

        assert outcome.mapped["material"] == "PLA"
        assert outcome.configured is True
        assert outcome.printer_offline is False
        row = (await db_session.execute(select(SpoolmanSlotAssignment))).scalar_one()
        assert (row.spoolman_spool_id, row.printer_id, row.ams_id, row.tray_id) == (10, printer.id, 0, 1)
        assert row.pending_config is False
        mqtt.ams_set_filament_setting.assert_called_once()
        assert mqtt.ams_set_filament_setting.call_args.kwargs["tray_type"] == "PLA"
        assert _announced(broadcast) == [(printer.id, 0, 1)]

    async def test_move_takes_the_spool_out_of_its_old_slot(
        self, db_session, printer_factory, spoolman, mqtt, broadcast
    ):
        printer = await printer_factory()
        db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=10))
        await db_session.commit()

        await assign_spoolman_spool(
            db_session,
            spoolman,
            spoolman_spool_id=10,
            printer_id=printer.id,
            ams_id=1,
            tray_id=2,
            move=True,
            printer_scope=ALL_PRINTERS,
            current_user=None,
        )

        rows = (
            await db_session.execute(select(SpoolmanSlotAssignment).execution_options(populate_existing=True))
        ).scalars()
        assert {(r.spoolman_spool_id, r.ams_id, r.tray_id) for r in rows} == {(10, 1, 2)}
        assert _announced(broadcast) == [(printer.id, 0, 0), (printer.id, 1, 2)]
