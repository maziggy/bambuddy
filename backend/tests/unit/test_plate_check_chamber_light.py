"""Plate check: the chamber light it switched on is always switched back off.

on_print_start turns the chamber light on for plate detection when it was
off. It must go back off once the camera is done with it -- also when
anything on the way raises, or the printer is left lit for the rest of the
print. It holds the light through the camera-light count rather than
switching it itself (SP-C11), so it goes off after that count's grace, and
not at all while a live view opened meanwhile still holds it.
"""

import asyncio
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
def _light_count(monkeypatch):
    from backend.app.services import camera_light as svc

    monkeypatch.setattr(svc, "_holders", {})
    monkeypatch.setattr(svc, "_lit_at", {})
    monkeypatch.setattr(svc, "_off_tasks", {})
    monkeypatch.setattr(svc, "OFF_GRACE_SECONDS", 0)
    # The camera-light mode is off: the plate check lights the chamber anyway.
    monkeypatch.setattr(svc, "_light_settings", AsyncMock(return_value=("off", 2.0)))


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
    BROADCASTS.clear()
    yield
    for d in dicts:
        d.clear()


BROADCASTS: list[dict] = []


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

    async def snapshot(*_args):
        events.append("snapshot")
        return b"jpeg"

    client = MagicMock()
    client.state = SimpleNamespace(chamber_light=False)

    def set_light(on: bool) -> bool:
        events.append(f"light:{'on' if on else 'off'}")
        return True

    client.set_chamber_light = MagicMock(side_effect=set_light)
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

    async def broadcast(message):
        events.append("broadcast")
        BROADCASTS.append(message)

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
        patch("backend.app.main._capture_snapshot_for_notification", side_effect=snapshot),
        patch("backend.app.services.camera_light._get_client", return_value=client),
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
        # The light goes off after the camera-light grace (0 here).
        from backend.app.services import camera_light as svc

        await asyncio.gather(*svc._off_tasks.values())


def _objects_on_plate():
    return AsyncMock(
        return_value=SimpleNamespace(needs_calibration=False, is_empty=False, confidence=0.9, difference_percent=12.0)
    )


@pytest.mark.asyncio
async def test_light_is_on_for_the_photo_and_goes_off_after():
    events: list[str] = []

    await _run_print_start(events, check_plate_empty=_objects_on_plate())

    assert events.index("light:on") < events.index("snapshot") < events.index("light:off")
    assert events.count("light:off") == 1


@pytest.mark.asyncio
async def test_light_goes_off_when_detection_raises():
    events: list[str] = []

    await _run_print_start(events, check_plate_empty=AsyncMock(side_effect=RuntimeError("camera gone")))

    assert events.count("light:on") == events.count("light:off") == 1
    assert events.index("light:on") < events.index("light:off")


@pytest.mark.asyncio
async def test_a_live_view_opened_during_the_check_keeps_the_light():
    """SP-C11: the viewer found the light already on and so never took it;
    the check then switched it off under them."""
    from backend.app.services.camera_light import camera_light

    events: list[str] = []
    viewer = camera_light(_printer(), wait=False)

    async def check_while_a_viewer_arrives(**_kwargs):
        await viewer.__aenter__()
        return SimpleNamespace(needs_calibration=False, is_empty=True, confidence=0.9, difference_percent=0.0)

    with patch("backend.app.services.camera_light._light_settings", AsyncMock(return_value=("all", 0.0))):
        await _run_print_start(events, check_plate_empty=AsyncMock(side_effect=check_while_a_viewer_arrives))

    assert "light:on" in events
    assert "light:off" not in events
    await viewer.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_light_goes_off_when_pausing_raises():
    events: list[str] = []

    await _run_print_start(
        events, check_plate_empty=_objects_on_plate(), pause_side_effect=RuntimeError("mqtt disconnected")
    )

    assert "light:off" in events
    assert "notify" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("check", "reason"),
    [
        (
            AsyncMock(
                return_value=SimpleNamespace(
                    needs_calibration=False,
                    is_empty=True,
                    confidence=0.0,
                    difference_percent=0.0,
                    failure_reason="no_camera_frame",
                    message="Failed to capture camera frame from any source",
                )
            ),
            "no_camera_frame",
        ),
        (AsyncMock(side_effect=RuntimeError("camera gone")), "error"),
        (
            AsyncMock(
                return_value=SimpleNamespace(
                    needs_calibration=True,
                    is_empty=True,
                    confidence=0.0,
                    difference_percent=0.0,
                    failure_reason=None,
                    message="No calibration - please calibrate with empty plate first",
                )
            ),
            "not_calibrated",
        ),
    ],
)
async def test_a_check_that_could_not_run_says_so(check, reason, caplog):
    """It still does not block the print, but it is no longer reported as an
    empty plate."""
    import logging

    events: list[str] = []
    with caplog.at_level(logging.WARNING):
        await _run_print_start(events, check_plate_empty=check)

    assert "pause" not in events
    assert [m for m in BROADCASTS if m["type"] == "plate_check_failed"] == [
        {"type": "plate_check_failed", "printer_id": 1, "printer_name": "TestP1S", "reason": reason}
    ]
    assert any("could not run" in r.message for r in caplog.records if r.levelno == logging.WARNING)
    assert not any("Plate is empty" in r.message for r in caplog.records)
