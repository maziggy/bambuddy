"""Spoolman's AMS sync has to tell the browser the slot changed.

Swap a spool and the AMS slot card keeps showing the *previous* spool's preset
name. The card reads ``slot_preset_mappings.preset_name`` ahead of the live
``tray_info_idx``, so a cached row wins over correct data pushed over the
socket — and everything else on the card rides the status push, which is why it
surfaces as one wrong line rather than an obviously stale card.

Built-in inventory raised ``spool_auto_assigned`` for this (the frontend simply
forgot to invalidate ``slotPresets`` on it). This loop raised nothing at all,
even though it rewrites the very same row through
``upsert_slot_preset_for_spoolman_spool`` — so in Spoolman mode there was no
event to hang an invalidation on, and the stale name stood until an unrelated
refetch.

An emptied slot counts: its ``spoolman_slot_assignments`` row is deleted here,
and a card still drawing the removed spool is the same defect.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment


def _status(ams_data):
    status = MagicMock()
    status.raw_data = {"ams": ams_data, "vt_tray": []}
    status.gcode_state = "IDLE"
    return status


def _tray(ams_id: int, tray_id: int):
    """What ``client.parse_ams_tray`` hands back for an occupied slot."""
    tray = MagicMock()
    tray.ams_id = ams_id
    tray.tray_id = tray_id
    tray.tray_uuid = "EB335968299543078925C71D83DA3864"
    tray.tag_uid = "7757EF0100000100"
    tray.tray_info_idx = "GFA01"
    tray.tray_type = "PLA"
    tray.tray_sub_brands = "PLA Matte"
    tray.tray_color = "042F56FF"
    return tray


async def _run_ams_change(printer_id: int, ams_data: list, *, parsed):
    """Drive ``on_ams_change`` with Spoolman standing in for the real server.

    ``parsed`` maps (ams_id, tray_id) to a parsed tray or ``None`` (empty slot),
    which is the only thing that decides whether the sync treats the slot as
    occupied or cleared.
    """
    from backend.app.main import on_ams_change

    spoolman = MagicMock()
    spoolman.health_check = AsyncMock(return_value=True)
    spoolman.get_spools = AsyncMock(return_value=[])
    spoolman.parse_ams_tray = MagicMock(
        side_effect=lambda ams_id, tray_data: parsed.get((ams_id, int(tray_data.get("id", 0))))
    )
    spoolman.sync_ams_tray = AsyncMock(return_value={"id": 4242})

    status = _status(ams_data)
    with (
        patch("backend.app.main.printer_manager") as pm_main,
        patch("backend.app.services.printer_manager.printer_manager") as pm_inv,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.get_spoolman_client", AsyncMock(return_value=spoolman)),
        patch(
            "backend.app.services.slot_preset_writer.upsert_slot_preset_for_spoolman_spool",
            AsyncMock(),
        ),
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
        return ws.broadcast, spoolman


def _slot_events(broadcast) -> set[tuple[int, int]]:
    """(ams_id, tray_id) of every assignment-changed event that went out."""
    return {
        (call.args[0]["ams_id"], call.args[0]["tray_id"])
        for call in broadcast.call_args_list
        if call.args and call.args[0].get("type") == "spool_assignment_changed"
    }


async def _enable_spoolman(db: AsyncSession) -> None:
    db.add(Settings(key="spoolman_enabled", value="true"))
    db.add(Settings(key="spoolman_url", value="http://spoolman.invalid:7912"))
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_synced_slot_is_broadcast(async_client: AsyncClient, printer_factory, db_session: AsyncSession):
    printer = await printer_factory(name="H2C")
    await _enable_spoolman(db_session)

    broadcast, spoolman = await _run_ams_change(
        printer.id,
        [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "042F56FF", "state": 11}]}],
        parsed={(0, 0): _tray(0, 0)},
    )

    spoolman.sync_ams_tray.assert_awaited()
    assert (0, 0) in _slot_events(broadcast)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_emptied_slot_is_broadcast(async_client: AsyncClient, printer_factory, db_session: AsyncSession):
    """The row is deleted here; a card still drawing the removed spool is the
    same bug seen from the other side."""
    printer = await printer_factory(name="H2C")
    await _enable_spoolman(db_session)
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=1, spoolman_spool_id=7))
    await db_session.commit()

    broadcast, _ = await _run_ams_change(
        printer.id,
        [{"id": 0, "tray": [{"id": 1}]}],
        parsed={(0, 1): None},
    )

    assert (0, 1) in _slot_events(broadcast)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_nothing_is_broadcast_when_no_slot_changed(
    async_client: AsyncClient, printer_factory, db_session: AsyncSession
):
    """A push that changes no slot must stay quiet — this runs on every AMS
    message, and an event per push would invalidate the browser's caches
    continuously."""
    printer = await printer_factory(name="H2C")
    await _enable_spoolman(db_session)

    broadcast, spoolman = await _run_ams_change(
        printer.id,
        [{"id": 0, "tray": []}],
        parsed={},
    )

    # Guards the assertion below against passing because the sync bailed out
    # early on a mis-set fixture rather than because it found nothing to say.
    spoolman.get_spools.assert_awaited()
    assert _slot_events(broadcast) == set()
