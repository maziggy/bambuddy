"""A plate check that could not run says why instead of reporting an empty plate."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services import plate_detection
from backend.app.services.plate_detection import PlateDetectionResult, check_plate_empty


@pytest.mark.asyncio
async def test_no_camera_frame():
    with (
        patch.object(plate_detection, "OPENCV_AVAILABLE", True),
        patch.object(plate_detection, "capture_camera_image", AsyncMock(return_value=(None, None))),
    ):
        result = await check_plate_empty(printer_id=1, ip_address="10.0.0.1", access_code="x", model="X1C")

    assert result.failure_reason == "no_camera_frame"
    assert result.to_dict()["failure_reason"] == "no_camera_frame"


@pytest.mark.asyncio
async def test_no_opencv():
    with patch.object(plate_detection, "OPENCV_AVAILABLE", False):
        result = await check_plate_empty(printer_id=1, ip_address="10.0.0.1", access_code="x", model="X1C")

    assert result.failure_reason == "opencv_missing"


def test_a_check_that_ran_has_no_failure_reason():
    result = PlateDetectionResult(is_empty=True, confidence=0.9, difference_percent=1.0, message="ok")

    assert result.to_dict()["failure_reason"] is None
