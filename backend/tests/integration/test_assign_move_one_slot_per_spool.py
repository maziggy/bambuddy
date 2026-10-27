"""A move assigns a spool to a slot and takes it out of its other slots in one commit (#1574).

The QR scan assigns the spool in the user's hand, which is in one slot at most.
The scan flow used to unassign the old slot and assign the new one in separate
requests, so a failure between them left the spool in no slot. With ``move``
both assign routes do it in the transaction that writes the new slot:

* the spool's other slots are cleared and the target's occupant replaced;
* a failure changes nothing;
* two moves of the same spool at once take turns and leave it in one slot,
  wherever it was before;
* a slot on a printer outside the caller's scope stays assigned (#1727);
* in Spoolman mode, a fallback tag the spool carries for a slot it leaves is
  cleared first, and a refusal from Spoolman stops the move; RFID and other
  tags stay; the slots it left and the one it went to are announced.

Without ``move`` the routes behave as before.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.core.auth import get_printer_scope_if_auth_enabled
from backend.app.core.database import Base, _set_sqlite_pragmas
from backend.app.core.printer_scope import PrinterScope
from backend.app.main import app
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.ams_assignment_lock import (
    ams_assignment_locks,
    get_ams_assignment_lock,
    get_spool_move_lock,
    spool_move_locks,
)
from backend.app.services.spoolman import SpoolmanUnavailableError
from backend.app.services.spoolman_tracking import get_fallback_spool_tag_for_slot

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _fresh_locks():
    """A contended lock binds to its test's event loop; printer and spool ids repeat."""
    ams_assignment_locks.clear()
    spool_move_locks.clear()
    yield
    ams_assignment_locks.clear()
    spool_move_locks.clear()


async def _until_queued(lock: asyncio.Lock, requests: int) -> None:
    """Return once that many requests wait for the lock."""
    async with asyncio.timeout(5):
        while len(lock._waiters or ()) < requests:
            await asyncio.sleep(0.01)


@pytest.fixture
def scope_limited_to():
    """Run requests as a caller who can only see the given printers."""

    def _limit(*printer_ids: int) -> None:
        app.dependency_overrides[get_printer_scope_if_auth_enabled] = lambda: PrinterScope(
            printer_ids=frozenset(printer_ids)
        )

    yield _limit
    app.dependency_overrides.pop(get_printer_scope_if_auth_enabled, None)


async def _commit_fails(self):
    raise RuntimeError("database went away")


# ---------------------------------------------------------------------------
# Built-in inventory: POST /inventory/assignments
# ---------------------------------------------------------------------------


@pytest.fixture
def offline_printers():
    """No printer is connected, so the assign stores the row and skips MQTT."""
    ws = MagicMock()
    ws.broadcast = AsyncMock()
    with (
        patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=None)),
        patch("backend.app.services.printer_manager.printer_manager.get_client", MagicMock(return_value=None)),
        patch("backend.app.api.routes.inventory.ws_manager", ws),
    ):
        yield ws


async def _spool(db: AsyncSession, **kwargs) -> Spool:
    spool = Spool(material="PLA", label_weight=1000, weight_used=0, **kwargs)
    db.add(spool)
    await db.commit()
    await db.refresh(spool)
    return spool


async def _assign_row(db: AsyncSession, spool_id: int, printer_id: int, ams_id: int, tray_id: int) -> None:
    db.add(SpoolAssignment(spool_id=spool_id, printer_id=printer_id, ams_id=ams_id, tray_id=tray_id))
    await db.commit()


async def _slots(db: AsyncSession) -> set[tuple[int, int, int, int]]:
    """Every built-in assignment as (spool, printer, ams, tray), as the database has it now."""
    rows = (await db.execute(select(SpoolAssignment).execution_options(populate_existing=True))).scalars().all()
    return {(r.spool_id, r.printer_id, r.ams_id, r.tray_id) for r in rows}


def _assign(spool_id: int, printer_id: int, ams_id: int, tray_id: int, **extra) -> dict:
    return {"spool_id": spool_id, "printer_id": printer_id, "ams_id": ams_id, "tray_id": tray_id, **extra}


class TestBuiltInMove:
    async def test_move_takes_the_spool_out_of_its_old_slot(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers
    ):
        printer = await printer_factory()
        spool = await _spool(db_session)
        await _assign_row(db_session, spool.id, printer.id, 0, 1)

        response = await async_client.post(
            "/api/v1/inventory/assignments", json=_assign(spool.id, printer.id, 1, 0, move=True)
        )

        assert response.status_code == 200, response.text
        assert await _slots(db_session) == {(spool.id, printer.id, 1, 0)}

    async def test_move_takes_the_spool_out_of_another_printers_slot(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers
    ):
        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        spool = await _spool(db_session)
        await _assign_row(db_session, spool.id, there.id, 2, 3)

        response = await async_client.post(
            "/api/v1/inventory/assignments", json=_assign(spool.id, here.id, 0, 0, move=True)
        )

        assert response.status_code == 200, response.text
        assert await _slots(db_session) == {(spool.id, here.id, 0, 0)}
        # Both slots changed, so both are announced.
        announced = {
            (c.args[0]["printer_id"], c.args[0]["ams_id"], c.args[0]["tray_id"])
            for c in offline_printers.broadcast.await_args_list
            if c.args[0]["type"] == "spool_assignment_changed"
        }
        assert announced == {(there.id, 2, 3), (here.id, 0, 0)}

    async def test_move_replaces_the_target_slots_spool(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers
    ):
        printer = await printer_factory()
        moving = await _spool(db_session)
        occupant = await _spool(db_session)
        await _assign_row(db_session, moving.id, printer.id, 0, 1)
        await _assign_row(db_session, occupant.id, printer.id, 0, 2)

        response = await async_client.post(
            "/api/v1/inventory/assignments", json=_assign(moving.id, printer.id, 0, 2, move=True)
        )

        assert response.status_code == 200, response.text
        assert await _slots(db_session) == {(moving.id, printer.id, 0, 2)}

    async def test_without_move_the_old_slot_stays(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers
    ):
        """A plain assign is per slot, as before: the "Show all spools" path relies on it."""
        printer = await printer_factory()
        spool = await _spool(db_session)
        await _assign_row(db_session, spool.id, printer.id, 0, 1)

        response = await async_client.post("/api/v1/inventory/assignments", json=_assign(spool.id, printer.id, 1, 0))

        assert response.status_code == 200, response.text
        assert await _slots(db_session) == {(spool.id, printer.id, 0, 1), (spool.id, printer.id, 1, 0)}

    async def test_a_slot_outside_the_callers_scope_stays(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers, scope_limited_to
    ):
        mine = await printer_factory(name="Mine")
        hidden = await printer_factory(name="Hidden")
        spool = await _spool(db_session)
        await _assign_row(db_session, spool.id, hidden.id, 0, 0)
        scope_limited_to(mine.id)

        response = await async_client.post(
            "/api/v1/inventory/assignments", json=_assign(spool.id, mine.id, 0, 1, move=True)
        )

        assert response.status_code == 200, response.text
        assert await _slots(db_session) == {(spool.id, hidden.id, 0, 0), (spool.id, mine.id, 0, 1)}

    async def test_a_failed_move_leaves_the_spool_where_it_was(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers
    ):
        printer = await printer_factory()
        spool = await _spool(db_session)
        await _assign_row(db_session, spool.id, printer.id, 0, 1)

        with patch.object(AsyncSession, "commit", _commit_fails), pytest.raises(RuntimeError):
            await async_client.post(
                "/api/v1/inventory/assignments", json=_assign(spool.id, printer.id, 1, 0, move=True)
            )

        assert await _slots(db_session) == {(spool.id, printer.id, 0, 1)}

    async def test_move_waits_for_the_lock_of_the_printer_it_leaves(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers
    ):
        """The old slot's row is written too, so its printer's lock is taken (SA-5)."""
        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        spool = await _spool(db_session)
        await _assign_row(db_session, spool.id, there.id, 0, 0)

        lock = get_ams_assignment_lock(there.id)
        await lock.acquire()
        try:
            task = asyncio.ensure_future(
                async_client.post("/api/v1/inventory/assignments", json=_assign(spool.id, here.id, 0, 0, move=True))
            )
            await asyncio.sleep(0.3)
            assert not task.done(), "the move cleared the old slot without waiting for its printer's lock"
        finally:
            lock.release()
        response = await asyncio.wait_for(task, 10)

        assert response.status_code == 200, response.text

    async def test_a_slot_the_spool_gained_while_the_move_waited_is_cleared_too(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers
    ):
        """The printers to lock are read again once the locks are held: here a
        plain assign put the spool on a printer the move had not locked."""
        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        spool = await _spool(db_session)

        lock = get_ams_assignment_lock(here.id)
        await lock.acquire()
        try:
            move = asyncio.ensure_future(
                async_client.post("/api/v1/inventory/assignments", json=_assign(spool.id, here.id, 0, 0, move=True))
            )
            await _until_queued(lock, 1)
            await _assign_row(db_session, spool.id, there.id, 1, 1)
        finally:
            lock.release()
        response = await asyncio.wait_for(move, 10)

        assert response.status_code == 200, response.text
        assert await _slots(db_session) == {(spool.id, here.id, 0, 0)}


class TestBuiltInConcurrentMoves:
    @pytest.fixture
    async def test_engine(self, test_engine, tmp_path):
        """Two requests at once need a connection each, as the app's pool gives
        them. The suite's in-memory SQLite database is a single connection,
        shared by every session, and one request's commit there fails while the
        other's statement is open ("SQL statements in progress"). On SQLite
        these tests get a database file instead; PostgreSQL pools already."""
        if test_engine.dialect.name != "sqlite":
            yield test_engine
            return
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'moves.db'}")
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        await engine.dispose()

    @pytest.mark.parametrize("starts_in_a_slot", [True, False], ids=["from-a-slot", "from-no-slot"])
    async def test_two_moves_of_the_same_spool_leave_it_in_one_slot(
        self, async_client: AsyncClient, db_session, printer_factory, offline_printers, starts_in_a_slot
    ):
        """#1606 review: the printers to lock were read before the locks were
        taken, so the move served second missed where the first put the spool.
        A spool in no slot was worse: each move locked only its own target's
        printer, and both assigned it. Moves of one spool now take turns."""
        start = await printer_factory(name="Start")
        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        spool = await _spool(db_session)
        if starts_in_a_slot:
            await _assign_row(db_session, spool.id, start.id, 0, 0)

        lock = get_spool_move_lock("inventory", spool.id)
        await lock.acquire()
        try:
            moves = [
                asyncio.ensure_future(
                    async_client.post("/api/v1/inventory/assignments", json=_assign(spool.id, pid, 0, 0, move=True))
                )
                for pid in (here.id, there.id)
            ]
            # Both have read where the spool is and wait for their turn.
            await _until_queued(lock, 2)
        finally:
            lock.release()
        responses = await asyncio.wait_for(asyncio.gather(*moves), 10)

        assert [r.status_code for r in responses] == [200, 200]
        slots = await _slots(db_session)
        assert len(slots) == 1, slots


# ---------------------------------------------------------------------------
# Spoolman: POST /spoolman/inventory/slot-assignments
# ---------------------------------------------------------------------------

SPOOLMAN_SPOOL_ID = 10


def _spoolman_spool(tag: str | None = None) -> dict:
    return {
        "id": SPOOLMAN_SPOOL_ID,
        "filament": {"id": 1, "name": "PLA Basic", "material": "PLA", "color_hex": "FF0000", "weight": 1000},
        "remaining_weight": 800.0,
        "used_weight": 200.0,
        "location": None,
        "comment": None,
        "registered": "2024-01-01T00:00:00+00:00",
        "archived": False,
        "extra": {} if tag is None else {"tag": json.dumps(tag)},
    }


@pytest.fixture
async def spoolman_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
def spoolman():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=_spoolman_spool())
    # The target slot's stale-tag cleanup (#1457) lists the spools; none here.
    client.get_spools = AsyncMock(return_value=[])
    client.merge_spool_extra = AsyncMock(return_value={"id": SPOOLMAN_SPOOL_ID, "extra": {}})
    with (
        patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)),
        patch("backend.app.api.routes.spoolman_inventory.printer_manager.get_client", MagicMock(return_value=None)),
    ):
        yield client


@pytest.fixture
def spoolman_ws():
    """What the Spoolman routes announce over the websocket."""
    ws = MagicMock()
    ws.broadcast = AsyncMock()
    with patch("backend.app.api.routes.spoolman_inventory.ws_manager", ws):
        yield ws


def _announced_slots(ws) -> set[tuple[int, int, int]]:
    return {
        (c.args[0]["printer_id"], c.args[0]["ams_id"], c.args[0]["tray_id"])
        for c in ws.broadcast.await_args_list
        if c.args[0].get("type") == "spool_assignment_changed"
    }


async def _spoolman_row(db: AsyncSession, printer_id: int, ams_id: int, tray_id: int) -> None:
    db.add(
        SpoolmanSlotAssignment(
            printer_id=printer_id, ams_id=ams_id, tray_id=tray_id, spoolman_spool_id=SPOOLMAN_SPOOL_ID
        )
    )
    await db.commit()


async def _spoolman_slots(db: AsyncSession) -> set[tuple[int, int, int, int]]:
    """Every Spoolman slot assignment as (spool, printer, ams, tray), as the database has it now."""
    rows = (await db.execute(select(SpoolmanSlotAssignment).execution_options(populate_existing=True))).scalars().all()
    return {(r.spoolman_spool_id, r.printer_id, r.ams_id, r.tray_id) for r in rows}


def _spoolman_assign(printer_id: int, ams_id: int, tray_id: int, **extra) -> dict:
    return {
        "spoolman_spool_id": SPOOLMAN_SPOOL_ID,
        "printer_id": printer_id,
        "ams_id": ams_id,
        "tray_id": tray_id,
        **extra,
    }


def _tag_writes(client) -> list[str]:
    """The extra.tag values written to the moved spool, in order."""
    return [
        json.loads(c.args[1]["tag"])
        for c in client.merge_spool_extra.await_args_list
        if c.args[0] == SPOOLMAN_SPOOL_ID and "tag" in c.args[1]
    ]


class TestSpoolmanMove:
    async def test_move_takes_the_spool_out_of_its_old_slot(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman
    ):
        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        await _spoolman_row(db_session, there.id, 1, 2)

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(here.id, 0, 0, move=True)
        )

        assert response.status_code == 200, response.text
        assert await _spoolman_slots(db_session) == {(SPOOLMAN_SPOOL_ID, here.id, 0, 0)}
        assert _tag_writes(spoolman) == []

    async def test_without_move_the_old_slot_stays(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman
    ):
        printer = await printer_factory()
        await _spoolman_row(db_session, printer.id, 0, 1)

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(printer.id, 0, 2)
        )

        assert response.status_code == 200, response.text
        assert await _spoolman_slots(db_session) == {
            (SPOOLMAN_SPOOL_ID, printer.id, 0, 1),
            (SPOOLMAN_SPOOL_ID, printer.id, 0, 2),
        }

    async def test_the_old_slots_fallback_tag_is_cleared(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman
    ):
        """Linked to a slot without RFID, the spool carries that slot's fallback tag.
        Left on it, the tag lookup would find the spool for its old slot again."""
        printer = await printer_factory()
        await _spoolman_row(db_session, printer.id, 0, 1)
        spoolman.get_spool.return_value = _spoolman_spool(
            tag=get_fallback_spool_tag_for_slot(printer.serial_number, 0, 1)
        )

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(printer.id, 1, 0, move=True)
        )

        assert response.status_code == 200, response.text
        assert _tag_writes(spoolman) == [""]
        assert await _spoolman_slots(db_session) == {(SPOOLMAN_SPOOL_ID, printer.id, 1, 0)}

    @pytest.mark.parametrize(
        "tag",
        [
            pytest.param("A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4", id="rfid-tray-uuid"),
            pytest.param("05CC1E0F00000100", id="rfid-tag-uid"),
        ],
    )
    async def test_a_tag_of_the_spool_itself_stays(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman, tag
    ):
        printer = await printer_factory()
        await _spoolman_row(db_session, printer.id, 0, 1)
        spoolman.get_spool.return_value = _spoolman_spool(tag=tag)

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(printer.id, 1, 0, move=True)
        )

        assert response.status_code == 200, response.text
        assert _tag_writes(spoolman) == []

    async def test_the_fallback_tag_of_a_slot_it_does_not_leave_stays(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman
    ):
        """Only the slots the spool leaves are matched -- here its tag is the target's own."""
        printer = await printer_factory()
        await _spoolman_row(db_session, printer.id, 0, 1)
        spoolman.get_spool.return_value = _spoolman_spool(
            tag=get_fallback_spool_tag_for_slot(printer.serial_number, 1, 0)
        )

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(printer.id, 1, 0, move=True)
        )

        assert response.status_code == 200, response.text
        assert _tag_writes(spoolman) == []

    async def test_a_refused_tag_cleanup_keeps_the_old_assignment(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman
    ):
        printer = await printer_factory()
        await _spoolman_row(db_session, printer.id, 0, 1)
        spoolman.get_spool.return_value = _spoolman_spool(
            tag=get_fallback_spool_tag_for_slot(printer.serial_number, 0, 1)
        )
        spoolman.merge_spool_extra.side_effect = SpoolmanUnavailableError("down")

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(printer.id, 1, 0, move=True)
        )

        assert response.status_code == 503
        assert "not moved" in response.json()["detail"]
        assert await _spoolman_slots(db_session) == {(SPOOLMAN_SPOOL_ID, printer.id, 0, 1)}

    async def test_a_failed_save_puts_the_cleared_tag_back(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman
    ):
        printer = await printer_factory()
        await _spoolman_row(db_session, printer.id, 0, 1)
        old_tag = get_fallback_spool_tag_for_slot(printer.serial_number, 0, 1)
        spoolman.get_spool.return_value = _spoolman_spool(tag=old_tag)

        with patch.object(AsyncSession, "commit", _commit_fails):
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(printer.id, 1, 0, move=True)
            )

        assert response.status_code == 500
        assert _tag_writes(spoolman) == ["", old_tag]
        assert await _spoolman_slots(db_session) == {(SPOOLMAN_SPOOL_ID, printer.id, 0, 1)}

    async def test_a_slot_outside_the_callers_scope_stays(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
        spoolman_settings,
        spoolman,
        scope_limited_to,
    ):
        mine = await printer_factory(name="Mine")
        hidden = await printer_factory(name="Hidden")
        await _spoolman_row(db_session, hidden.id, 0, 0)
        # Even its fallback tag: that slot is not the caller's to change.
        spoolman.get_spool.return_value = _spoolman_spool(
            tag=get_fallback_spool_tag_for_slot(hidden.serial_number, 0, 0)
        )
        scope_limited_to(mine.id)

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(mine.id, 0, 1, move=True)
        )

        assert response.status_code == 200, response.text
        assert _tag_writes(spoolman) == []
        assert await _spoolman_slots(db_session) == {
            (SPOOLMAN_SPOOL_ID, hidden.id, 0, 0),
            (SPOOLMAN_SPOOL_ID, mine.id, 0, 1),
        }

    async def test_a_move_announces_the_slots_it_left_and_the_one_it_went_to(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman, spoolman_ws
    ):
        """#1606 review: other tabs and the SpoolBuddy kiosk kept the spool in its old slot."""
        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        await _spoolman_row(db_session, there.id, 1, 2)

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(here.id, 0, 0, move=True)
        )

        assert response.status_code == 200, response.text
        assert _announced_slots(spoolman_ws) == {(there.id, 1, 2), (here.id, 0, 0)}

    async def test_a_plain_assign_announces_its_slot(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman, spoolman_ws
    ):
        printer = await printer_factory()

        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(printer.id, 0, 3)
        )

        assert response.status_code == 200, response.text
        assert _announced_slots(spoolman_ws) == {(printer.id, 0, 3)}

    async def test_a_move_also_clears_a_slot_the_spool_gained_meanwhile(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman, spoolman_ws
    ):
        """Between this move reading the spool's slots and deleting them,
        something else (a link, the AMS sync) gave it another slot: that one
        goes too."""
        from backend.app.api.routes import spoolman_inventory

        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        await _spoolman_row(db_session, there.id, 0, 0)
        clear_tag = spoolman_inventory._clear_vacated_slot_fallback_tag

        async def another_slot_is_committed(*args, **kwargs):
            await _spoolman_row(db_session, there.id, 1, 1)
            return await clear_tag(*args, **kwargs)

        with patch.object(spoolman_inventory, "_clear_vacated_slot_fallback_tag", another_slot_is_committed):
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(here.id, 0, 0, move=True)
            )

        assert response.status_code == 200, response.text
        assert await _spoolman_slots(db_session) == {(SPOOLMAN_SPOOL_ID, here.id, 0, 0)}
        assert _announced_slots(spoolman_ws) == {(there.id, 0, 0), (there.id, 1, 1), (here.id, 0, 0)}

    async def test_two_moves_of_the_same_spool_take_turns(
        self, async_client: AsyncClient, db_session, printer_factory, spoolman_settings, spoolman
    ):
        """#1606 review: on PostgreSQL a move's DELETE missed the slot another
        move of the spool inserted and committed after the DELETE began, and the
        spool ended in both. SQLite's single writer hid it."""
        start = await printer_factory(name="Start")
        here = await printer_factory(name="Here")
        there = await printer_factory(name="There")
        await _spoolman_row(db_session, start.id, 0, 0)

        lock = get_spool_move_lock("spoolman", SPOOLMAN_SPOOL_ID)
        await lock.acquire()
        try:
            moves = [
                asyncio.ensure_future(
                    async_client.post(
                        "/api/v1/spoolman/inventory/slot-assignments", json=_spoolman_assign(pid, 0, 0, move=True)
                    )
                )
                for pid in (here.id, there.id)
            ]
            await _until_queued(lock, 2)
        finally:
            lock.release()
        responses = await asyncio.wait_for(asyncio.gather(*moves), 10)

        assert [r.status_code for r in responses] == [200, 200]
        slots = await _spoolman_slots(db_session)
        assert len(slots) == 1, slots
