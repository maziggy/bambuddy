"""What the Spoolman AMS syncs write to the slot table, and announce (SM-C3, SSA-2, SSA-3).

- The automatic pass read the slot table, then awaited Spoolman for every tray
  and wrote every synced slot back, unchanged ones included. A spool assigned
  by hand in between was overwritten with what the pass had read. It now writes
  only slots whose spool it changed, and only while they still hold what it read.
- The Sync buttons in Settings wrote slots without announcing them, refreshing
  the slot's preset or clearing its grace hold, as the automatic pass does.
- Both seeded a newly created spool's weight from the built-in inventory's
  slot assignments, which in Spoolman mode are left over from before the switch.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.spoolman import AMSTray
from backend.tests.integration.test_spoolman_ams_sync_broadcast import (
    _enable_spoolman,
    _run_ams_change,
    _slot_events,
    _tray,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _slots(db_session, printer_id) -> dict[tuple[int, int], int]:
    rows = (
        await db_session.execute(
            select(SpoolmanSlotAssignment)
            .where(SpoolmanSlotAssignment.printer_id == printer_id)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return {(row.ams_id, row.tray_id): row.spoolman_spool_id for row in rows}


async def _printer_with_slot(db_session, printer_factory, spool_id: int = 7) -> int:
    await _enable_spoolman(db_session)
    printer = await printer_factory()
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=spool_id))
    await db_session.commit()
    return printer.id


def _assigning_by_hand_meanwhile(db_session, printer_id, slot, spool_id, returns):
    """sync_ams_tray, during which the user assigns *spool_id* to *slot*."""

    async def sync(tray, *args, **kwargs):
        await db_session.execute(
            update(SpoolmanSlotAssignment)
            .where(
                SpoolmanSlotAssignment.printer_id == printer_id,
                SpoolmanSlotAssignment.ams_id == slot[0],
                SpoolmanSlotAssignment.tray_id == slot[1],
            )
            .values(spoolman_spool_id=spool_id)
        )
        await db_session.commit()
        return returns

    return AsyncMock(side_effect=sync)


AMS = [{"id": 0, "tray": [{"id": 0}, {"id": 1}]}]


@pytest.mark.parametrize("synced", [7, 8], ids=["unchanged", "changed"])
async def test_the_automatic_pass_keeps_a_spool_assigned_meanwhile(
    async_client: AsyncClient, db_session, printer_factory, synced
):
    printer_id = await _printer_with_slot(db_session, printer_factory)
    sync = _assigning_by_hand_meanwhile(db_session, printer_id, (0, 0), 9, {"id": synced})

    broadcast, _client = await _run_ams_change(printer_id, AMS[:1], parsed={(0, 0): _tray(0, 0)}, sync=sync)

    assert await _slots(db_session, printer_id) == {(0, 0): 9}
    assert (0, 0) not in _slot_events(broadcast), "nothing the pass wrote changed"


async def test_the_automatic_pass_announces_no_slot_it_left_as_it_was(
    async_client: AsyncClient, db_session, printer_factory
):
    printer_id = await _printer_with_slot(db_session, printer_factory)

    broadcast, _client = await _run_ams_change(
        printer_id, AMS[:1], parsed={(0, 0): _tray(0, 0)}, sync=AsyncMock(return_value={"id": 7})
    )

    assert await _slots(db_session, printer_id) == {(0, 0): 7}
    assert (0, 0) not in _slot_events(broadcast)


async def test_the_automatic_pass_still_writes_and_announces_what_it_changed(
    async_client: AsyncClient, db_session, printer_factory
):
    printer_id = await _printer_with_slot(db_session, printer_factory)

    broadcast, _client = await _run_ams_change(
        printer_id, AMS[:1], parsed={(0, 0): _tray(0, 0)}, sync=AsyncMock(return_value={"id": 8})
    )

    assert await _slots(db_session, printer_id) == {(0, 0): 8}
    assert _slot_events(broadcast) == {(0, 0)}


# ── The Sync buttons ─────────────────────────────────────────────────────────

PATHS = ["/api/v1/spoolman/sync/{printer_id}", "/api/v1/spoolman/sync-all"]


def _parsed_tray(ams_id, tray_data):
    return AMSTray(
        ams_id=ams_id,
        tray_id=int(tray_data.get("id", 0)),
        tray_type="PLA",
        tray_sub_brands="PLA Matte",
        tray_color="042F56FF",
        remain=-1,
        tag_uid="7757EF0100000100",
        tray_uuid="EB335968299543078925C71D83DA3864",
        tray_info_idx="GFA01",
        tray_weight=1000,
    )


async def _manual_sync(async_client, printer_id, path, synced: int, *, trays=None, sync=None):
    trays = trays or [{"id": 0, "tray_type": "PLA"}]
    state = SimpleNamespace(state="IDLE", raw_data={"ams": [{"id": 0, "tray": trays}]})
    client = MagicMock()
    client.health_check = AsyncMock(return_value=True)
    client.get_spools = AsyncMock(return_value=[])
    # A tray without a type reads empty.
    client.parse_ams_tray = MagicMock(
        side_effect=lambda ams_id, tray: _parsed_tray(ams_id, tray) if tray.get("tray_type") else None
    )
    client.sync_ams_tray = sync or AsyncMock(return_value={"id": synced})
    preset = AsyncMock()
    with (
        patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
        patch("backend.app.api.routes.spoolman.printer_manager") as pm,
        patch("backend.app.services.spoolman_slot_sync.ws_manager") as ws,
        patch("backend.app.services.slot_preset_writer.upsert_slot_preset_for_spoolman_spool", preset),
        patch("backend.app.services.slot_unlink_grace.forget_slot") as forget,
    ):
        ws.broadcast = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_all_statuses = MagicMock(return_value={printer_id: state})
        response = await async_client.post(path.format(printer_id=printer_id))
    assert response.status_code == 200, response.text
    return client, ws.broadcast, preset, forget


@pytest.mark.parametrize("path", PATHS)
async def test_a_sync_button_announces_a_changed_slot(async_client: AsyncClient, db_session, printer_factory, path):
    printer_id = await _printer_with_slot(db_session, printer_factory)

    _client, broadcast, preset, forget = await _manual_sync(async_client, printer_id, path, synced=8)

    assert await _slots(db_session, printer_id) == {(0, 0): 8}
    assert _slot_events(broadcast) == {(0, 0)}
    assert preset.await_args.kwargs["spoolman_spool"] == {"id": 8}
    forget.assert_called_once_with(printer_id, 0, 0)


@pytest.mark.parametrize("path", PATHS)
async def test_a_sync_button_does_not_seed_a_spool_from_the_built_in_inventory(
    async_client: AsyncClient, db_session, printer_factory, path
):
    """A slot assignment left from before the switch to Spoolman: 880 g of
    1000 used, nothing to do with the spool now in the slot."""
    printer_id = await _printer_with_slot(db_session, printer_factory)
    stale = Spool(material="PLA", label_weight=1000, weight_used=880)
    db_session.add(stale)
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=stale.id, printer_id=printer_id, ams_id=0, tray_id=0))
    await db_session.commit()

    client, *_ = await _manual_sync(async_client, printer_id, path, synced=8)

    assert client.sync_ams_tray.await_args.kwargs.get("inventory_remaining") is None


@pytest.mark.parametrize("path", PATHS)
async def test_a_sync_button_keeps_a_slot_that_was_filled_meanwhile(
    async_client: AsyncClient, db_session, printer_factory, path
):
    """Slot 0 read empty, so the sync would unlink spool 7; by then it holds 9."""
    printer_id = await _printer_with_slot(db_session, printer_factory)
    sync = _assigning_by_hand_meanwhile(db_session, printer_id, (0, 0), 9, {"id": 5})

    await _manual_sync(
        async_client, printer_id, path, synced=5, trays=[{"id": 0}, {"id": 1, "tray_type": "PLA"}], sync=sync
    )

    assert await _slots(db_session, printer_id) == {(0, 0): 9, (0, 1): 5}


@pytest.mark.parametrize("path", PATHS)
async def test_a_sync_button_still_unlinks_a_slot_that_reads_empty(
    async_client: AsyncClient, db_session, printer_factory, path
):
    printer_id = await _printer_with_slot(db_session, printer_factory)

    _client, broadcast, *_ = await _manual_sync(async_client, printer_id, path, synced=5, trays=[{"id": 0}])

    assert await _slots(db_session, printer_id) == {}
    assert _slot_events(broadcast) == {(0, 0)}


async def test_the_automatic_pass_keeps_a_spool_assigned_to_a_slot_it_read_empty(
    async_client: AsyncClient, db_session, printer_factory
):
    await _enable_spoolman(db_session)
    printer_id = (await printer_factory()).id

    async def sync(tray, *args, **kwargs):
        db_session.add(SpoolmanSlotAssignment(printer_id=printer_id, ams_id=0, tray_id=0, spoolman_spool_id=9))
        await db_session.commit()
        return {"id": 8}

    await _run_ams_change(printer_id, AMS[:1], parsed={(0, 0): _tray(0, 0)}, sync=AsyncMock(side_effect=sync))

    assert await _slots(db_session, printer_id) == {(0, 0): 9}


async def test_without_the_slot_table_as_read_the_syncs_write_as_before(db_session, printer_factory):
    """Its load failed: nothing to compare against, so a swap and a removal are
    written as before rather than dropped."""
    from backend.app.services.spoolman_slot_sync import write_slot_results

    printer_id = await _printer_with_slot(db_session, printer_factory, spool_id=10)
    db_session.add(SpoolmanSlotAssignment(printer_id=printer_id, ams_id=0, tray_id=1, spoolman_spool_id=11))
    await db_session.commit()

    with patch("backend.app.services.spoolman_slot_sync.ws_manager") as ws:
        ws.broadcast = AsyncMock()
        changed = await write_slot_results(db_session, None, [(printer_id, 0, 0, 99)], [(printer_id, 0, 1)])

    assert await _slots(db_session, printer_id) == {(0, 0): 99}
    assert sorted(changed) == [(printer_id, 0, 0), (printer_id, 0, 1)]
