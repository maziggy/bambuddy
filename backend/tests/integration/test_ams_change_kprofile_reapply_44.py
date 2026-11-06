"""An AMS change no longer re-selects a spool's stored K-profile itself.

It used to set the slot back to the stored profile whenever its cali_idx
differed, so a profile picked on purpose in Bambu Studio or on the printer's
screen was undone by the next AMS report that changed the slot. The #3219
drift check already puts back a *lost* selection (cali_idx -1 or missing)
while the printer is idle, and leaves a different real profile, a deliberate
"Default" and the other hotend's calibration alone. It is now the only path.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spool_k_profile import SpoolKProfile
from backend.app.services import kprofile_drift
from backend.app.services.usage_tracker import _active_sessions

TAG_UID = "A1B2C3D4E5F60708"
TRAY_UUID = "0123456789ABCDEF0123456789ABCDEF"


@pytest.fixture(autouse=True)
def _no_sessions():
    _active_sessions.clear()
    yield
    _active_sessions.clear()


async def _ams_push(test_engine, db_session, printer_factory, *, live_cali_idx, state="IDLE") -> MagicMock:
    from backend.app.main import on_ams_change

    printer = await printer_factory(name="X1C")
    spool = Spool(material="PLA", label_weight=1000, weight_used=0, tag_uid=TAG_UID, tray_uuid=TRAY_UUID)
    db_session.add(spool)
    await db_session.flush()
    db_session.add(
        SpoolKProfile(
            spool_id=spool.id, printer_id=printer.id, extruder=0, nozzle_diameter="0.4", k_value=0.02, cali_idx=7
        )
    )
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

    tray = {
        "id": 0,
        "tray_type": "PLA",
        "tray_color": "FF0000FF",
        "tag_uid": TAG_UID,
        "tray_uuid": TRAY_UUID,
        "tray_info_idx": "GFA00",
        "remain": -1,
    }
    if live_cali_idx is not None:
        tray["cali_idx"] = live_cali_idx
    ams_data = [{"id": 0, "tray": [tray]}]
    status = MagicMock()
    status.raw_data = {"ams": {"ams": ams_data}}
    status.state = state
    status.connected = True
    client = MagicMock()

    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main.ws_manager") as mock_ws,
    ):
        mock_pm.get_printer.return_value = MagicMock(name="X1C", serial_number="KREAPPLY")
        mock_pm.get_status.return_value = status
        mock_pm.get_client.return_value = client
        mock_pm.get_model.return_value = "X1C"
        mock_relay.on_ams_change = AsyncMock()
        mock_ws.send_printer_status = AsyncMock()
        mock_ws.broadcast = AsyncMock()

        await on_ams_change(printer.id, ams_data)

    return client


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("live", [-1, None, 3])
async def test_the_ams_change_leaves_the_selection_to_the_drift_check(test_engine, db_session, printer_factory, live):
    client = await _ams_push(test_engine, db_session, printer_factory, live_cali_idx=live)

    client.extrusion_cali_sel.assert_not_called()


@pytest.mark.parametrize(("live", "picked_up"), [(-1, True), (None, True), (3, False)])
def test_the_drift_check_picks_up_only_a_lost_selection(live, picked_up):
    kprofile_drift._watches.clear()
    tray = {"id": 0, "tray_type": "PLA"}
    if live is not None:
        tray["cali_idx"] = live
    state = MagicMock(connected=True, state="IDLE", raw_data={"ams": [{"id": 0, "tray": [tray]}]})

    try:
        assert kprofile_drift.needs_check(4242, state) is picked_up
    finally:
        kprofile_drift._watches.clear()
