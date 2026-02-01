"""Build plate empty detection using OpenCV.

Analyzes camera frames to detect if there are objects on the build plate.
Uses calibration-based difference detection - compares current frame to
a reference image of the empty plate.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Optional OpenCV import - feature disabled if not available
try:
    import cv2
    import numpy as np

    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False
    logger.info("OpenCV not available - plate detection feature disabled")


def _get_calibration_dir() -> Path:
    """Get the calibration directory from settings (ensures persistence in Docker)."""
    from backend.app.core.config import settings

    return settings.plate_calibration_dir


class PlateDetectionResult:
    """Result of plate detection analysis."""

    def __init__(
        self,
        is_empty: bool,
        confidence: float,
        difference_percent: float,
        message: str,
        debug_image: bytes | None = None,
        needs_calibration: bool = False,
    ):
        self.is_empty = is_empty
        self.confidence = confidence  # 0.0 to 1.0
        self.difference_percent = difference_percent  # How different from reference
        self.message = message
        self.debug_image = debug_image  # Optional annotated image for debugging
        self.needs_calibration = needs_calibration  # True if no reference image exists

    def to_dict(self) -> dict:
        return {
            "is_empty": bool(self.is_empty),
            "confidence": float(round(self.confidence, 2)),
            "difference_percent": float(round(self.difference_percent, 2)),
            "message": self.message,
            "has_debug_image": self.debug_image is not None,
            "needs_calibration": bool(self.needs_calibration),
        }


class PlateDetector:
    """Detects if the build plate is empty using calibration-based difference detection."""

    # Default region of interest (ROI) as percentage of image dimensions
    # These define where the build plate typically appears in the camera view
    # Format: (x_start%, y_start%, width%, height%)
    DEFAULT_ROI = (0.15, 0.35, 0.70, 0.55)  # Center-lower portion of frame

    # Detection thresholds for difference detection
    # Using mean pixel difference (0-100% scale)
    # Small objects may only cause 1-2% mean difference
    DEFAULT_DIFFERENCE_THRESHOLD = 1.0
    DEFAULT_BLUR_SIZE = 21  # Gaussian blur kernel size (must be odd) - unused with edge detection

    def __init__(
        self,
        roi: tuple[float, float, float, float] | None = None,
        difference_threshold: float = DEFAULT_DIFFERENCE_THRESHOLD,
        blur_size: int = DEFAULT_BLUR_SIZE,
    ):
        """Initialize the plate detector.

        Args:
            roi: Region of interest as (x%, y%, w%, h%) - percentages of image size
            difference_threshold: Percentage of pixels that must differ to trigger "not empty"
            blur_size: Gaussian blur kernel size for noise reduction
        """
        if not OPENCV_AVAILABLE:
            raise RuntimeError("OpenCV is not installed. Install with: pip install opencv-python-headless")

        self.roi = roi or self.DEFAULT_ROI
        self.difference_threshold = difference_threshold
        self.blur_size = blur_size if blur_size % 2 == 1 else blur_size + 1  # Must be odd

    # Maximum number of reference images to store per printer
    MAX_REFERENCES = 5

    def _get_metadata_path(self, printer_id: int) -> Path:
        """Get the path to the metadata JSON file for a printer."""
        _get_calibration_dir().mkdir(parents=True, exist_ok=True)
        return _get_calibration_dir() / f"printer_{printer_id}_metadata.json"

    def _load_metadata(self, printer_id: int) -> dict:
        """Load metadata for a printer's references."""
        import json

        meta_path = self._get_metadata_path(printer_id)
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"references": {}}

    def _save_metadata(self, printer_id: int, metadata: dict) -> None:
        """Save metadata for a printer's references."""
        import json

        meta_path = self._get_metadata_path(printer_id)
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

    def _get_reference_paths(self, printer_id: int) -> list[Path]:
        """Get all existing reference image paths for a printer."""
        _get_calibration_dir().mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(self.MAX_REFERENCES):
            path = _get_calibration_dir() / f"printer_{printer_id}_ref_{i}.jpg"
            if path.exists():
                paths.append(path)
        return paths

    def _get_next_reference_slot(self, printer_id: int) -> Path:
        """Get the path for the next reference image slot (cycles through slots)."""
        _get_calibration_dir().mkdir(parents=True, exist_ok=True)
        # Find first empty slot, or use oldest (slot 0) and shift others
        for i in range(self.MAX_REFERENCES):
            path = _get_calibration_dir() / f"printer_{printer_id}_ref_{i}.jpg"
            if not path.exists():
                return path
        # All slots full - return slot 0 (will be overwritten, but we rotate first)
        return _get_calibration_dir() / f"printer_{printer_id}_ref_0.jpg"

    def _rotate_references(self, printer_id: int) -> None:
        """Rotate references: delete oldest (0), shift others down."""
        # Delete slot 0
        slot0 = _get_calibration_dir() / f"printer_{printer_id}_ref_0.jpg"
        if slot0.exists():
            logger.info(f"Rotating references: removing oldest {slot0}")
            slot0.unlink()
        # Shift others down
        for i in range(1, self.MAX_REFERENCES):
            old_path = _get_calibration_dir() / f"printer_{printer_id}_ref_{i}.jpg"
            new_path = _get_calibration_dir() / f"printer_{printer_id}_ref_{i - 1}.jpg"
            if old_path.exists():
                old_path.rename(new_path)

        # Also rotate metadata
        metadata = self._load_metadata(printer_id)
        refs = metadata.get("references", {})
        new_refs = {}
        for i in range(1, self.MAX_REFERENCES):
            if str(i) in refs:
                new_refs[str(i - 1)] = refs[str(i)]
        metadata["references"] = new_refs
        self._save_metadata(printer_id, metadata)

    def get_references(self, printer_id: int) -> list[dict]:
        """Get all references with metadata for a printer.

        Returns list of dicts with: index, label, timestamp, has_image
        """

        metadata = self._load_metadata(printer_id)
        refs = metadata.get("references", {})
        result = []

        for i in range(self.MAX_REFERENCES):
            path = _get_calibration_dir() / f"printer_{printer_id}_ref_{i}.jpg"
            if path.exists():
                ref_meta = refs.get(str(i), {})
                result.append(
                    {
                        "index": i,
                        "label": ref_meta.get("label", ""),
                        "timestamp": ref_meta.get("timestamp", ""),
                        "has_image": True,
                    }
                )

        return result

    def update_reference_label(self, printer_id: int, index: int, label: str) -> bool:
        """Update the label for a reference."""
        if index < 0 or index >= self.MAX_REFERENCES:
            return False

        path = _get_calibration_dir() / f"printer_{printer_id}_ref_{index}.jpg"
        if not path.exists():
            return False

        metadata = self._load_metadata(printer_id)
        if "references" not in metadata:
            metadata["references"] = {}
        if str(index) not in metadata["references"]:
            metadata["references"][str(index)] = {}

        metadata["references"][str(index)]["label"] = label
        self._save_metadata(printer_id, metadata)
        return True

    def delete_reference(self, printer_id: int, index: int) -> bool:
        """Delete a specific reference by index."""
        if index < 0 or index >= self.MAX_REFERENCES:
            return False

        path = _get_calibration_dir() / f"printer_{printer_id}_ref_{index}.jpg"
        if not path.exists():
            return False

        # Delete image
        logger.info(f"Deleting reference {index} for printer {printer_id}: {path}")
        path.unlink()

        # Remove from metadata
        metadata = self._load_metadata(printer_id)
        refs = metadata.get("references", {})
        if str(index) in refs:
            del refs[str(index)]
        metadata["references"] = refs
        self._save_metadata(printer_id, metadata)

        # Shift remaining references down to fill the gap
        for i in range(index + 1, self.MAX_REFERENCES):
            old_img = _get_calibration_dir() / f"printer_{printer_id}_ref_{i}.jpg"
            new_img = _get_calibration_dir() / f"printer_{printer_id}_ref_{i - 1}.jpg"
            if old_img.exists():
                old_img.rename(new_img)
                # Also shift metadata
                if str(i) in refs:
                    refs[str(i - 1)] = refs[str(i)]
                    del refs[str(i)]

        metadata["references"] = refs
        self._save_metadata(printer_id, metadata)
        return True

    def get_reference_thumbnail(self, printer_id: int, index: int, max_size: int = 150) -> bytes | None:
        """Get a thumbnail of a reference image.

        Returns JPEG bytes or None if not found.
        """
        path = _get_calibration_dir() / f"printer_{printer_id}_ref_{index}.jpg"
        if not path.exists():
            return None

        try:
            img = cv2.imread(str(path))
            if img is None:
                return None

            # Calculate thumbnail size maintaining aspect ratio
            h, w = img.shape[:2]
            if w > h:
                new_w = max_size
                new_h = int(h * max_size / w)
            else:
                new_h = max_size
                new_w = int(w * max_size / h)

            thumb = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            _, buffer = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buffer.tobytes()
        except Exception as e:
            logger.error(f"Error creating thumbnail: {e}")
            return None

    def _extract_roi(self, frame: np.ndarray) -> tuple[np.ndarray, int, int, int, int]:
        """Extract the region of interest from a frame.

        Returns:
            Tuple of (roi_frame, x_start, y_start, roi_width, roi_height)
        """
        height, width = frame.shape[:2]
        x_start = int(width * self.roi[0])
        y_start = int(height * self.roi[1])
        roi_width = int(width * self.roi[2])
        roi_height = int(height * self.roi[3])
        roi_frame = frame[y_start : y_start + roi_height, x_start : x_start + roi_width]
        return roi_frame, x_start, y_start, roi_width, roi_height

    def _preprocess_for_comparison(self, frame: np.ndarray) -> np.ndarray:
        """Preprocess a frame for comparison.

        Uses heavy blur to create "blob" representation - smooths out texture
        and noise while preserving large objects. Then normalizes brightness
        to reduce lighting sensitivity.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Very heavy blur to smooth texture, keep only large shapes
        blurred = cv2.GaussianBlur(gray, (51, 51), 0)
        # Normalize to 0-255 range to reduce brightness sensitivity
        normalized = cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX)
        return normalized

    def calibrate(self, image_data: bytes, printer_id: int, label: str | None = None) -> tuple[bool, str, int]:
        """Calibrate by saving a reference image of the empty plate.

        Stores up to MAX_REFERENCES (5) images per printer. When all slots are full,
        the oldest reference is removed and others are shifted.

        Args:
            image_data: JPEG image data as bytes
            printer_id: Printer database ID
            label: Optional label for this reference (e.g., "High Temp Plate")

        Returns:
            Tuple of (success, message, index) where index is the slot used
        """
        from datetime import datetime

        try:
            # Decode image
            nparr = np.frombuffer(image_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None:
                return False, "Failed to decode image", -1

            # Get existing references count
            existing_refs = self._get_reference_paths(printer_id)
            num_existing = len(existing_refs)

            # If all slots are full, rotate (remove oldest)
            if num_existing >= self.MAX_REFERENCES:
                self._rotate_references(printer_id)
                num_existing = self.MAX_REFERENCES - 1

            # Save to next available slot
            slot_index = num_existing
            reference_path = _get_calibration_dir() / f"printer_{printer_id}_ref_{slot_index}.jpg"
            write_success = cv2.imwrite(str(reference_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])

            if not write_success:
                logger.error(f"cv2.imwrite failed for {reference_path}")
                return False, "Failed to save reference image", -1

            # Verify the file actually exists and has content
            if not reference_path.exists():
                logger.error(f"Reference image not found after save: {reference_path}")
                return False, "Reference image not found after save", -1

            file_size = reference_path.stat().st_size
            if file_size < 1000:  # JPEG should be at least 1KB
                logger.error(f"Reference image too small ({file_size} bytes): {reference_path}")
                reference_path.unlink()  # Clean up invalid file
                return False, f"Reference image corrupted (only {file_size} bytes)", -1

            logger.info(f"Saved reference image: {reference_path} ({file_size} bytes)")

            # Save metadata
            metadata = self._load_metadata(printer_id)
            if "references" not in metadata:
                metadata["references"] = {}
            metadata["references"][str(slot_index)] = {
                "label": label or "",
                "timestamp": datetime.now().isoformat(),
            }
            self._save_metadata(printer_id, metadata)

            logger.info(
                f"Saved plate calibration reference {slot_index + 1}/{self.MAX_REFERENCES} for printer {printer_id}"
            )
            return True, f"Calibration saved ({slot_index + 1}/{self.MAX_REFERENCES} references)", slot_index

        except Exception as e:
            logger.exception("Error during plate calibration")
            # Don't expose exception details to user - log has full info
            error_type = type(e).__name__
            return False, f"Calibration error: {error_type}", -1

    def get_calibration_count(self, printer_id: int) -> int:
        """Get the number of calibration references for a printer."""
        return len(self._get_reference_paths(printer_id))

    def has_calibration(self, printer_id: int, plate_type: str | None = None) -> bool:
        """Check if a printer has any calibration reference images."""
        return len(self._get_reference_paths(printer_id)) > 0

    def delete_calibration(self, printer_id: int, plate_type: str | None = None) -> bool:
        """Delete all calibration reference images for a printer."""
        paths = self._get_reference_paths(printer_id)
        if not paths:
            return False
        for path in paths:
            path.unlink()
        logger.info(f"Deleted {len(paths)} plate calibration reference(s) for printer {printer_id}")
        return True

    def analyze_frame(
        self, image_data: bytes, printer_id: int, plate_type: str | None = None, include_debug_image: bool = False
    ) -> PlateDetectionResult:
        """Analyze a camera frame to detect if the plate is empty.

        Compares the current frame to all calibration reference images and uses
        the best match (lowest difference) for the final result.

        Args:
            image_data: JPEG image data as bytes
            printer_id: Printer database ID (for reference lookup)
            plate_type: Unused - kept for API compatibility
            include_debug_image: If True, include annotated image in result

        Returns:
            PlateDetectionResult with analysis results
        """
        try:
            # Check for calibration
            reference_paths = self._get_reference_paths(printer_id)
            if not reference_paths:
                return PlateDetectionResult(
                    is_empty=True,  # Default to empty when not calibrated
                    confidence=0.0,
                    difference_percent=0.0,
                    message="No calibration - please calibrate with empty plate first",
                    needs_calibration=True,
                )

            # Decode current image
            nparr = np.frombuffer(image_data, np.uint8)
            current_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if current_frame is None:
                return PlateDetectionResult(
                    is_empty=True,
                    confidence=0.0,
                    difference_percent=0.0,
                    message="Failed to decode current image",
                )

            # Extract ROI from current frame
            current_roi, x_start, y_start, roi_width, roi_height = self._extract_roi(current_frame)
            current_processed = self._preprocess_for_comparison(current_roi)

            # Compare against all references, find best match (lowest difference)
            best_difference_percent = float("inf")
            best_ref_idx = -1
            best_diff = None

            for idx, ref_path in enumerate(reference_paths):
                # Load reference image
                reference_frame = cv2.imread(str(ref_path), cv2.IMREAD_COLOR)
                if reference_frame is None:
                    continue

                # Ensure same dimensions
                if current_frame.shape != reference_frame.shape:
                    reference_frame = cv2.resize(reference_frame, (current_frame.shape[1], current_frame.shape[0]))

                # Extract ROI and preprocess
                reference_roi, _, _, _, _ = self._extract_roi(reference_frame)
                reference_processed = self._preprocess_for_comparison(reference_roi)

                # Calculate absolute difference
                diff = cv2.absdiff(current_processed, reference_processed)

                # Calculate mean difference as percentage
                mean_diff = np.mean(diff)
                difference_percent = (mean_diff / 255.0) * 100

                if difference_percent < best_difference_percent:
                    best_difference_percent = difference_percent
                    best_ref_idx = idx
                    best_diff = diff

            if best_ref_idx == -1:
                return PlateDetectionResult(
                    is_empty=True,
                    confidence=0.0,
                    difference_percent=0.0,
                    message="Failed to load any reference images - please recalibrate",
                    needs_calibration=True,
                )

            difference_percent = best_difference_percent

            # Determine if plate is empty (use best match)
            is_empty = difference_percent < self.difference_threshold

            # Calculate confidence
            if is_empty:
                # Higher confidence when very little difference
                confidence = 1.0 - min(1.0, difference_percent / self.difference_threshold)
            else:
                # Higher confidence when clearly different
                confidence = min(1.0, difference_percent / (self.difference_threshold * 2))

            # Generate message
            num_refs = len(reference_paths)
            if is_empty:
                message = (
                    f"Plate appears empty (difference: {difference_percent:.1f}%, ref {best_ref_idx + 1}/{num_refs})"
                )
            else:
                message = f"Objects detected on plate (difference: {difference_percent:.1f}%, best ref {best_ref_idx + 1}/{num_refs})"

            # Generate debug image if requested
            debug_image = None
            if include_debug_image and best_diff is not None:
                debug_frame = current_frame.copy()

                # Draw ROI rectangle
                cv2.rectangle(
                    debug_frame,
                    (x_start, y_start),
                    (x_start + roi_width, y_start + roi_height),
                    (0, 255, 0),
                    2,
                )

                # Create colored difference overlay
                # Red = areas that are different from reference
                # Amplify diff for visibility (multiply by 3, cap at 255)
                diff_amplified = np.minimum(best_diff * 3, 255).astype(np.uint8)
                diff_colored = cv2.cvtColor(diff_amplified, cv2.COLOR_GRAY2BGR)
                diff_colored[:, :, 0] = 0  # Remove blue
                diff_colored[:, :, 1] = 0  # Remove green
                # Red channel has the diff

                # Overlay difference on ROI
                roi_overlay = debug_frame[y_start : y_start + roi_height, x_start : x_start + roi_width]
                cv2.addWeighted(diff_colored, 0.5, roi_overlay, 0.5, 0, roi_overlay)

                # Add status text
                status_text = "EMPTY" if is_empty else "OBJECTS DETECTED"
                color = (0, 255, 0) if is_empty else (0, 0, 255)
                cv2.putText(debug_frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
                cv2.putText(
                    debug_frame,
                    f"Diff: {difference_percent:.1f}% (ref {best_ref_idx + 1}/{num_refs})",
                    (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    color,
                    2,
                )
                cv2.putText(
                    debug_frame,
                    f"Confidence: {confidence:.0%}",
                    (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    color,
                    2,
                )

                # Encode debug image as JPEG
                _, buffer = cv2.imencode(".jpg", debug_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                debug_image = buffer.tobytes()

            return PlateDetectionResult(
                is_empty=is_empty,
                confidence=confidence,
                difference_percent=difference_percent,
                message=message,
                debug_image=debug_image,
            )

        except Exception as e:
            logger.exception("Error analyzing frame for plate detection")
            return PlateDetectionResult(
                is_empty=True,  # Default to empty on error (don't block prints)
                confidence=0.0,
                difference_percent=0.0,
                message=f"Analysis error: {e!s}",
            )


async def capture_camera_image(
    printer_id: int,
    ip_address: str,
    access_code: str,
    model: str,
    external_camera_url: str | None = None,
    external_camera_type: str | None = None,
    use_external: bool = False,
) -> tuple[bytes | None, str]:
    """Capture an image from the printer camera.

    If there's an active camera stream, uses the buffered frame instead of
    creating a new connection (which would fail while stream is active).

    Returns:
        Tuple of (image_data, camera_source) or (None, error_message)
    """
    image_data: bytes | None = None
    camera_source = "unknown"

    # Try external camera first if requested and available
    if use_external and external_camera_url and external_camera_type:
        try:
            from backend.app.services.external_camera import capture_frame

            image_data = await capture_frame(external_camera_url, external_camera_type)
            if image_data:
                camera_source = "external"
                logger.debug(f"Captured frame from external camera for printer {printer_id}")
        except Exception as e:
            logger.warning(f"Failed to capture from external camera: {e}")

    # Fall back to built-in camera
    if image_data is None:
        # First, check if there's an active stream with a buffered frame
        # This avoids blocking when camera viewer is open
        try:
            from backend.app.api.routes.camera import get_buffered_frame

            buffered = get_buffered_frame(printer_id)
            if buffered:
                image_data = buffered
                camera_source = "built-in (buffered)"
                logger.debug(f"Using buffered frame from active stream for printer {printer_id}")
        except Exception as e:
            logger.debug(f"Could not get buffered frame: {e}")

        # If no buffered frame, try to capture a new one
        if image_data is None:
            import tempfile

            from backend.app.services.camera import capture_camera_frame

            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            try:
                success = await capture_camera_frame(ip_address, access_code, model, tmp_path, timeout=10)
                if success:
                    with open(tmp_path, "rb") as f:
                        image_data = f.read()
                    camera_source = "built-in"
                    logger.debug(f"Captured frame from built-in camera for printer {printer_id}")
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    return image_data, camera_source


async def check_plate_empty(
    printer_id: int,
    ip_address: str,
    access_code: str,
    model: str,
    plate_type: str | None = None,
    include_debug_image: bool = False,
    external_camera_url: str | None = None,
    external_camera_type: str | None = None,
    use_external: bool = False,
    roi: tuple[float, float, float, float] | None = None,
) -> PlateDetectionResult:
    """Check if the build plate is empty for a printer.

    Args:
        printer_id: Printer database ID
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model string
        plate_type: Type of build plate for calibration lookup
        include_debug_image: If True, include annotated image in result
        external_camera_url: URL of external camera (if configured)
        external_camera_type: Type of external camera (mjpeg, rtsp, snapshot)
        use_external: If True, prefer external camera over built-in
        roi: Region of interest as (x%, y%, w%, h%) - percentages of image size

    Returns:
        PlateDetectionResult with analysis results
    """
    if not OPENCV_AVAILABLE:
        return PlateDetectionResult(
            is_empty=True,
            confidence=0.0,
            difference_percent=0.0,
            message="OpenCV not available - plate detection disabled",
        )

    image_data, camera_source = await capture_camera_image(
        printer_id, ip_address, access_code, model, external_camera_url, external_camera_type, use_external
    )

    if image_data is None:
        return PlateDetectionResult(
            is_empty=True,  # Default to empty on error
            confidence=0.0,
            difference_percent=0.0,
            message="Failed to capture camera frame from any source",
        )

    # Analyze the captured frame
    detector = PlateDetector(roi=roi)
    result = detector.analyze_frame(image_data, printer_id, plate_type, include_debug_image)

    # Add camera source to message
    result.message = f"[{camera_source}] {result.message}"

    return result


async def calibrate_plate(
    printer_id: int,
    ip_address: str,
    access_code: str,
    model: str,
    label: str | None = None,
    external_camera_url: str | None = None,
    external_camera_type: str | None = None,
    use_external: bool = False,
) -> tuple[bool, str, int]:
    """Calibrate plate detection by capturing a reference image of the empty plate.

    Args:
        printer_id: Printer database ID
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model string
        label: Optional label for this reference (e.g., "High Temp Plate")
        external_camera_url: URL of external camera (if configured)
        external_camera_type: Type of external camera (mjpeg, rtsp, snapshot)
        use_external: If True, prefer external camera over built-in

    Returns:
        Tuple of (success, message, index)
    """
    if not OPENCV_AVAILABLE:
        return False, "OpenCV not available - plate detection disabled", -1

    image_data, camera_source = await capture_camera_image(
        printer_id, ip_address, access_code, model, external_camera_url, external_camera_type, use_external
    )

    if image_data is None:
        return False, "Failed to capture camera frame for calibration", -1

    detector = PlateDetector()
    success, message, index = detector.calibrate(image_data, printer_id, label)

    if success:
        message = f"[{camera_source}] {message}"

    return success, message, index


def get_calibration_status(printer_id: int, plate_type: str | None = None) -> dict:
    """Get calibration status for a printer.

    Returns:
        Dict with calibration info including reference count
    """
    if not OPENCV_AVAILABLE:
        return {
            "available": False,
            "calibrated": False,
            "reference_count": 0,
            "max_references": 5,
            "message": "OpenCV not available",
        }

    detector = PlateDetector()
    calibrated = detector.has_calibration(printer_id)
    ref_count = detector.get_calibration_count(printer_id)

    if calibrated:
        message = f"Calibrated with {ref_count}/{detector.MAX_REFERENCES} reference(s)"
    else:
        message = "Not calibrated - please calibrate with empty plate"

    return {
        "available": True,
        "calibrated": calibrated,
        "reference_count": ref_count,
        "max_references": detector.MAX_REFERENCES,
        "message": message,
    }


def delete_calibration(printer_id: int, plate_type: str | None = None) -> bool:
    """Delete calibration for a printer and plate type."""
    if not OPENCV_AVAILABLE:
        return False

    detector = PlateDetector()
    return detector.delete_calibration(printer_id, plate_type)


def is_plate_detection_available() -> bool:
    """Check if plate detection feature is available (OpenCV installed)."""
    return OPENCV_AVAILABLE
