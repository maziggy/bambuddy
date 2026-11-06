"""Prepare oversized camera snapshots for the Gadget API's image limit."""

from io import BytesIO

from PIL import Image, ImageOps

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
MAX_IMAGE_DIMENSION = 2048
INVALID_IMAGE_MESSAGE = "Could not prepare the camera snapshot for OctoEverywhere."


def prepare_frame(frame: bytes) -> bytes:
    """Keep normal frames unchanged and shrink oversized images to a JPEG."""
    if len(frame) <= MAX_IMAGE_BYTES:
        return frame

    try:
        with Image.open(BytesIO(frame)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError(INVALID_IMAGE_MESSAGE)
            # JPEG decoders can reduce resolution before allocating all pixels.
            # Bound the working image before copying or converting its colors.
            source.draft("RGB", (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
            source.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
            with ImageOps.exif_transpose(source) as oriented, oriented.convert("RGB") as image:
                for quality in (90, 80, 70):
                    output = BytesIO()
                    image.save(output, format="JPEG", quality=quality)
                    prepared = output.getvalue()
                    if len(prepared) <= MAX_IMAGE_BYTES:
                        return prepared
    except Exception:
        raise ValueError(INVALID_IMAGE_MESSAGE) from None

    raise ValueError(INVALID_IMAGE_MESSAGE)
