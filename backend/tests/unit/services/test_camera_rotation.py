"""Tests for the shared camera-rotation helpers (#2708).

Every other test of a rotating path patches ``apply_camera_rotation`` out and
asserts the call, which proves the wiring but not the rotation. These drive
the real PIL round trip, so a flipped sign or a dropped ``expand=True`` fails
here rather than shipping.
"""

import io
import logging

import pytest
from PIL import Image

from backend.app.services.camera import apply_camera_rotation, apply_camera_rotation_to_file

logger = logging.getLogger(__name__)


def _jpeg(width: int, height: int, corner: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """A JPEG with one distinctly coloured pixel block in the top-left corner,
    so which way it turned is observable and not just the dimensions."""
    img = Image.new("RGB", (width, height), (0, 0, 255))
    for x in range(min(8, width)):
        for y in range(min(8, height)):
            img.putpixel((x, y), corner)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def _brightest_corner(img: Image.Image) -> str:
    """Which corner holds the red block, sampled a few pixels in to stay clear
    of JPEG ringing at the edges."""
    w, h = img.size
    probes = {
        "top-left": (3, 3),
        "top-right": (w - 4, 3),
        "bottom-left": (3, h - 4),
        "bottom-right": (w - 4, h - 4),
    }
    return max(probes, key=lambda name: img.getpixel(probes[name])[0] - img.getpixel(probes[name])[2])


class TestApplyCameraRotation:
    def test_zero_rotation_returns_the_input_object(self):
        """Not merely equal — identity. apply_camera_rotation_to_file uses this
        to decide there is nothing to write back."""
        src = _jpeg(64, 32)
        assert apply_camera_rotation(src, 0, logger) is src

    def test_90_degrees_turns_clockwise(self):
        """camera_rotation is documented as degrees *clockwise*, and PIL's
        rotate() is counter-clockwise — the helper negates to compensate. A
        lost negation would send the corner to bottom-right instead."""
        src = _jpeg(64, 32)
        assert _brightest_corner(_open(src)) == "top-left"

        out = _open(apply_camera_rotation(src, 90, logger))
        assert out.size == (32, 64)  # expand=True, so the frame is not cropped
        assert _brightest_corner(out) == "top-right"

    def test_270_degrees_turns_the_other_way(self):
        out = _open(apply_camera_rotation(_jpeg(64, 32), 270, logger))
        assert out.size == (32, 64)
        assert _brightest_corner(out) == "bottom-left"

    def test_180_degrees_keeps_the_dimensions_and_flips_the_corner(self):
        out = _open(apply_camera_rotation(_jpeg(64, 32), 180, logger))
        assert out.size == (64, 32)
        assert _brightest_corner(out) == "bottom-right"

    def test_applying_180_twice_is_the_bug_that_was_fixed(self):
        """The regression this guards: two rotations cancel out and the photo
        is upside-down again. Kept as a test so the invariant that
        _stage22_finish_frames holds exactly one rotation has a stated reason.
        """
        src = _jpeg(64, 32)
        once = apply_camera_rotation(src, 180, logger)
        twice = apply_camera_rotation(once, 180, logger)
        assert _brightest_corner(_open(once)) == "bottom-right"
        assert _brightest_corner(_open(twice)) == "top-left"  # back to the original

    def test_undecodable_bytes_return_unchanged(self):
        """A capture path must not lose a frame because the rotate failed —
        an unrotated photo beats no photo."""
        junk = b"not a jpeg at all"
        assert apply_camera_rotation(junk, 90, logger) is junk

    def test_a_failed_rotate_is_logged_as_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger=__name__):
            apply_camera_rotation(b"not a jpeg at all", 90, logger)
        assert any("Failed to apply camera rotation" in r.message for r in caplog.records)

    def test_a_successful_rotate_does_not_log_at_info(self, caplog):
        """Layer-timelapse calls this once per layer; at INFO a tall print
        would bury the log."""
        with caplog.at_level(logging.INFO, logger=__name__):
            apply_camera_rotation(_jpeg(64, 32), 90, logger)
        assert caplog.records == []


class TestApplyCameraRotationToFile:
    """The two finish-photo sources that let ffmpeg write the file and never
    hold the bytes: capture_finish_photo and the timelapse last-frame extract."""

    @pytest.mark.asyncio
    async def test_rotates_in_place(self, tmp_path):
        path = tmp_path / "finish.jpg"
        path.write_bytes(_jpeg(64, 32))

        await apply_camera_rotation_to_file(path, 90, logger)

        out = _open(path.read_bytes())
        assert out.size == (32, 64)
        assert _brightest_corner(out) == "top-right"

    @pytest.mark.asyncio
    async def test_zero_rotation_leaves_the_file_untouched(self, tmp_path):
        path = tmp_path / "finish.jpg"
        original = _jpeg(64, 32)
        path.write_bytes(original)

        await apply_camera_rotation_to_file(path, 0, logger)

        assert path.read_bytes() == original

    @pytest.mark.asyncio
    async def test_a_file_that_cannot_be_rotated_is_left_intact(self, tmp_path):
        """Not truncated, not deleted — the caller's unrotated photo survives."""
        path = tmp_path / "finish.jpg"
        path.write_bytes(b"not a jpeg at all")

        await apply_camera_rotation_to_file(path, 90, logger)

        assert path.read_bytes() == b"not a jpeg at all"

    @pytest.mark.asyncio
    async def test_a_missing_file_does_not_raise(self, tmp_path):
        """Best-effort: this runs after the capture reported success, and must
        not turn a delivered photo into a failed one."""
        await apply_camera_rotation_to_file(tmp_path / "gone.jpg", 90, logger)
