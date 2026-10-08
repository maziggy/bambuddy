"""Oversized Gadget snapshots are reduced without changing ordinary frames."""

from io import BytesIO

import pytest
from PIL import Image

from backend.app.services.octoeverywhere_images import INVALID_IMAGE_MESSAGE, MAX_IMAGE_BYTES, prepare_frame


@pytest.fixture(scope="module")
def oversized_jpeg():
    with Image.effect_noise((3072, 2304), 100).convert("RGB") as image:
        output = BytesIO()
        exif = Image.Exif()
        exif[274] = 6  # The camera image should be displayed rotated clockwise.
        image.save(output, format="JPEG", quality=100, subsampling=0, exif=exif)
        frame = output.getvalue()
    assert len(frame) > MAX_IMAGE_BYTES
    return frame


@pytest.mark.parametrize("size", [100, MAX_IMAGE_BYTES])
def test_normal_frames_pass_through_without_decoding(size):
    # Deliberately not a valid JPEG: the size fast path must not decode it.
    frame = b"x" * size
    assert prepare_frame(frame) is frame


def test_oversized_jpeg_is_valid_bounded_and_preserves_orientation(oversized_jpeg):
    prepared = prepare_frame(oversized_jpeg)
    assert 0 < len(prepared) <= MAX_IMAGE_BYTES
    with Image.open(BytesIO(prepared)) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.width < image.height
        assert max(image.size) <= 2048
        assert image.getexif().get(274, 1) == 1


def test_invalid_oversized_frame_returns_safe_error():
    frame = b"private-camera-data" * (MAX_IMAGE_BYTES // len(b"private-camera-data") + 1)
    with pytest.raises(ValueError, match=f"^{INVALID_IMAGE_MESSAGE}$") as error:
        prepare_frame(frame)
    assert "private-camera-data" not in str(error.value)
    assert error.value.__cause__ is None
