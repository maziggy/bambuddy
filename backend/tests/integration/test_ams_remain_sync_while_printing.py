"""The AMS remain% weight sync stays off while the printer prints.

Usage is deducted precisely at completion from the 3MF; raising weight_used
from remain% during the print as well charges it twice (#880). The guard used
to be only the usage tracker's in-memory session, which a restart with a
stale persisted session never registers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.usage_tracker import _active_sessions


def _loaded_before_the_hand_edit(spool, _context):
    set_committed_value(spool, "weight_locked", False)
    set_committed_value(spool, "weight_used", 100)


@pytest.fixture(autouse=True)
def _no_sessions():
    _active_sessions.clear()
    yield
    _active_sessions.clear()


async def _weight_after_ams_push(
    test_engine,
    db_session,
    printer_factory,
    state: str,
    sent: list[str] | None = None,
    *,
    weight_used: float = 100,
    weight_locked: bool = False,
    loaded_as_unlocked: bool = False,
) -> float:
    """``loaded_as_unlocked``: the sync loads the spool unlocked at 100 g, as
    it was before a hand edit that committed right after that load."""
    from backend.app.main import on_ams_change

    printer = await printer_factory(name="X1C")
    spool = Spool(material="PLA", label_weight=1000, weight_used=weight_used, weight_locked=weight_locked)
    db_session.add(spool)
    await db_session.flush()
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
    spool_id = spool.id

    ams_data = [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "remain": 50}]}]
    status = MagicMock()
    status.raw_data = {"ams": {"ams": ams_data}}
    status.state = state
    status.connected = True

    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main.ws_manager") as mock_ws,
    ):
        mock_pm.get_printer.return_value = MagicMock(name="X1C", serial_number="REMAINTEST")
        mock_pm.get_status.return_value = status
        mock_pm.get_client.return_value = MagicMock()
        mock_pm.get_model.return_value = "X1C"
        mock_relay.on_ams_change = AsyncMock()
        mock_ws.send_printer_status = AsyncMock()
        mock_ws.broadcast = AsyncMock()

        if loaded_as_unlocked:
            event.listen(Spool, "load", _loaded_before_the_hand_edit)
        try:
            await on_ams_change(printer.id, ams_data)
        finally:
            if loaded_as_unlocked:
                event.remove(Spool, "load", _loaded_before_the_hand_edit)

    if sent is not None:
        sent.extend(c.args[0]["type"] for c in mock_ws.broadcast.call_args_list)

    db_session.expire_all()
    return (await db_session.get(Spool, spool_id)).weight_used


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("state", ["RUNNING", "PAUSE"])
async def test_no_sync_while_printing_without_a_tracker_session(test_engine, db_session, printer_factory, state):
    assert await _weight_after_ams_push(test_engine, db_session, printer_factory, state) == 100


@pytest.mark.asyncio
@pytest.mark.integration
async def test_syncs_when_idle(test_engine, db_session, printer_factory):
    assert await _weight_after_ams_push(test_engine, db_session, printer_factory, "IDLE") == 500


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_synced_weight_is_announced(test_engine, db_session, printer_factory):
    """WU-9: other open tabs refresh weights only on inventory_changed."""
    sent: list[str] = []

    assert await _weight_after_ams_push(test_engine, db_session, printer_factory, "IDLE", sent) == 500
    assert "inventory_changed" in sent


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_weight_set_by_hand_meanwhile_is_kept(test_engine, db_session, printer_factory):
    """WL-6: the sync decided on the spool as it had loaded it. A weight set by
    hand (and so locked) after that load was overwritten, with the lock left set."""
    sent: list[str] = []
    weight = await _weight_after_ams_push(
        test_engine,
        db_session,
        printer_factory,
        "IDLE",
        sent,
        weight_used=300,
        weight_locked=True,
        loaded_as_unlocked=True,
    )

    assert weight == 300
    assert "inventory_changed" not in sent, "nothing changed, so nothing to announce"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_sync_while_a_finished_print_is_being_charged(test_engine, db_session, printer_factory):
    """WU-3: the printer reports FINISH and the tracker has taken the session,
    but the usage is not booked yet."""
    with patch("backend.app.services.usage_tracker.printer_is_charging", lambda printer_id: True):
        weight = await _weight_after_ams_push(test_engine, db_session, printer_factory, "FINISH")

    assert weight == 100
