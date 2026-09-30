"""Plate check: the chamber light it switched on is always switched back off.

on_print_start turns the chamber light on for plate detection when it was
off. It must go back off as soon as the camera is done with it -- before the
broadcast and the provider sends -- and also when anything on the way raises,
or the printer is left lit for the rest of the print.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    _timelapse_baselines,
)


@pytest.fixture(autouse=True)
def _clear_dicts():
    dicts = (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
        _timelapse_baselines,
    )
    for d in dicts:
        d.clear()
    yield
    for d in dicts:
        d.clear()


def _printer():
    printer = MagicMock()
    printer.id = 1
    printer.name = "TestP1S"
    printer.plate_detection_enabled = True
    printer.plate_detection_roi_x = None
    printer.plate_detection_roi_y = None
    printer.plate_detection_roi_w = None
    printer.plate_detection_roi_h = None
    printer.auto_archive = False
    printer.external_camera_enabled = False
    printer.external_camera_url = None
    return printer


async def _run_print_start(events: list[str], *, check_plate_empty, pause_side_effect=None):
    printer = _printer()

    client = MagicMock()
    client.state = SimpleNamespace(chamber_light=False)
    client.set_chamber_light = MagicMock(side_effect=lambda on: events.append(f"light:{'on' if on else 'off'}"))
    client.pause_print = MagicMock(side_effect=pause_side_effect or (lambda: events.append("pause")))

    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        found = printer if "from printers" in sql else None
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=found),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[found] if found else []))),
        )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_router)

    async def broadcast(_message):
        events.append("broadcast")

    async def notify(**_kwargs):
        events.append("notify")

    with (
        patch("backend.app.main.async_session", return_value=session),
        patch("backend.app.main.notification_service") as mock_notif,
        patch("backend.app.main.smart_plug_manager") as mock_plug,
        patch("backend.app.main.ws_manager") as mock_ws,
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock),
        patch("backend.app.main._capture_snapshot_for_notification", new_callable=AsyncMock, return_value=b"jpeg"),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._load_objects_from_archive"),
        patch("backend.app.main._store_spoolman_print_data", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.services.plate_detection.check_plate_empty", check_plate_empty),
    ):
        mock_notif.on_plate_not_empty = AsyncMock(side_effect=notify)
        mock_notif.on_print_start = AsyncMock()
        mock_plug.on_print_start = AsyncMock()
        mock_ws.broadcast = AsyncMock(side_effect=broadcast)
        mock_ws.send_print_start = AsyncMock()
        mock_ws.send_archive_updated = AsyncMock()
        mock_relay.on_print_start = AsyncMock()
        mock_pm.get_client = MagicMock(return_value=client)
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "Benchy.3mf", "subtask_name": "Benchy"})


def _objects_on_plate():
    return AsyncMock(
        return_value=SimpleNamespace(needs_calibration=False, is_empty=False, confidence=0.9, difference_percent=12.0)
    )


@pytest.mark.asyncio
async def test_light_goes_off_before_the_notifications_are_sent():
    events: list[str] = []

    await _run_print_start(events, check_plate_empty=_objects_on_plate())

    assert events.index("light:on") < events.index("light:off")
    assert events.index("light:off") < events.index("broadcast")
    assert events.index("light:off") < events.index("notify")


@pytest.mark.asyncio
async def test_light_goes_off_when_detection_raises():
    events: list[str] = []

    await _run_print_start(events, check_plate_empty=AsyncMock(side_effect=RuntimeError("camera gone")))

    assert events == ["light:on", "light:off"]


@pytest.mark.asyncio
async def test_light_goes_off_when_pausing_raises():
    events: list[str] = []

    await _run_print_start(
        events, check_plate_empty=_objects_on_plate(), pause_side_effect=RuntimeError("mqtt disconnected")
    )

    assert "light:off" in events
    assert "notify" not in events
