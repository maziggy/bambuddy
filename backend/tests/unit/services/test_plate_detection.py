"""Unit tests for plate detection service."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock cv2 and numpy before importing the module
cv2_mock = MagicMock()
np_mock = MagicMock()


class TestPlateDetectionResult:
    """Tests for PlateDetectionResult class."""

    def test_result_to_dict(self):
        """Verify PlateDetectionResult.to_dict() returns correct structure."""
        with patch.dict("sys.modules", {"cv2": cv2_mock, "numpy": np_mock}):
            from backend.app.services.plate_detection import PlateDetectionResult

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
            from backend.app.services.plate_detection import PlateDetectionResult

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
            from backend.app.services.plate_detection import PlateDetectionResult

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
