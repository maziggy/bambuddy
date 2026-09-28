"""on_print_start's plate-check block, on the two things the AI backend changed.

Both are only visible from main.py -- the service-level tests in
test_ai_bed_check.py cannot see either:

* The print-start call site actually forwards ``PRINT_START_DEADLINE_SECONDS``
  to ``check_plate_empty``. Everything below it treats the deadline as given;
  if this one kwarg were dropped, the AI backend would silently revert to the
  uncapped 60s ceiling and a stuck vision model would stall print start with
  nothing failing anywhere.
* The ``metric_str`` branch. ``difference_percent`` is an OpenCV-only
  pixel-diff value and is ``None`` for the AI backend, so the original
  ``f"Diff: {…:.1f}%"`` would raise inside the pause path -- i.e. the print
  would NOT be paused with objects on the plate. The string it produces is
  user-visible (it goes out on the plate_not_empty websocket message), so the
  assertions pin the text, not just the absence of an exception.
"""

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
from backend.app.services.bedcheck_ai import PRINT_START_DEADLINE_SECONDS
from backend.app.services.plate_detection import PlateDetectionResult

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_dicts():
    for d in (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
        _timelapse_baselines,
    ):
        d.clear()
    yield
    for d in (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
        _timelapse_baselines,
    ):
        d.clear()


def _printer():
    printer = MagicMock()
    printer.id = 1
    printer.name = "X1C"
    printer.model = "X1C"
    printer.ip_address = "192.168.1.100"
    printer.access_code = "12345678"
    printer.plate_detection_enabled = True
    printer.bedcheck_backend_override = "ai"
    printer.external_camera_enabled = False
    printer.external_camera_url = None
    printer.external_camera_type = None
    printer.external_camera_snapshot_url = None
    printer.plate_detection_roi_x = None
    printer.plate_detection_roi_y = None
    printer.plate_detection_roi_w = None
    printer.plate_detection_roi_h = None
    # False so on_print_start returns right after the plate-check block: the
    # archive machinery below it is irrelevant to both assertions here.
    printer.auto_archive = False
    return printer


async def _run_print_start(plate_result: PlateDetectionResult):
    """Drive on_print_start through the plate-check block with a canned
    verdict, returning the mocked check_plate_empty and ws_manager so the
    caller can assert on the kwargs it was given and the message it produced.
    """
    printer = _printer()

    def execute_router(stmt, *_args, **_kwargs):
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=printer),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[printer]))),
        )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_router)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()

    check_plate_empty = AsyncMock(return_value=plate_result)

    with (
        patch("backend.app.main.async_session") as session_maker,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.printer_manager") as pm,
        # Imported inside on_print_start, so it has to be patched at its source.
        patch("backend.app.services.plate_detection.check_plate_empty", new=check_plate_empty),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
    ):
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        notif.on_plate_not_empty = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.broadcast = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        relay.on_print_start = AsyncMock()
        client = MagicMock()
        # Truthy chamber_light => light_was_off is False => the 2.5s
        # light-settle sleep is skipped and the test stays fast.
        client.state = MagicMock(chamber_light=True)
        pm.get_client = MagicMock(return_value=client)
        pm.get_status = MagicMock(return_value=MagicMock())
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TESTPLATE"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "/data/Metadata/plate_1.gcode", "subtask_name": "Bracket"})

    return check_plate_empty, ws, notif


def _plate_message(ws) -> str:
    for call in ws.broadcast.await_args_list:
        payload = call.args[0]
        if payload.get("type") == "plate_not_empty":
            return payload["message"]
    raise AssertionError("on_print_start never broadcast a plate_not_empty message")


@pytest.mark.asyncio
async def test_print_start_forwards_the_deadline_to_check_plate_empty():
    """The safety-path bound only exists if this call site passes it. A mutant
    that drops the kwarg leaves check_plate_empty on its uncapped default and
    nothing else in the suite notices."""
    result = PlateDetectionResult(
        is_empty=True, confidence=0.95, difference_percent=None, ai_confidence=0.95, message="ok", backend="ai"
    )

    check_plate_empty, _ws, _notif = await _run_print_start(result)

    check_plate_empty.assert_awaited_once()
    kwargs = check_plate_empty.await_args.kwargs
    assert kwargs["deadline_seconds"] == PRINT_START_DEADLINE_SECONDS
    assert kwargs["deadline_seconds"] == 20.0
    assert kwargs["printer_id"] == 1
    assert kwargs["backend_override"] == "ai"


@pytest.mark.asyncio
async def test_ai_verdict_reports_confidence_instead_of_a_pixel_diff():
    """difference_percent is None for the AI backend; the pause path must fall
    through to ai_confidence rather than format None with `:.1f`."""
    result = PlateDetectionResult(
        is_empty=False,
        confidence=0.87,
        difference_percent=None,
        ai_confidence=0.87,
        message="objects",
        backend="ai",
    )

    _check, ws, notif = await _run_print_start(result)

    message = _plate_message(ws)
    assert "Confidence: 87%" in message
    assert "Diff:" not in message
    notif.on_plate_not_empty.assert_awaited_once()
    assert notif.on_plate_not_empty.await_args.kwargs["difference_percent"] is None
    assert notif.on_plate_not_empty.await_args.kwargs["ai_confidence"] == 0.87


@pytest.mark.asyncio
async def test_opencv_verdict_still_reports_the_pixel_diff():
    """Regression guard on the branch the AI backend was threaded through: an
    OpenCV verdict must read exactly as it did before."""
    result = PlateDetectionResult(
        is_empty=False, confidence=0.7, difference_percent=12.34, ai_confidence=None, message="objects"
    )

    _check, ws, _notif = await _run_print_start(result)

    message = _plate_message(ws)
    assert "Diff: 12.3%" in message
    assert "Confidence:" not in message


@pytest.mark.asyncio
async def test_verdict_with_neither_metric_falls_back_to_not_available():
    """A not-empty verdict carrying neither metric (the AI fail-open shape
    never reaches here, but a future backend could) must still pause the print
    and say so, not raise inside the pause path."""
    result = PlateDetectionResult(
        is_empty=False, confidence=0.5, difference_percent=None, ai_confidence=None, message="objects", backend="ai"
    )

    _check, ws, _notif = await _run_print_start(result)

    assert "Diff: N/A" in _plate_message(ws)
