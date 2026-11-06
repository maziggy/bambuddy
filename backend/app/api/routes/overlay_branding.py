"""Server-stored streaming overlay logo. Mutation requires settings permission."""

import io
import os
import tempfile
import warnings

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from backend.app.core.auth import RequireOverlayTokenAnyPrinterIfAuthEnabled, RequirePermissionIfAuthEnabled
from backend.app.core.config import settings
from backend.app.core.permissions import Permission
from backend.app.models.user import User

router = APIRouter(tags=["overlay-branding"])
MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 4_000_000


def _read_logo() -> Response:
    try:
        content = (settings.base_dir / "overlay-branding" / "logo.png").read_bytes()
    except FileNotFoundError:
        raise HTTPException(404, "No overlay logo saved") from None
    return Response(content, media_type="image/png", headers={"Cache-Control": "no-store"})


def _save_logo(content: bytes) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in ("PNG", "WEBP") or image.width * image.height > MAX_PIXELS:
                    raise HTTPException(400, "Use a PNG or WebP image with at most 4 million pixels")
                if getattr(image, "is_animated", False):
                    raise HTTPException(400, "Animated logos are not supported")
                image.load()
                image = image.convert("RGBA")
                image.thumbnail((512, 512))
                output = io.BytesIO()
                image.save(output, format="PNG")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "Invalid PNG or WebP image") from None
    directory = settings.base_dir / "overlay-branding"
    directory.mkdir(parents=True, exist_ok=True)
    # Replace atomically so concurrent readers never see a partially written PNG.
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        temporary_path = temporary.name
        try:
            temporary.write(output.getvalue())
            temporary.close()
            os.replace(temporary_path, directory / "logo.png")
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)


@router.get("/settings/overlay-logo")
def get_logo(_: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ)):
    return _read_logo()


@router.get("/overlay-branding/logo")
def get_stream_logo(_: None = RequireOverlayTokenAnyPrinterIfAuthEnabled):
    return _read_logo()


@router.post("/settings/overlay-logo")
async def upload_logo(
    file: UploadFile = File(...),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "Logo must be 2 MiB or smaller")
    await run_in_threadpool(_save_logo, content)
    return {"status": "ok"}


@router.delete("/settings/overlay-logo")
def delete_logo(_: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE)):
    (settings.base_dir / "overlay-branding" / "logo.png").unlink(missing_ok=True)
    return {"status": "ok"}
