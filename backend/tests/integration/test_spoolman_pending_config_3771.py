"""A Spoolman slot assigned while the printer is offline is configured later (#3771).

The built-in inventory replays a slot's configuration on the first AMS report
it can be sent on. Spoolman mode had nothing like it: an assign the printer
never got stayed unconfigured. The slot row is now marked ``pending_config``,
the replay sends it once the printer reports the slot loaded, and anything
that puts another spool in the slot ends it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.spoolman_slot_sync import write_slot_results
from backend.tests.integration.test_spoolman_slot_assignment_mqtt import (  # noqa: F401
    SAMPLE_SPOOL,
    mock_spoolman_client,
    slot_settings,
    test_printer,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

LOADED = {"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}


async def _row(db_session, printer_id) -> SpoolmanSlotAssignment:
    return (
        await db_session.execute(
            select(SpoolmanSlotAssignment)
            .where(SpoolmanSlotAssignment.printer_id == printer_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _pending_row(db_session, printer_id, spool_id: int = 10) -> SpoolmanSlotAssignment:
    row = SpoolmanSlotAssignment(
        printer_id=printer_id, ams_id=0, tray_id=0, spoolman_spool_id=spool_id, pending_config=True
    )
    db_session.add(row)
    await db_session.commit()
    return row


class TestTheAssign:
    @pytest.mark.parametrize("connected", [False, True], ids=["offline", "online"])
    async def test_it_marks_what_did_not_reach_the_printer(
        self, async_client: AsyncClient, db_session, slot_settings, test_printer, mock_spoolman_client, connected
    ):
        client = MagicMock()
        client.ams_set_filament_setting.return_value = connected
        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm:
            pm.get_client = MagicMock(return_value=client)
            pm.get_status.return_value = None
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 10  # still the spool
        assert (body["configured"], body["pending_config"], body["printer_offline"]) == (
            connected,
            not connected,
            not connected,
        )
        assert (await _row(db_session, test_printer.id)).pending_config is (not connected)

    async def test_a_printer_without_a_client_is_pending_from_the_upsert(
        self, async_client: AsyncClient, db_session, slot_settings, test_printer, mock_spoolman_client
    ):
        # Decided before the upsert, so no second write after the publish.
        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm:
            pm.get_client = MagicMock(return_value=None)
            pm.get_status.return_value = None
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.json()["printer_offline"] is True
        assert (await _row(db_session, test_printer.id)).pending_config is True

    async def test_a_printer_that_connected_meanwhile_is_not_pending(
        self, async_client: AsyncClient, db_session, slot_settings, test_printer, mock_spoolman_client
    ):
        client = MagicMock()
        client.state.connected = False  # offline when the slot is written ...
        client.ams_set_filament_setting.return_value = True  # ... and back for the publish
        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm:
            pm.get_client = MagicMock(return_value=client)
            pm.get_status.return_value = None
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.json()["printer_offline"] is False
        assert (await _row(db_session, test_printer.id)).pending_config is False

    async def test_assigning_again_ends_the_pending_one(
        self, async_client: AsyncClient, db_session, slot_settings, test_printer, mock_spoolman_client
    ):
        await _pending_row(db_session, test_printer.id, spool_id=11)
        client = MagicMock()
        client.ams_set_filament_setting.return_value = True
        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm:
            pm.get_client = MagicMock(return_value=client)
            pm.get_status.return_value = None
            await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
            )

        row = await _row(db_session, test_printer.id)
        assert (row.spoolman_spool_id, row.pending_config) == (10, False)


class TestOtherWriters:
    @pytest.fixture(autouse=True)
    def _quiet(self):
        with (
            patch("backend.app.services.spoolman_slot_sync._announce", AsyncMock()),
            patch("backend.app.services.spoolman_slot_sync._release_elsewhere", AsyncMock()),
        ):
            yield

    @pytest.mark.parametrize("read", [None, {}], ids=["unconditional", "compared"])
    async def test_another_spool_ends_it(self, db_session, printer_factory, read):
        printer = await printer_factory()
        await _pending_row(db_session, printer.id, spool_id=10)
        read = None if read is None else {(printer.id, 0, 0): 10}

        await write_slot_results(db_session, read, [(printer.id, 0, 0, 12)], [])

        row = await _row(db_session, printer.id)
        assert (row.spoolman_spool_id, row.pending_config) == (12, False)

    async def test_the_same_spool_keeps_it(self, db_session, printer_factory):
        printer = await printer_factory()
        await _pending_row(db_session, printer.id, spool_id=10)

        await write_slot_results(db_session, None, [(printer.id, 0, 0, 10)], [])

        assert (await _row(db_session, printer.id)).pending_config is True


class TestTheReplay:
    @pytest.fixture(autouse=True)
    def _app_session(self, async_client):
        """main's async_session on the test database, as async_client sets it up."""

    async def _replay(self, db_session, printer_id, *, apply, connected=True, tray=LOADED, feeds_print=False):
        from backend.app.main import _replay_spoolman_pending_configs
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        await db_session.commit()
        spoolman = MagicMock()
        spoolman.get_spool = AsyncMock(return_value=SAMPLE_SPOOL)
        status = SimpleNamespace(connected=connected, raw_data={})
        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._slot_feeds_print", return_value=feeds_print),
            patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=spoolman)),
            patch("backend.app.api.routes.spoolman_inventory.apply_spoolman_spool_to_slot", apply),
        ):
            pm.get_status.return_value = status
            await _replay_spoolman_pending_configs(printer_id, [{"id": 0, "tray": [tray]}], feeds_print)
        return await _row(db_session, printer_id)

    async def test_a_loaded_slot_is_configured(self, db_session, printer_factory):
        printer = await printer_factory()
        await _pending_row(db_session, printer.id)
        apply = AsyncMock(return_value=True)

        row = await self._replay(db_session, printer.id, apply=apply)

        apply.assert_awaited_once()
        assert apply.await_args.kwargs["spoolman_spool_id"] == 10
        assert apply.await_args.kwargs["mapped"]["material"] == "PLA"
        assert row.pending_config is False

    async def test_still_not_sent_stays_pending(self, db_session, printer_factory):
        printer = await printer_factory()
        await _pending_row(db_session, printer.id)

        row = await self._replay(db_session, printer.id, apply=AsyncMock(return_value=False))

        assert row.pending_config is True

    async def test_an_attempt_that_raised_ends_it(self, db_session, printer_factory):
        printer = await printer_factory()
        await _pending_row(db_session, printer.id)

        row = await self._replay(db_session, printer.id, apply=AsyncMock(side_effect=RuntimeError("boom")))

        assert row.pending_config is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"connected": False},
            {"tray": {"id": 0, "tray_type": "", "tray_color": "", "state": 9}},
            {"feeds_print": True},
        ],
        ids=["offline", "empty-slot", "feeding-the-print"],
    )
    async def test_nothing_is_sent(self, db_session, printer_factory, kwargs):
        printer = await printer_factory()
        await _pending_row(db_session, printer.id)
        apply = AsyncMock(return_value=True)

        row = await self._replay(db_session, printer.id, apply=apply, **kwargs)

        apply.assert_not_awaited()
        assert row.pending_config is True

    async def test_every_ams_change_runs_it(self, db_session, printer_factory):
        from backend.tests.integration.test_spoolman_ams_sync_broadcast import _enable_spoolman, _run_ams_change

        await _enable_spoolman(db_session)
        printer = await printer_factory()
        ams_data = [{"id": 0, "tray": [LOADED]}]
        replay = AsyncMock()

        with patch("backend.app.main._replay_spoolman_pending_configs", replay):
            await _run_ams_change(printer.id, ams_data, parsed={})

        replay.assert_awaited_once()
        assert replay.await_args.args[:2] == (printer.id, ams_data)
