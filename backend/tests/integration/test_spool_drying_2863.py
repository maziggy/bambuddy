"""Each spool remembers when it was last dried (#2863).

An AMS drying cycle that ran at least half its length stamps every spool
assigned to that AMS, in whichever inventory holds the slot assignments. The
date can also be set by hand, which clears the temperature and hours that
described the AMS cycle.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.bambu_mqtt import DryingCycleEnd
from backend.app.services.spool_drying import record_drying_cycle

FULL_CYCLE = DryingCycleEnd(
    ams_id=0, remaining_minutes=1, peak_minutes=480, start_seen=True, target_temp=55, target_hours=8
)
SHORT_CYCLE = DryingCycleEnd(
    ams_id=0, remaining_minutes=470, peak_minutes=480, start_seen=True, target_temp=55, target_hours=8
)


async def _assign(db: AsyncSession, printer_id: int, ams_id: int, tray_id: int) -> Spool:
    spool = Spool(material="PLA", rgba="FF0000FF")
    db.add(spool)
    await db.flush()
    db.add(SpoolAssignment(spool_id=spool.id, printer_id=printer_id, ams_id=ams_id, tray_id=tray_id))
    await db.commit()
    return spool


@pytest.fixture
def no_broadcast():
    with patch("backend.app.core.websocket.ws_manager.broadcast", AsyncMock()) as broadcast:
        yield broadcast


class TestBuiltInInventory:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_cycle_stamps_only_the_spools_in_that_ams(self, printer_factory, db_session, no_broadcast):
        printer = await printer_factory(name="H2D")
        in_ams = [await _assign(db_session, printer.id, 0, tray) for tray in (0, 1)]
        other_ams = await _assign(db_session, printer.id, 1, 0)

        assert await record_drying_cycle(db_session, printer.id, FULL_CYCLE) == 2

        for spool in in_ams:
            await db_session.refresh(spool)
            assert spool.last_dried_at is not None
            assert spool.last_dried_temp == 55
            assert spool.last_dried_hours == 8.0
        await db_session.refresh(other_ams)
        assert other_ams.last_dried_at is None
        no_broadcast.assert_awaited_once_with({"type": "inventory_changed"})

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cycle_stopped_early_keeps_the_earlier_drying(self, printer_factory, db_session, no_broadcast):
        printer = await printer_factory(name="H2D")
        spool = await _assign(db_session, printer.id, 0, 0)
        earlier = datetime(2026, 9, 1, 12, 0)
        spool.last_dried_at = earlier
        spool.last_dried_temp = 65
        spool.last_dried_hours = 12.0
        await db_session.commit()

        assert await record_drying_cycle(db_session, printer.id, SHORT_CYCLE) == 0

        await db_session.refresh(spool)
        assert spool.last_dried_at == earlier
        assert spool.last_dried_temp == 65
        no_broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_mode_leaves_built_in_rows_alone(self, printer_factory, db_session, no_broadcast):
        """The built-in assignments are preserved while Spoolman is active
        (#2812); they do not describe what is in the AMS."""
        printer = await printer_factory(name="H2D")
        spool = await _assign(db_session, printer.id, 0, 0)
        db_session.add(Settings(key="spoolman_enabled", value="true"))
        await db_session.commit()

        with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=None)):
            await record_drying_cycle(db_session, printer.id, FULL_CYCLE)

        await db_session.refresh(spool)
        assert spool.last_dried_at is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hand_set_date_clears_temperature_and_hours(self, async_client: AsyncClient, db_session):
        spool = Spool(
            material="PLA",
            last_dried_at=datetime(2026, 9, 1, 12, 0),
            last_dried_temp=55,
            last_dried_hours=8.0,
        )
        db_session.add(spool)
        await db_session.commit()

        response = await async_client.patch(
            f"/api/v1/inventory/spools/{spool.id}", json={"last_dried_at": "2026-10-06T14:30:00+02:00"}
        )

        assert response.status_code == 200
        body = response.json()
        # Stored as UTC, the way every naive DateTime column holds it.
        assert body["last_dried_at"].startswith("2026-10-06T12:30:00")
        assert body["last_dried_temp"] is None
        assert body["last_dried_hours"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_null_clears_the_date(self, async_client: AsyncClient, db_session):
        spool = Spool(material="PLA", last_dried_at=datetime(2026, 9, 1, 12, 0), last_dried_temp=55)
        db_session.add(spool)
        await db_session.commit()

        response = await async_client.patch(f"/api/v1/inventory/spools/{spool.id}", json={"last_dried_at": None})

        assert response.status_code == 200
        assert response.json()["last_dried_at"] is None
        assert response.json()["last_dried_temp"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unrelated_edit_keeps_the_record(self, async_client: AsyncClient, db_session):
        spool = Spool(material="PLA", last_dried_at=datetime(2026, 9, 1, 12, 0), last_dried_temp=55)
        db_session.add(spool)
        await db_session.commit()

        response = await async_client.patch(f"/api/v1/inventory/spools/{spool.id}", json={"note": "shelf 2"})

        assert response.status_code == 200
        assert response.json()["last_dried_at"].startswith("2026-09-01T12:00:00")
        assert response.json()["last_dried_temp"] == 55

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_set_date_clears_temperature_and_hours(self, async_client: AsyncClient, db_session):
        spools = [Spool(material="PLA", last_dried_temp=55, last_dried_hours=8.0) for _ in range(2)]
        db_session.add_all(spools)
        await db_session.commit()

        response = await async_client.post(
            "/api/v1/inventory/spools/bulk-update",
            json={"ids": [s.id for s in spools], "update": {"last_dried_at": "2026-10-06T12:30:00Z"}},
        )

        assert response.status_code == 200
        for spool in spools:
            await db_session.refresh(spool)
            assert spool.last_dried_at == datetime(2026, 10, 6, 12, 30)
            assert spool.last_dried_temp is None
            assert spool.last_dried_hours is None


class TestSpoolmanInventory:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_cycle_writes_the_extra_fields(self, printer_factory, db_session, no_broadcast):
        printer = await printer_factory(name="H2D")
        db_session.add(Settings(key="spoolman_enabled", value="true"))
        for tray, spool_id in ((0, 11), (1, 12)):
            db_session.add(
                SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=tray, spoolman_spool_id=spool_id)
            )
        db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=1, tray_id=0, spoolman_spool_id=13))
        await db_session.commit()
        client = MagicMock()
        client.merge_spool_extra = AsyncMock(return_value={})

        with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
            assert await record_drying_cycle(db_session, printer.id, FULL_CYCLE) == 2

        assert [c.args[0] for c in client.merge_spool_extra.call_args_list] == [11, 12]
        fields = client.merge_spool_extra.call_args.args[1]
        assert json.loads(fields["bambu_last_dried_temp"]) == "55"
        assert json.loads(fields["bambu_last_dried_hours"]) == "8.0"
        assert datetime.fromisoformat(json.loads(fields["bambu_last_dried_at"]))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_one_failing_spool_does_not_skip_the_rest(self, printer_factory, db_session, no_broadcast):
        printer = await printer_factory(name="H2D")
        db_session.add(Settings(key="spoolman_enabled", value="true"))
        for tray, spool_id in ((0, 11), (1, 12)):
            db_session.add(
                SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=tray, spoolman_spool_id=spool_id)
            )
        await db_session.commit()
        client = MagicMock()
        client.merge_spool_extra = AsyncMock(side_effect=[RuntimeError("Spoolman down"), {}])

        with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
            assert await record_drying_cycle(db_session, printer.id, FULL_CYCLE) == 1

        assert client.merge_spool_extra.await_count == 2

    def test_mapping_reads_the_extra_fields(self):
        spool = {
            "id": 5,
            "filament": {"id": 1, "name": "PLA", "material": "PLA"},
            "extra": {
                "bambu_last_dried_at": json.dumps("2026-10-06T12:30:00"),
                "bambu_last_dried_temp": json.dumps("55"),
                "bambu_last_dried_hours": json.dumps("7.5"),
            },
        }
        mapped = _map_spoolman_spool(spool)
        assert mapped["last_dried_at"] == "2026-10-06T12:30:00"
        assert mapped["last_dried_temp"] == 55
        assert mapped["last_dried_hours"] == 7.5

    def test_mapping_reads_cleared_and_absent_fields_as_unknown(self):
        base = {"id": 5, "filament": {"id": 1, "name": "PLA", "material": "PLA"}}
        cleared = {
            **base,
            "extra": {
                "bambu_last_dried_at": json.dumps(""),
                "bambu_last_dried_temp": json.dumps(""),
                "bambu_last_dried_hours": json.dumps(""),
            },
        }
        for spool in (base, cleared):
            mapped = _map_spoolman_spool(spool)
            assert mapped["last_dried_at"] is None
            assert mapped["last_dried_temp"] is None
            assert mapped["last_dried_hours"] is None
