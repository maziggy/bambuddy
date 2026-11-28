"""A SpoolBuddy pending load assigns the held spool to the slot loaded next (#3756).

``POST /spoolbuddy/devices/{id}/pending-load`` records the printer's empty
slots. When one of them reports a spool (the presence bit flips) and settles,
the spool is assigned there with ``move``, through the same code as
``POST /inventory/assignments`` or, in Spoolman mode, the slot-assignment
route. Nothing is assigned for a slot that was full when the load was made,
for two slots loading at once, for a slot reporting a tag, or for an occupied
slot on a printing printer. One pending load per device and per printer, kept
in memory only.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services import pending_load as pending_load_module
from backend.app.services.ams_assignment_lock import ams_assignment_locks, spool_move_locks
from backend.app.services.pending_load import pending_loads

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

API = "/api/v1/spoolbuddy/devices"
TAG = "C7EFC10300000100"


class FakePrinter:
    """The live status of one printer: unit 0 with four slots, unit 128 an AMS-HT."""

    def __init__(self):
        self.state = "IDLE"
        self.units = {
            0: {"id": "0", "tray": [self._tray(t, exists=t != 2) for t in range(4)]},
        }

    @staticmethod
    def _tray(tray_id: int, *, exists: bool, state: int | None = None, tag_uid: str = "0000000000000000") -> dict:
        return {
            "id": str(tray_id),
            "exists": exists,
            "state": state if state is not None else (11 if exists else 9),
            "tag_uid": tag_uid,
            "tray_uuid": "00000000000000000000000000000000",
            "tray_type": "",
        }

    def set_tray(self, ams_id: int, tray_id: int, **kwargs) -> None:
        self.units[ams_id]["tray"][tray_id] = self._tray(tray_id, **kwargs)

    def status(self):
        return SimpleNamespace(raw_data={"ams": list(self.units.values())}, state=self.state)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    pending_loads.clear()
    ams_assignment_locks.clear()
    spool_move_locks.clear()
    monkeypatch.setattr(pending_load_module, "SETTLE_POLL_S", 0.01)
    monkeypatch.setattr(pending_load_module, "SETTLE_TIMEOUT_S", 0.0)
    yield
    pending_loads.clear()
    pending_loads.clock = __import__("time").monotonic
    ams_assignment_locks.clear()
    spool_move_locks.clear()


@pytest.fixture
def printer_state():
    fake = FakePrinter()
    with (
        patch("backend.app.services.printer_manager.printer_manager.get_status", lambda _pid: fake.status()),
        patch("backend.app.services.printer_manager.printer_manager.get_client", MagicMock(return_value=None)),
        patch("backend.app.services.printer_manager.printer_manager.get_model", MagicMock(return_value="H2C")),
    ):
        yield fake


@pytest.fixture
def broadcast():
    with patch("backend.app.core.websocket.ws_manager.broadcast", AsyncMock()) as mock:
        yield mock


@pytest.fixture
async def device(async_client):
    response = await async_client.post(
        f"{API}/register", json={"device_id": "sb-1", "hostname": "kiosk", "ip_address": "192.168.1.50"}
    )
    assert response.status_code == 200
    return "sb-1"


@pytest.fixture
async def printer(printer_factory):
    return await printer_factory(model="H2C")


async def _spool(db_session, **kwargs) -> Spool:
    spool = Spool(material="PLA", label_weight=1000, weight_used=0, **kwargs)
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)
    return spool


async def _arm(async_client, device_id, spool_id, printer_id, **extra):
    return await async_client.post(
        f"{API}/{device_id}/pending-load", json={"spool_id": spool_id, "printer_id": printer_id, **extra}
    )


async def _load(fake: FakePrinter, printer_id: int, ams_id: int, tray_id: int, **tray) -> None:
    """A spool goes into a slot: the tray reports it, then the presence bit flips."""
    fake.set_tray(ams_id, tray_id, exists=True, **tray)
    await pending_loads.on_presence_change(printer_id, ams_id, tray_id, True)


async def _settled(device_id: str) -> None:
    load = pending_loads.get(device_id)
    if load is not None and load.settle_task is not None:
        await asyncio.wait_for(asyncio.shield(load.settle_task), 5)


async def _assignments(db_session) -> set[tuple[int, int, int, int]]:
    rows = (await db_session.execute(select(SpoolAssignment).execution_options(populate_existing=True))).scalars()
    return {(r.spool_id, r.printer_id, r.ams_id, r.tray_id) for r in rows}


class TestCreate:
    async def test_pending_with_its_countdown(self, async_client, db_session, device, printer, printer_state):
        spool = await _spool(db_session)
        response = await _arm(async_client, device, spool.id, printer.id, timeout_s=90)
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "pending"
        assert 85 <= body["expires_in_s"] <= 90
        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "pending"

    async def test_an_unregistered_device_is_refused(self, async_client, db_session, printer, printer_state):
        spool = await _spool(db_session)
        assert (await _arm(async_client, "nobody", spool.id, printer.id)).status_code == 404

    async def test_an_unknown_spool_is_refused(self, async_client, device, printer, printer_state):
        assert (await _arm(async_client, device, 99999, printer.id)).status_code == 404

    async def test_an_archived_spool_is_refused(self, async_client, db_session, device, printer, printer_state):
        from datetime import datetime, timezone

        spool = await _spool(db_session, archived_at=datetime.now(timezone.utc))
        assert (await _arm(async_client, device, spool.id, printer.id)).status_code == 400

    async def test_an_unknown_printer_is_refused(self, async_client, db_session, device, printer_state):
        spool = await _spool(db_session)
        assert (await _arm(async_client, device, spool.id, 999)).status_code == 404

    @pytest.mark.parametrize("timeout_s", [9, 601])
    async def test_the_window_is_10_to_600_seconds(
        self, async_client, db_session, device, printer, printer_state, timeout_s
    ):
        spool = await _spool(db_session)
        assert (await _arm(async_client, device, spool.id, printer.id, timeout_s=timeout_s)).status_code == 422

    async def test_another_device_on_the_same_printer_gets_409(
        self, async_client, db_session, device, printer, printer_state
    ):
        await async_client.post(
            f"{API}/register", json={"device_id": "sb-2", "hostname": "b", "ip_address": "10.0.0.2"}
        )
        spool = await _spool(db_session)
        assert (await _arm(async_client, device, spool.id, printer.id)).status_code == 200
        assert (await _arm(async_client, "sb-2", spool.id, printer.id)).status_code == 409

    async def test_the_same_device_replaces_its_own(self, async_client, db_session, device, printer, printer_state):
        first = await _spool(db_session)
        second = await _spool(db_session)
        await _arm(async_client, device, first.id, printer.id)
        response = await _arm(async_client, device, second.id, printer.id)
        assert response.status_code == 200
        assert response.json()["spool_id"] == second.id

    async def test_register_and_heartbeat_offer_the_feature(self, async_client, device):
        registered = await async_client.post(
            f"{API}/register", json={"device_id": device, "hostname": "kiosk", "ip_address": "192.168.1.50"}
        )
        assert "pending_load" in registered.json()["features"]
        heartbeat = await async_client.post(f"{API}/{device}/heartbeat", json={})
        assert "pending_load" in heartbeat.json()["features"]


class TestMatching:
    async def test_an_untagged_spool_in_an_empty_slot_is_assigned(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)

        await _load(printer_state, printer.id, 0, 2)
        await _settled(device)

        body = (await async_client.get(f"{API}/{device}/pending-load")).json()
        assert (body["state"], body["ams_id"], body["tray_id"]) == ("assigned", 0, 2)
        # The fixture's printer has no MQTT client: the slot waits for it.
        assert (body["configured"], body["printer_offline"]) == (False, True)
        assert await _assignments(db_session) == {(spool.id, printer.id, 0, 2)}
        events = [c.args[0] for c in broadcast.await_args_list if c.args[0]["type"] == "spool_auto_assigned"]
        assert events == [
            {
                "type": "spool_auto_assigned",
                "printer_id": printer.id,
                "ams_id": 0,
                "tray_id": 2,
                "spool_id": spool.id,
                "device_id": device,
                "source": "pending_load",
            }
        ]

    async def test_a_connected_printer_gets_the_settings(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)
        client = MagicMock()

        with patch("backend.app.services.printer_manager.printer_manager.get_client", MagicMock(return_value=client)):
            await _load(printer_state, printer.id, 0, 2)
            await _settled(device)

        body = (await async_client.get(f"{API}/{device}/pending-load")).json()
        assert (body["state"], body["configured"], body["printer_offline"]) == ("assigned", True, False)
        assert client.ams_set_filament_setting.call_args.kwargs["tray_id"] == 2

    async def test_the_spool_leaves_its_old_slot(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        spool = await _spool(db_session)
        db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0))
        await db_session.commit()
        await _arm(async_client, device, spool.id, printer.id)

        await _load(printer_state, printer.id, 0, 2)
        await _settled(device)

        assert await _assignments(db_session) == {(spool.id, printer.id, 0, 2)}

    async def test_a_slot_that_was_full_does_not_match(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)

        # Slot 1 held a spool when the load was made: a blip, not a load.
        await pending_loads.on_presence_change(printer.id, 0, 1, True)
        await _settled(device)
        await asyncio.sleep(0.05)

        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "pending"
        assert await _assignments(db_session) == set()

    async def test_a_swap_in_the_same_slot_is_assigned(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        old = await _spool(db_session)
        spool = await _spool(db_session)
        db_session.add(SpoolAssignment(spool_id=old.id, printer_id=printer.id, ams_id=0, tray_id=1))
        await db_session.commit()
        await _arm(async_client, device, spool.id, printer.id)

        printer_state.set_tray(0, 1, exists=False)
        await pending_loads.on_presence_change(printer.id, 0, 1, False)
        await _load(printer_state, printer.id, 0, 1)
        await _settled(device)

        assert await _assignments(db_session) == {(spool.id, printer.id, 0, 1)}

    async def test_two_slots_loading_assigns_neither(
        self, async_client, db_session, device, printer, printer_state, broadcast, monkeypatch
    ):
        monkeypatch.setattr(pending_load_module, "SETTLE_TIMEOUT_S", 60.0)
        printer_state.set_tray(0, 3, exists=False)
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)

        # Both report a spool before either has settled.
        await _load(printer_state, printer.id, 0, 2, state=9)
        await _load(printer_state, printer.id, 0, 3, state=9)

        body = (await async_client.get(f"{API}/{device}/pending-load")).json()
        assert (body["state"], body["reason"]) == ("failed", "several_slots_loaded")
        assert await _assignments(db_session) == set()

    async def test_a_slot_reporting_a_tag_is_left_to_rfid(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)

        await _load(printer_state, printer.id, 0, 2, tag_uid=TAG)
        await _settled(device)

        body = (await async_client.get(f"{API}/{device}/pending-load")).json()
        assert (body["state"], body["reason"]) == ("failed", "slot_has_tag")
        assert await _assignments(db_session) == set()

    async def test_an_assigned_slot_on_a_printing_printer_is_not_replaced(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        old = await _spool(db_session)
        spool = await _spool(db_session)
        db_session.add(SpoolAssignment(spool_id=old.id, printer_id=printer.id, ams_id=0, tray_id=2))
        await db_session.commit()
        printer_state.state = "RUNNING"
        await _arm(async_client, device, spool.id, printer.id)

        await _load(printer_state, printer.id, 0, 2)
        await _settled(device)

        body = (await async_client.get(f"{API}/{device}/pending-load")).json()
        assert (body["state"], body["reason"]) == ("failed", "printing")
        assert await _assignments(db_session) == {(old.id, printer.id, 0, 2)}

    async def test_an_empty_slot_on_a_printing_printer_is_assigned(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        spool = await _spool(db_session)
        printer_state.state = "RUNNING"
        await _arm(async_client, device, spool.id, printer.id)

        await _load(printer_state, printer.id, 0, 2)
        await _settled(device)

        assert await _assignments(db_session) == {(spool.id, printer.id, 0, 2)}

    async def test_a_spool_pulled_out_before_it_settles_is_not_assigned(
        self, async_client, db_session, device, printer, printer_state, broadcast, monkeypatch
    ):
        monkeypatch.setattr(pending_load_module, "SETTLE_TIMEOUT_S", 60.0)
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)

        await _load(printer_state, printer.id, 0, 2, state=9)
        task = pending_loads.get(device).settle_task
        printer_state.set_tray(0, 2, exists=False)
        await pending_loads.on_presence_change(printer.id, 0, 2, False)
        await asyncio.gather(task, return_exceptions=True)

        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "pending"
        assert await _assignments(db_session) == set()

    async def test_a_slot_that_never_reaches_state_11_is_assigned_after_the_wait(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        # An AMS-HT reports its loaded tray as 9, the A1 Mini BMCU as 3.
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)

        await _load(printer_state, printer.id, 0, 2, state=3)
        await _settled(device)

        assert await _assignments(db_session) == {(spool.id, printer.id, 0, 2)}

    async def test_a_drying_ams_that_never_feeds_ends_failed(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        now = [1000.0]
        pending_loads.clock = lambda: now[0]
        printer_state.units[0]["dry_time"] = 120
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id, timeout_s=60)

        await _load(printer_state, printer.id, 0, 2, state=9)
        await asyncio.sleep(0.05)
        assert pending_loads.get(device).state == "pending"  # still waiting for the feed

        now[0] += 61
        await _settled(device)

        load = pending_loads.get(device)
        assert (load.state, load.reason) == ("failed", "not_loaded")
        assert await _assignments(db_session) == set()


class TestEnding:
    async def test_cancel(self, async_client, db_session, device, printer, printer_state, broadcast):
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)

        assert (await async_client.delete(f"{API}/{device}/pending-load")).json()["state"] == "cancelled"
        await _load(printer_state, printer.id, 0, 2)
        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "cancelled"
        assert await _assignments(db_session) == set()

    async def test_expiry(self, async_client, db_session, device, printer, printer_state, broadcast):
        now = [1000.0]
        pending_loads.clock = lambda: now[0]
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id, timeout_s=30)

        now[0] += 31
        await _load(printer_state, printer.id, 0, 2)

        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "expired"
        assert await _assignments(db_session) == set()

    async def test_none_made_or_lost_in_a_restart_reads_expired(self, async_client, device):
        body = (await async_client.get(f"{API}/{device}/pending-load")).json()
        assert body == {
            "device_id": device,
            "state": "expired",
            "spool_id": None,
            "printer_id": None,
            "expires_in_s": None,
            "ams_id": None,
            "tray_id": None,
            "configured": None,
            "printer_offline": None,
            "reason": None,
        }

    async def test_a_result_is_kept_for_a_while(
        self, async_client, db_session, device, printer, printer_state, broadcast
    ):
        now = [1000.0]
        pending_loads.clock = lambda: now[0]
        spool = await _spool(db_session)
        await _arm(async_client, device, spool.id, printer.id)
        await async_client.delete(f"{API}/{device}/pending-load")

        now[0] += pending_load_module.RESULT_KEEP_S - 1
        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "cancelled"
        now[0] += 2
        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "expired"


class TestSpoolmanMode:
    @pytest.fixture
    async def spoolman(self, db_session):
        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
        await db_session.commit()
        client = MagicMock()
        client.get_spool = AsyncMock(
            return_value={
                "id": 10,
                "filament": {"id": 1, "name": "PLA Basic", "material": "PLA", "color_hex": "FF0000", "weight": 1000},
                "remaining_weight": 800.0,
                "archived": False,
                "extra": {},
            }
        )
        client.get_spools = AsyncMock(return_value=[])
        client.merge_spool_extra = AsyncMock(return_value={"id": 10, "extra": {}})
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)):
            yield client

    async def test_a_spoolman_spool_is_assigned(
        self, async_client, db_session, device, printer, printer_state, broadcast, spoolman
    ):
        assert (await _arm(async_client, device, 10, printer.id)).status_code == 200

        await _load(printer_state, printer.id, 0, 2)
        await _settled(device)

        assert (await async_client.get(f"{API}/{device}/pending-load")).json()["state"] == "assigned"
        rows = (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all()
        assert [(r.spoolman_spool_id, r.printer_id, r.ams_id, r.tray_id) for r in rows] == [(10, printer.id, 0, 2)]

    async def test_a_push_that_fails_is_reported_not_configured(
        self, async_client, db_session, device, printer, printer_state, broadcast, spoolman
    ):
        """Assigned, but the settings did not reach the printer and are not retried (#3771)."""
        await _arm(async_client, device, 10, printer.id)

        with (
            patch(
                "backend.app.services.printer_manager.printer_manager.get_client", MagicMock(return_value=MagicMock())
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.apply_spoolman_spool_to_slot",
                AsyncMock(side_effect=RuntimeError("publish failed")),
            ),
        ):
            await _load(printer_state, printer.id, 0, 2)
            await _settled(device)

        body = (await async_client.get(f"{API}/{device}/pending-load")).json()
        assert (body["state"], body["configured"], body["printer_offline"]) == ("assigned", False, False)

    async def test_an_archived_spoolman_spool_is_refused(self, async_client, device, printer, printer_state, spoolman):
        spoolman.get_spool.return_value = {**spoolman.get_spool.return_value, "archived": True}
        assert (await _arm(async_client, device, 10, printer.id)).status_code == 400
