"""Unit tests for plate detection service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock cv2 and numpy before importing the module
cv2_mock = MagicMock()
np_mock = MagicMock()


def _mock_async_session(row_value=None, raise_on_enter=None):
    """Build a mock 'async_session' callable shaped like the real
    backend.app.core.database.async_session -- an async-context-manager
    factory whose session's .execute(select(...)).scalar_one_or_none()
    returns an object with .value == row_value (or None for "no row").

    get_bedcheck_backend() resolves async_session via a *local* import
    inside its own function body (from backend.app.core.database import
    async_session), so this file -- which carries no DB fixture of its own
    -- patches the real source attribute, backend.app.core.database.async_session,
    rather than anything on the reloaded plate_detection module.

    If raise_on_enter is given, entering the context manager raises it
    (simulates a DB connection failure before any query runs).
    """
    mock_cm = MagicMock()
    if raise_on_enter is not None:
        mock_cm.__aenter__ = AsyncMock(side_effect=raise_on_enter)
    else:
        mock_result = MagicMock()
        row = MagicMock(value=row_value) if row_value is not None else None
        mock_result.scalar_one_or_none.return_value = row
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_cm)


class TestPlateDetectionResult:
    """Tests for PlateDetectionResult class."""

    def test_result_to_dict(self):
        """Verify PlateDetectionResult.to_dict() returns correct structure."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)
            PlateDetectionResult = pd_module.PlateDetectionResult

            result = PlateDetectionResult(
                is_empty=True,
                confidence=0.95,
                difference_percent=0.5,
                message="Test message",
                debug_image=None,
                needs_calibration=False,
            )

            d = result.to_dict()

            assert d["is_empty"] is True
            assert d["confidence"] == 0.95
            assert d["difference_percent"] == 0.5
            assert d["message"] == "Test message"
            assert d["has_debug_image"] is False
            assert d["needs_calibration"] is False

    def test_result_with_debug_image(self):
        """Verify has_debug_image is True when debug_image is provided."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)
            PlateDetectionResult = pd_module.PlateDetectionResult

            result = PlateDetectionResult(
                is_empty=False,
                confidence=0.8,
                difference_percent=5.0,
                message="Objects detected",
                debug_image=b"fake_image_data",
                needs_calibration=False,
            )

            d = result.to_dict()
            assert d["has_debug_image"] is True

    def test_result_needs_calibration(self):
        """Verify needs_calibration flag is preserved."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)
            PlateDetectionResult = pd_module.PlateDetectionResult

            result = PlateDetectionResult(
                is_empty=True,
                confidence=0.0,
                difference_percent=0.0,
                message="No calibration",
                needs_calibration=True,
            )

            d = result.to_dict()
            assert d["needs_calibration"] is True


class TestPlateDetector:
    """Tests for PlateDetector class."""

    def test_detector_initialization(self):
        """Verify PlateDetector initializes with default values."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            # Re-import to get fresh module
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            # Mock OPENCV_AVAILABLE
            pd_module.OPENCV_AVAILABLE = True

            detector = pd_module.PlateDetector()
            assert detector.roi == (0.15, 0.35, 0.70, 0.55)
            assert detector.difference_threshold == 1.0

    def test_detector_custom_roi(self):
        """Verify PlateDetector accepts custom ROI."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            pd_module.OPENCV_AVAILABLE = True

            custom_roi = (0.1, 0.2, 0.8, 0.6)
            detector = pd_module.PlateDetector(roi=custom_roi)
            assert detector.roi == custom_roi

    def test_detector_raises_without_opencv(self):
        """Verify PlateDetector raises when OpenCV not available."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            pd_module.OPENCV_AVAILABLE = False

            with pytest.raises(RuntimeError, match="OpenCV is not installed"):
                pd_module.PlateDetector()


class TestCalibrationStatus:
    """Tests for calibration status functions."""

    def test_get_calibration_status_no_opencv(self):
        """Verify calibration status when OpenCV not available."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            pd_module.OPENCV_AVAILABLE = False

            status = pd_module.get_calibration_status(1)

            assert status["available"] is False
            assert status["calibrated"] is False
            assert status["reference_count"] == 0
            assert "OpenCV not available" in status["message"]

    def test_is_plate_detection_available_true(self):
        """Verify is_plate_detection_available returns True when OpenCV available."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            pd_module.OPENCV_AVAILABLE = True
            assert pd_module.is_plate_detection_available() is True

    def test_is_plate_detection_available_false(self):
        """Verify is_plate_detection_available returns False when OpenCV not available."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            pd_module.OPENCV_AVAILABLE = False
            assert pd_module.is_plate_detection_available() is False


class TestDeleteCalibration:
    """Tests for delete_calibration function."""

    def test_delete_calibration_no_opencv(self):
        """Verify delete_calibration returns False when OpenCV not available."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            pd_module.OPENCV_AVAILABLE = False

            result = pd_module.delete_calibration(1)
            assert result is False


class TestSelectorDispatch:
    """Tests for check_plate_empty()'s bedcheck_backend dispatcher (AI bed-check).

    get_bedcheck_backend() reads its one setting via a *local* import inside
    its own function body -- `from backend.app.core.database import
    async_session` at call time, not a module-level binding -- so it always
    resolves whatever backend.app.core.database.async_session currently is,
    including the mock installed by _mock_async_session() below. This file
    carries no DB fixture of its own (verified: no db/async_session/conftest
    reference anywhere else in it), so that guarded, always-resolving read is
    the only way these tests can exercise the dispatcher at all.
    """

    @pytest.mark.asyncio
    async def test_default_backend_is_opencv(self):
        """No bedcheck_backend row in DB -> get_bedcheck_backend() returns
        'opencv' -> check_plate_empty() dispatches to _check_plate_empty_opencv
        and never touches the bedcheck_ai module."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            sentinel = pd_module.PlateDetectionResult(
                is_empty=True, confidence=0.0, difference_percent=0.0, message="opencv result"
            )
            with (
                patch("backend.app.core.database.async_session", _mock_async_session(row_value=None)),
                patch.object(pd_module, "_check_plate_empty_opencv", AsyncMock(return_value=sentinel)) as mock_opencv,
                patch("backend.app.services.bedcheck_ai.check_bed_ai", AsyncMock()) as mock_check_bed_ai,
            ):
                result = await pd_module.check_plate_empty(1, "10.0.0.5", "code", "X1C")

            mock_opencv.assert_awaited_once()
            mock_check_bed_ai.assert_not_awaited()
            assert result is sentinel

    @pytest.mark.asyncio
    async def test_ai_backend_dispatches_to_check_bed_ai(self):
        """bedcheck_backend='ai' row present -> check_plate_empty() captures a
        frame and dispatches to bedcheck_ai.check_bed_ai with it, never calls
        _check_plate_empty_opencv."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            sentinel = pd_module.PlateDetectionResult(
                is_empty=False, confidence=0.9, difference_percent=90.0, message="ai result"
            )
            with (
                patch("backend.app.core.database.async_session", _mock_async_session(row_value="ai")),
                patch.object(
                    pd_module, "capture_camera_image", AsyncMock(return_value=(b"\xff\xd8fake", "built-in"))
                ) as mock_capture,
                patch.object(pd_module, "_check_plate_empty_opencv", AsyncMock()) as mock_opencv,
                patch(
                    "backend.app.services.bedcheck_ai.check_bed_ai", AsyncMock(return_value=sentinel)
                ) as mock_check_bed_ai,
            ):
                result = await pd_module.check_plate_empty(1, "10.0.0.5", "code", "X1C")

            mock_capture.assert_awaited_once()
            mock_check_bed_ai.assert_awaited_once_with(1, b"\xff\xd8fake", "built-in")
            mock_opencv.assert_not_awaited()
            assert result is sentinel

    @pytest.mark.asyncio
    async def test_ai_backend_fails_open_when_capture_fails(self):
        """bedcheck_backend='ai' but no frame captured -> the same
        'Failed to capture camera frame from any source' fail-open result as
        the opencv path uses, and bedcheck_ai.check_bed_ai is never called."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            with (
                patch("backend.app.core.database.async_session", _mock_async_session(row_value="ai")),
                patch.object(pd_module, "capture_camera_image", AsyncMock(return_value=(None, "unknown"))),
                patch("backend.app.services.bedcheck_ai.check_bed_ai", AsyncMock()) as mock_check_bed_ai,
            ):
                result = await pd_module.check_plate_empty(1, "10.0.0.5", "code", "X1C")

            mock_check_bed_ai.assert_not_awaited()
            assert result.is_empty is True
            assert result.confidence == 0.0
            assert "Failed to capture camera frame" in result.message

    @pytest.mark.parametrize("garbage_value", ["both", "nonsense", "", "OpenCV", "AI"])
    @pytest.mark.asyncio
    async def test_unknown_backend_value_falls_back_to_opencv(self, garbage_value):
        """Any unrecognized bedcheck_backend row value (including 'both' --
        regression coverage that removing the earlier-considered 'both' mode
        didn't leave a dangling code path that half-handles it) resolves to
        'opencv', never raises."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            sentinel = pd_module.PlateDetectionResult(
                is_empty=True, confidence=0.0, difference_percent=0.0, message="opencv result"
            )
            with (
                patch("backend.app.core.database.async_session", _mock_async_session(row_value=garbage_value)),
                patch.object(pd_module, "_check_plate_empty_opencv", AsyncMock(return_value=sentinel)) as mock_opencv,
                patch("backend.app.services.bedcheck_ai.check_bed_ai", AsyncMock()) as mock_check_bed_ai,
            ):
                result = await pd_module.check_plate_empty(1, "10.0.0.5", "code", "X1C")

            mock_opencv.assert_awaited_once()
            mock_check_bed_ai.assert_not_awaited()
            assert result is sentinel

    @pytest.mark.asyncio
    async def test_backend_read_failure_falls_back_to_opencv(self):
        """A DB error inside get_bedcheck_backend()'s read (pool exhaustion,
        locked SQLite, disconnected engine) must not propagate -- it falls
        back to 'opencv' and check_plate_empty() still returns a normal
        result instead of raising. This is the test that most directly
        defends the 'no new fail-closed path' requirement: camera.py's
        manual-check route holds no try/except around its call to
        check_plate_empty."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            sentinel = pd_module.PlateDetectionResult(
                is_empty=True, confidence=0.0, difference_percent=0.0, message="opencv result"
            )
            db_error = ConnectionError("pool exhausted")
            with (
                patch(
                    "backend.app.core.database.async_session",
                    _mock_async_session(raise_on_enter=db_error),
                ),
                patch.object(pd_module, "_check_plate_empty_opencv", AsyncMock(return_value=sentinel)) as mock_opencv,
                patch("backend.app.services.bedcheck_ai.check_bed_ai", AsyncMock()) as mock_check_bed_ai,
            ):
                # Must not raise.
                result = await pd_module.check_plate_empty(1, "10.0.0.5", "code", "X1C")

            mock_opencv.assert_awaited_once()
            mock_check_bed_ai.assert_not_awaited()
            assert result is sentinel

    @pytest.mark.asyncio
    async def test_get_bedcheck_backend_read_failure_returns_opencv_directly(self):
        """Narrower unit check on get_bedcheck_backend() itself (not routed
        through the dispatcher): a raising DB session resolves to 'opencv',
        never propagates."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            import importlib

            import backend.app.services.plate_detection as pd_module

            importlib.reload(pd_module)

            with patch(
                "backend.app.core.database.async_session",
                _mock_async_session(raise_on_enter=RuntimeError("db down")),
            ):
                backend = await pd_module.get_bedcheck_backend()

            assert backend == "opencv"
