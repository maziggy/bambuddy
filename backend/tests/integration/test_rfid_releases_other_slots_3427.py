"""A tag read in a slot takes the spool out of its other slots (#3427).

A spool assigned by hand to printer A's empty slot, then loaded into printer B,
stayed assigned to both once B read its tag: A's slot was configured from, and
charged to, a spool sitting in B. A manual move already took a spool out of its
other slots (#1574); the RFID auto-assign and the Spoolman AMS sync did not.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services import spool_relocation

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

UUID = "2E5476BB23AA431296203D76D312DA6F"
TAG = "0D19333900000100"


def _tray(tray_id: int, *, tagged: bool = True) -> dict:
    return {
        "id": tray_id,
        "tray_type": "PLA",
        "tray_color": "AFB1AEFF",
        "tray_info_idx": "GFA10",
        "tray_uuid": UUID if tagged else "0" * 32,
        "tag_uid": TAG if tagged else "0" * 16,
        "remain": 100,
    }


def _status(by_printer: dict[int, list], *, wrapped: bool = False):
    """``get_status`` answering each printer with its own AMS report: the list
    the MQTT client stores, or with ``wrapped`` the push's ``{"ams": [...]}``."""
    return lambda pid: SimpleNamespace(
        raw_data={"ams": {"ams": by_printer.get(pid, [])} if wrapped else by_printer.get(pid, [])},
        state="IDLE",
        connected=True,
    )


async def _rows(db_session, model=SpoolAssignment) -> set[tuple[int, int, int]]:
    db_session.expire_all()
    rows = (await db_session.execute(select(model))).scalars().all()
    return {(r.printer_id, r.ams_id, r.tray_id) for r in rows}


async def test_a_tag_read_on_printer_b_releases_printer_as_slot(test_engine, db_session, printer_factory):
    """The reporter's case, through the real AMS-update handler."""
    from backend.app.main import on_ams_change

    a_id = (await printer_factory()).id
    b_id = (await printer_factory()).id
    spool = Spool(material="PLA", label_weight=1000, weight_used=0, rgba="AFB1AEFF", tray_uuid=UUID, tag_uid=TAG)
    db_session.add(spool)
    await db_session.flush()
    spool_id = spool.id
    # Assigned by hand to A's empty slot, waiting for a spool.
    db_session.add(SpoolAssignment(spool_id=spool_id, printer_id=a_id, ams_id=0, tray_id=1))
    await db_session.commit()

    b_ams = [{"id": 0, "tray": [_tray(0)]}]
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    status = _status({b_id: b_ams}, wrapped=True)
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.services.printer_manager.printer_manager.get_status", status),
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.services.spool_relocation.ws_manager") as relocation_ws,
    ):
        pm.get_printer.return_value = MagicMock(name="X1C", serial_number="B")
        pm.get_status.side_effect = status
        pm.get_client.return_value = MagicMock()
        pm.get_model.return_value = "X1C"
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()
        relocation_ws.broadcast = AsyncMock()

        await on_ams_change(b_id, b_ams)

    assert await _rows(db_session) == {(b_id, 0, 0)}
    relocation_ws.broadcast.assert_awaited_once_with(
        {"type": "spool_assignment_changed", "printer_id": a_id, "ams_id": 0, "tray_id": 1}
    )


async def test_a_spool_matched_by_material_is_not_taken_from_its_slot(test_engine, db_session, printer_factory):
    """An unknown tag linked to an untagged spool of the same filament proves
    nothing about where that spool is: two identical rolls, one in each printer."""
    from backend.app.main import on_ams_change

    a_id = (await printer_factory()).id
    b_id = (await printer_factory()).id
    spool = Spool(material="PLA", brand="Bambu Lab", label_weight=1000, weight_used=0, rgba="AFB1AEFF")
    db_session.add(spool)
    await db_session.flush()
    spool_id = spool.id
    db_session.add(SpoolAssignment(spool_id=spool_id, printer_id=a_id, ams_id=0, tray_id=1))
    await db_session.commit()

    b_ams = [{"id": 0, "tray": [_tray(0)]}]
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    status = _status({b_id: b_ams})
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.services.printer_manager.printer_manager.get_status", status),
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.services.spool_relocation.ws_manager") as relocation_ws,
    ):
        pm.get_printer.return_value = MagicMock(name="X1C", serial_number="B")
        pm.get_status.side_effect = status
        pm.get_client.return_value = MagicMock()
        pm.get_model.return_value = "X1C"
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()
        relocation_ws.broadcast = AsyncMock()

        await on_ams_change(b_id, b_ams)

    db_session.expire_all()
    linked = (await db_session.execute(select(Spool).where(Spool.id == spool_id))).scalar_one()
    assert linked.tray_uuid == UUID  # the match did happen...
    assert (a_id, 0, 1) in await _rows(db_session)  # ...and A keeps its spool


class TestReleaseOtherSlots:
    async def _setup(self, db_session, printer_factory, slots):
        printers = [(await printer_factory()).id for _ in range(2)]
        spool = Spool(material="PLA", label_weight=1000, weight_used=0)
        db_session.add(spool)
        await db_session.flush()
        spool_id = spool.id
        for printer_idx, ams_id, tray_id in slots:
            db_session.add(
                SpoolAssignment(spool_id=spool_id, printer_id=printers[printer_idx], ams_id=ams_id, tray_id=tray_id)
            )
        await db_session.commit()
        return printers, spool_id

    async def _release(self, test_engine, reports, spool_id, keep, inventory=spool_relocation.INTERNAL):
        real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with (
            patch("backend.app.core.database.async_session", real_db),
            patch("backend.app.services.printer_manager.printer_manager.get_status", _status(reports)),
            patch("backend.app.services.spool_relocation.ws_manager") as ws,
        ):
            ws.broadcast = AsyncMock()
            return await spool_relocation.release_other_slots(inventory, spool_id, keep)

    async def test_releases_every_other_slot(self, test_engine, db_session, printer_factory):
        (a, b), spool_id = await self._setup(db_session, printer_factory, [(0, 0, 1), (0, 1, 2), (1, 0, 0)])
        vacated = await self._release(test_engine, {b: [{"id": 0, "tray": [_tray(0)]}]}, spool_id, (b, 0, 0))
        assert sorted(vacated) == [(a, 0, 1), (a, 1, 2)]
        assert await _rows(db_session) == {(b, 0, 0)}

    async def test_a_slot_reporting_the_same_tag_keeps_its_assignment(self, test_engine, db_session, printer_factory):
        """One tag in two slots is a misread: neither takes the other's row."""
        (a, b), spool_id = await self._setup(db_session, printer_factory, [(0, 0, 1), (1, 0, 0)])
        reports = {a: [{"id": 0, "tray": [_tray(0), _tray(1)]}], b: [{"id": 0, "tray": [_tray(0)]}]}
        assert await self._release(test_engine, reports, spool_id, (b, 0, 0)) == []
        assert await _rows(db_session) == {(a, 0, 1), (b, 0, 0)}

    async def test_nothing_without_a_tag_in_the_kept_slot(self, test_engine, db_session, printer_factory):
        (a, b), spool_id = await self._setup(db_session, printer_factory, [(0, 0, 1), (1, 0, 0)])
        reports = {b: [{"id": 0, "tray": [_tray(0, tagged=False)]}]}
        assert await self._release(test_engine, reports, spool_id, (b, 0, 0)) == []
        assert await _rows(db_session) == {(a, 0, 1), (b, 0, 0)}

    async def test_nothing_once_the_kept_slot_holds_another_spool(self, test_engine, db_session, printer_factory):
        """Reassigned since the read: the old read decides nothing."""
        (a, b), spool_id = await self._setup(db_session, printer_factory, [(0, 0, 1)])
        other = Spool(material="PLA", label_weight=1000, weight_used=0)
        db_session.add(other)
        await db_session.flush()
        db_session.add(SpoolAssignment(spool_id=other.id, printer_id=b, ams_id=0, tray_id=0))
        await db_session.commit()
        reports = {b: [{"id": 0, "tray": [_tray(0)]}]}
        assert await self._release(test_engine, reports, spool_id, (b, 0, 0)) == []
        assert await _rows(db_session) == {(a, 0, 1), (b, 0, 0)}

    async def test_external_slot_is_read_from_vt_tray(self, test_engine, db_session, printer_factory):
        (a, b), spool_id = await self._setup(db_session, printer_factory, [(0, 0, 1), (1, 255, 0)])
        real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        status = lambda pid: SimpleNamespace(raw_data={"vt_tray": [{**_tray(0), "id": "254"}]} if pid == b else {})  # noqa: E731
        with (
            patch("backend.app.core.database.async_session", real_db),
            patch("backend.app.services.printer_manager.printer_manager.get_status", status),
            patch("backend.app.services.spool_relocation.ws_manager") as ws,
        ):
            ws.broadcast = AsyncMock()
            vacated = await spool_relocation.release_other_slots(spool_relocation.INTERNAL, spool_id, (b, 255, 0))
        assert vacated == [(a, 0, 1)]

    async def test_takes_the_move_lock_then_printer_locks_in_id_order(self, test_engine, db_session, printer_factory):
        """A move's order (#1574): any other order can deadlock against one."""
        (a, b), spool_id = await self._setup(db_session, printer_factory, [(0, 0, 1), (1, 0, 0)])
        taken = []
        real_move, real_printer = spool_relocation.get_spool_move_lock, spool_relocation.get_ams_assignment_lock

        def move_lock(inventory, spool_id):
            taken.append(("spool", spool_id))
            return real_move(inventory, spool_id)

        def printer_lock(pid):
            taken.append(("printer", pid))
            return real_printer(pid)

        with (
            patch("backend.app.services.spool_relocation.get_spool_move_lock", move_lock),
            patch("backend.app.services.spool_relocation.get_ams_assignment_lock", printer_lock),
        ):
            await self._release(test_engine, {b: [{"id": 0, "tray": [_tray(0)]}]}, spool_id, (b, 0, 0))
        assert taken == [("spool", spool_id), ("printer", min(a, b)), ("printer", max(a, b))]


async def test_spoolman_sync_releases_the_spools_other_slots(test_engine, db_session, printer_factory):
    """Parity: the Spoolman AMS sync writes the slot it read the tag in and
    takes the spool out of its other slots."""
    from backend.app.services.spoolman_slot_sync import write_slot_results

    a = (await printer_factory()).id
    b = (await printer_factory()).id
    db_session.add(SpoolmanSlotAssignment(printer_id=a, ams_id=0, tray_id=1, spoolman_spool_id=42))
    await db_session.commit()

    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    reports = {b: [{"id": 0, "tray": [_tray(0)]}]}
    with (
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.services.printer_manager.printer_manager.get_status", _status(reports)),
        patch("backend.app.services.spool_relocation.ws_manager") as ws,
        patch("backend.app.services.spoolman_slot_sync.ws_manager") as sync_ws,
    ):
        ws.broadcast = AsyncMock()
        sync_ws.broadcast = AsyncMock()
        async with real_db() as db:
            await write_slot_results(db, {(a, 0, 1): 42}, [(b, 0, 0, 42)], [])

    assert await _rows(db_session, SpoolmanSlotAssignment) == {(b, 0, 0)}


async def test_spoolman_sync_without_a_slot_snapshot_releases_too(test_engine, db_session, printer_factory):
    """The write that runs when the slot table could not be read first."""
    from backend.app.services.spoolman_slot_sync import write_slot_results

    a = (await printer_factory()).id
    b = (await printer_factory()).id
    db_session.add(SpoolmanSlotAssignment(printer_id=a, ams_id=0, tray_id=1, spoolman_spool_id=42))
    await db_session.commit()

    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    reports = {b: [{"id": 0, "tray": [_tray(0)]}]}
    with (
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.services.printer_manager.printer_manager.get_status", _status(reports)),
        patch("backend.app.services.spool_relocation.ws_manager") as ws,
        patch("backend.app.services.spoolman_slot_sync.ws_manager") as sync_ws,
    ):
        ws.broadcast = AsyncMock()
        sync_ws.broadcast = AsyncMock()
        async with real_db() as db:
            await write_slot_results(db, None, [(b, 0, 0, 42)], [])

    assert await _rows(db_session, SpoolmanSlotAssignment) == {(b, 0, 0)}


async def test_spoolman_sync_of_an_untagged_slot_releases_nothing(test_engine, db_session, printer_factory):
    from backend.app.services.spoolman_slot_sync import write_slot_results

    a = (await printer_factory()).id
    b = (await printer_factory()).id
    db_session.add(SpoolmanSlotAssignment(printer_id=a, ams_id=0, tray_id=1, spoolman_spool_id=42))
    await db_session.commit()

    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    reports = {b: [{"id": 0, "tray": [_tray(0, tagged=False)]}]}
    with (
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.services.printer_manager.printer_manager.get_status", _status(reports)),
        patch("backend.app.services.spoolman_slot_sync.ws_manager") as sync_ws,
    ):
        sync_ws.broadcast = AsyncMock()
        async with real_db() as db:
            await write_slot_results(db, None, [(b, 0, 0, 42)], [])

    assert await _rows(db_session, SpoolmanSlotAssignment) == {(a, 0, 1), (b, 0, 0)}


async def test_spoolman_release_clears_the_old_slots_fallback_tag(test_engine, db_session, printer_factory):
    """As a Spoolman move does: a spool linked to an untagged slot carries that
    slot's fallback tag, and usage tracking would keep finding it there."""
    from backend.app.services.spoolman_tracking import get_fallback_spool_tag_for_slot

    printer_a = await printer_factory()
    a, serial_a = printer_a.id, printer_a.serial_number
    b = (await printer_factory()).id
    db_session.add(SpoolmanSlotAssignment(printer_id=a, ams_id=0, tray_id=1, spoolman_spool_id=42))
    db_session.add(SpoolmanSlotAssignment(printer_id=b, ams_id=0, tray_id=0, spoolman_spool_id=42))
    await db_session.commit()

    fallback = get_fallback_spool_tag_for_slot(serial_a, 0, 1)
    client = MagicMock()
    client.get_spool = AsyncMock(return_value={"id": 42, "extra": {"tag": f'"{fallback}"'}})
    client.merge_spool_extra = AsyncMock()
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.core.database.async_session", real_db),
        patch(
            "backend.app.services.printer_manager.printer_manager.get_status",
            _status({b: [{"id": 0, "tray": [_tray(0)]}]}),
        ),
        patch("backend.app.services.spool_relocation.ws_manager") as ws,
        patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
    ):
        ws.broadcast = AsyncMock()
        vacated = await spool_relocation.release_other_slots(spool_relocation.SPOOLMAN, 42, (b, 0, 0))

    assert vacated == [(a, 0, 1)]
    client.merge_spool_extra.assert_awaited_once_with(42, {"tag": '""'})
