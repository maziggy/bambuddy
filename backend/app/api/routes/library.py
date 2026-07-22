"""API routes for File Manager (Library) functionality."""

import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse as FastAPIFileResponse
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.cloud import resolve_api_key_cloud_owner
from backend.app.core.auth import (
    RequireCameraStreamTokenIfAuthEnabled,
    require_ownership_permission,
    require_permission_if_auth_enabled,
)
from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session, get_db
from backend.app.core.permissions import Permission
from backend.app.core.tasks import spawn_background_task
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile, LibraryFileTag, LibraryFolder
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project
from backend.app.models.user import User
from backend.app.schemas.library import (
    AddToQueueError,
    AddToQueueRequest,
    AddToQueueResponse,
    AddToQueueResult,
    BatchThumbnailRequest,
    BatchThumbnailResponse,
    BatchThumbnailResult,
    BulkDeleteRequest,
    BulkDeleteResponse,
    ExternalFolderCreate,
    FileDuplicate,
    FileListResponse,
    FileMoveRequest,
    FileResponse as FileResponseSchema,
    FileUpdate,
    FileUploadResponse,
    FolderCreate,
    FolderReadmeResponse,
    FolderResponse,
    FolderTreeItem,
    FolderUpdate,
    TagSummary,
    ZipExtractError,
    ZipExtractResponse,
    ZipExtractResult,
)
from backend.app.schemas.slicer import SliceRequest, SliceResponse
from backend.app.services.archive import ThreeMFParser
from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing
from backend.app.services.stl_thumbnail import MIN_USABLE_STL_BYTES, generate_stl_thumbnail
from backend.app.utils.filename import InvalidFilenameError, validate_print_filename
from backend.app.utils.threemf_tools import (
    extract_embedded_presets_from_3mf,
    extract_nozzle_mapping_from_3mf,
    extract_project_filaments_from_3mf,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])


def _ensure_library_file_visible(
    library_file: LibraryFile | None,
    user: User | None,
    can_read_all: bool,
) -> LibraryFile:
    """Per-file visibility gate for ownership-scoped LIBRARY reads (#1726-adjacent).

    Mirrors archives.py::_ensure_archive_visible — single enforcement point so a
    less-guarded sibling route can't accidentally leak a row. Same shape:

      - Missing / soft-deleted → 404 (not 403, to avoid id-enumeration leaks).
      - ``can_read_all`` true (LIBRARY_READ_ALL or auth disabled) → file returned.
      - ``can_read_all`` false and ``created_by_id != user.id`` → 404.
      - Ownerless files (``created_by_id is None``) require ALL — fail-closed.
    """
    if library_file is None or getattr(library_file, "deleted_at", None) is not None:
        raise HTTPException(404, "File not found")
    if can_read_all:
        return library_file
    if user is None:
        raise HTTPException(404, "File not found")
    if library_file.created_by_id is None or library_file.created_by_id != user.id:
        raise HTTPException(404, "File not found")
    return library_file


def get_library_dir() -> Path:
    """Get the library storage directory."""
    base_dir = Path(app_settings.archive_dir)
    library_dir = base_dir / "library"
    library_dir.mkdir(parents=True, exist_ok=True)
    return library_dir


def get_library_files_dir() -> Path:
    """Get the directory for library files."""
    files_dir = get_library_dir() / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def classify_file_type(filename: str) -> str:
    """Return the canonical ``LibraryFile.file_type`` for *filename*.

    Compound extensions are preserved — a `.gcode.3mf` file (a sliced
    output, still a 3MF zip on disk) is classified ``gcode.3mf`` rather
    than ``3mf``. Pre-#1600 this was only done in the external-scan
    path; the upload / ZIP-extract / in-process paths all stripped to
    the trailing extension and stored ``3mf``, so the FE had to accept
    both. Unified here so every ingest path stores the same value and
    downstream gates (gcode download, file-type filter, thumbnail
    extraction) only need to handle one canonical name per file family.
    Files with no extension classify as ``unknown``.
    """
    lower = filename.lower()
    if lower.endswith(".gcode.3mf"):
        return "gcode.3mf"
    ext = os.path.splitext(lower)[1]
    return ext[1:] if ext else "unknown"


def get_library_thumbnails_dir() -> Path:
    """Get the directory for library thumbnails."""
    thumbnails_dir = get_library_dir() / "thumbnails"
    thumbnails_dir.mkdir(parents=True, exist_ok=True)
    return thumbnails_dir


def to_relative_path(absolute_path: Path | str) -> str:
    """Convert an absolute path to a path relative to base_dir for storage."""
    if not absolute_path:
        return ""
    abs_path = Path(absolute_path)
    base_dir = Path(app_settings.base_dir)
    try:
        return str(abs_path.relative_to(base_dir))
    except ValueError:
        # Path is not under base_dir, return as-is (shouldn't happen normally)
        return str(abs_path)


def to_absolute_path(relative_path: str | None) -> Path | None:
    """Convert a relative path (from database) to an absolute path for file operations."""
    if not relative_path:
        return None
    path = Path(relative_path)
    # Handle already-absolute paths verbatim (backwards compatibility during migration).
    # Legacy DB rows may store absolute paths that predate the base_dir layout; the
    # traversal guard below only applies to relative paths coming from user input.
    if path.is_absolute():
        return path.resolve()
    base = Path(app_settings.base_dir).resolve()
    resolved = (base / relative_path).resolve()
    # Guard against path traversal — resolved path must stay inside base_dir.
    # Use is_relative_to() to avoid the /data/app vs /data/app_evil prefix confusion
    # that a plain startswith(str(base)) check would miss.
    if not resolved.is_relative_to(base):
        raise ValueError(f"Path escapes base directory: {relative_path!r}")
    return resolved


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def validate_print_file_upload(filename: str, content: bytes) -> None:
    """Reject obviously-unprintable uploads early so the printer doesn't see them (#1401).

    Bambu printers in network mode only parse ``.gcode.3mf`` zip containers
    — raw ``.gcode`` and corrupt/non-zip ``.3mf`` uploads cascade into a
    confusing "Printing stopped because the printer was unable to parse the
    3mf file" rejection 30 seconds after the user clicks Print. The
    the queue dispatch path appends ``.3mf`` to a raw-gcode filename when
    constructing the FTP destination, which is how the printer ends up with a
    file named ``.gcode.3mf`` whose body is raw gcode — exactly the shape that
    triggers the firmware parse failure. Catching both classes here gives an
    actionable error at the
    upload itself.

    Compares the filename suffix rather than ``os.path.splitext`` because
    compound extensions like ``.gcode.3mf`` show up as just ``.3mf`` after
    ``splitext`` — same content validation needs to fire for both
    single-``.3mf`` and ``.gcode.3mf`` uploads.

    Raises ``HTTPException(400, ...)`` with a human-readable message on
    rejection; returns ``None`` for valid (or irrelevant — e.g. STL,
    image) uploads.
    """
    lower_filename = filename.lower()
    is_3mf_upload = lower_filename.endswith(".3mf")
    is_raw_gcode_upload = lower_filename.endswith(".gcode") and not lower_filename.endswith(".gcode.3mf")

    if is_raw_gcode_upload:
        raise HTTPException(
            status_code=400,
            detail=(
                "Raw .gcode files can't be printed on Bambu printers in network mode — "
                "they need a .gcode.3mf zip container (gcode plus metadata). Re-export from "
                "your slicer and make sure the file ends in '.gcode.3mf', not just '.gcode'. "
                "If your OS hides extensions, double-check the file with the extension visible."
            ),
        )

    if is_3mf_upload and not content.startswith(b"PK\x03\x04"):
        raise HTTPException(
            status_code=400,
            detail=(
                "This .3mf file isn't a valid ZIP container. 3MF files are ZIP archives — "
                "either the file is corrupted or it's raw gcode renamed to .3mf. Re-export "
                "from your slicer using its 'Export Plate Sliced File' action."
            ),
        )


def _resolve_upload_destination(target_folder: LibraryFolder | None, filename: str) -> tuple[Path, bool]:
    """Resolve the on-disk destination for an uploaded file.

    Non-external target: returns ``(<library_files_dir>/<uuid><ext>, False)``.
    Writable external target: writes to ``<external_path>/<filename>``
    (preserves the real filename so the file is recognisable on the mount);
    returns ``(dest, True)``. Raises ``HTTPException`` for read-only external
    folders (403), missing/inaccessible/non-writable external paths (400), and
    filename collisions on the external mount (409). See #1112 — previously
    uploads to writable external folders were silently misrouted to the
    internal library dir.
    """
    if target_folder is not None and target_folder.is_external:
        if target_folder.external_readonly:
            raise HTTPException(status_code=403, detail="Cannot upload to a read-only external folder")
        if not target_folder.external_path:
            raise HTTPException(status_code=400, detail="External folder has no configured path")
        ext_dir = Path(target_folder.external_path)
        if not ext_dir.exists() or not ext_dir.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"External path is not accessible: {target_folder.external_path}",
            )
        if not os.access(ext_dir, os.W_OK):
            raise HTTPException(
                status_code=400,
                detail=f"External path is not writable: {target_folder.external_path}",
            )
        # Guard against path-traversal via a pathological filename — join then
        # verify the resolved destination is still inside the external dir.
        dest = (ext_dir / filename).resolve()  # SEC-PATH-OK: resolve + relative_to containment check on next line
        try:
            dest.relative_to(ext_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid filename")
        if dest.exists():
            raise HTTPException(
                status_code=409,
                detail=f"A file named {filename!r} already exists in the external folder",
            )
        return dest, True
    ext = os.path.splitext(filename)[1].lower()
    return get_library_files_dir() / f"{uuid.uuid4().hex}{ext}", False


def _stored_file_path(abs_path: Path, is_external: bool) -> str:
    """Produce the value to persist in ``LibraryFile.file_path``.

    External files store the absolute mount path directly (same as scan does),
    so ``to_absolute_path`` round-trips through its ``is_absolute()`` fast
    path. Managed files store a path relative to ``base_dir`` for portability.
    """
    return str(abs_path) if is_external else to_relative_path(abs_path)


class _MoveSkip(Exception):
    """Signalled by ``_move_file_bytes`` to skip a file with a user-visible reason.

    Carries an optional `code` for machine-friendly grouping (the
    front-end can localise it) and a fallback English `reason` for logs.
    """

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _resolve_source_disk_path(file: LibraryFile) -> Path | None:
    """Return the absolute on-disk path for an existing LibraryFile, or None
    if it can't be located (legacy DB row, deleted file, etc.)."""
    if file.is_external:
        return Path(file.file_path) if file.file_path else None
    return to_absolute_path(file.file_path)


def _move_file_bytes(file: LibraryFile, target_folder: LibraryFolder | None) -> str:
    """Physically relocate `file`'s bytes to match `target_folder`.

    Used by the move endpoint when source/target straddle the
    managed↔external boundary (#1112 follow-up — the prior implementation
    updated the DB row's ``folder_id`` but never moved the bytes, so a
    file moved to an external SMB folder showed up in Bambuddy's UI but
    not on the NAS).

    Returns the new ``file_path`` value to persist (relative for managed
    targets, absolute for external targets — matches the upload + scan
    paths). Raises ``_MoveSkip`` for any condition that would make the
    move unsafe (target unwritable, filename collision, source missing).

    The copy-then-unlink ordering means a partial copy followed by a
    failed unlink leaves both the source and the dest on disk — better
    than the symmetric "rename or move" which would lose the source if
    the target write didn't complete on a flaky mount. The DB row stays
    pointed at the source until the caller commits the new ``file_path``.
    """
    src = _resolve_source_disk_path(file)
    if not src or not src.exists():
        raise _MoveSkip("source_missing", "source file missing on disk")

    target_is_external = target_folder is not None and target_folder.is_external

    if target_is_external:
        if target_folder.external_readonly:
            # Already blocked at top level, but defence-in-depth.
            raise _MoveSkip("target_readonly", "target external folder is read-only")
        if not target_folder.external_path:
            raise _MoveSkip("target_misconfigured", "target external folder has no path")
        ext_dir = Path(target_folder.external_path)
        if not ext_dir.exists() or not ext_dir.is_dir():
            raise _MoveSkip("target_inaccessible", f"target path not accessible: {ext_dir}")
        if not os.access(ext_dir, os.W_OK):
            raise _MoveSkip("target_unwritable", f"target path not writable: {ext_dir}")
        dest = (ext_dir / file.filename).resolve()  # SEC-PATH-OK: resolve + relative_to containment check on next line
        try:
            dest.relative_to(ext_dir.resolve())
        except ValueError:
            raise _MoveSkip("invalid_filename", f"unsafe filename: {file.filename!r}") from None
        if dest.exists():
            raise _MoveSkip("name_collision", f"a file named {file.filename!r} already exists in target")
        try:
            shutil.copy2(src, dest)
        except OSError as e:
            # Clean up partial dest so a retry can succeed.
            with contextlib.suppress(OSError):
                dest.unlink(missing_ok=True)
            raise _MoveSkip("copy_failed", f"copy failed: {e}") from e
    else:
        # → managed (root or non-external folder): generate a fresh UUID
        # filename in the internal store so we don't collide with another
        # file that happens to share `filename`.
        ext = src.suffix.lower()
        dest = get_library_files_dir() / f"{uuid.uuid4().hex}{ext}"
        try:
            shutil.copy2(src, dest)
        except OSError as e:
            with contextlib.suppress(OSError):
                dest.unlink(missing_ok=True)
            raise _MoveSkip("copy_failed", f"copy failed: {e}") from e

    # Copy succeeded — unlink the original. A failure here leaves an
    # orphan on disk but the DB row is consistent against the new dest.
    try:
        src.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(
            "Move: copied %s → %s but couldn't remove source: %s",
            src,
            dest,
            e,
        )

    return _stored_file_path(dest, is_external=target_is_external)


def _clean_3mf_metadata(obj):
    """Strip bytes and thumbnail-carrier keys so the payload is JSON-storable.

    Shared by ``upload_file`` and :func:`save_3mf_bytes_to_library` — the
    ``ThreeMFParser`` output embeds the thumbnail bytes under
    ``_thumbnail_data``/``_thumbnail_ext`` and may also include raw bytes in
    other fields, none of which can be JSON-encoded.
    """
    if isinstance(obj, dict):
        return {
            k: _clean_3mf_metadata(v)
            for k, v in obj.items()
            if not isinstance(v, bytes) and k not in ("_thumbnail_data", "_thumbnail_ext")
        }
    if isinstance(obj, list):
        return [_clean_3mf_metadata(i) for i in obj if not isinstance(i, bytes)]
    if isinstance(obj, bytes):
        return None
    return obj


def _read_3mf_entry(zip_path: Path, entry: str) -> bytes | None:
    """Return the raw bytes of an entry inside a 3MF (ZIP), or ``None`` when
    the file isn't a parseable zip / doesn't contain that entry / any IO
    error. Used to lift the source archive's per-plate render onto a
    re-sliced archive (#1493 follow-up) — the slicer CLI often doesn't
    emit a fresh ``Metadata/plate_N.png`` and the project-wide cover-art
    fallback in :class:`ThreeMFParser` looks unrelated to the actual slice.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if entry not in zf.namelist():
                return None
            return zf.read(entry)
    except (zipfile.BadZipFile, OSError, KeyError):
        return None


def _without_print_name(metadata: dict | None) -> dict | None:
    """Drop the embedded 3MF Title (``print_name``) from library-file metadata.

    The 3MF ``<metadata name="Title">`` holds the in-app project title — the
    generic ``"Exported 3D Model"`` for a Bambu Studio "Save As", a marketing
    title for a MakerWorld download — never the filename the user saved as.
    The FileManager keys its display name, search and sort off ``print_name``,
    so storing it makes every card show the wrong name (#1489). A library
    file's display name is its filename; only ``PrintArchive`` carries a real
    ``print_name``. Returns the input unchanged when there's nothing to strip;
    otherwise a new dict (never mutates the argument).
    """
    if not metadata or "print_name" not in metadata:
        return metadata
    return {k: v for k, v in metadata.items() if k != "print_name"}


async def save_3mf_bytes_to_library(
    db: AsyncSession,
    *,
    file_bytes: bytes,
    filename: str,
    folder_id: int | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    owner_id: int | None = None,
) -> tuple[LibraryFile, bool]:
    """Save a 3MF blob into the library and return ``(library_file, was_existing)``.

    Used by routes that receive a 3MF in-process rather than as a multipart
    upload (currently: MakerWorld import; reusable for any future source that
    fetches bytes server-side). Deduplicates by ``source_url`` when provided —
    if a LibraryFile with the same source_url already exists, the existing
    row is returned and the bytes are NOT re-saved (MakerWorld signed URLs
    change each download, so hash-based dedupe alone would miss re-imports).

    Parses 3MF metadata + thumbnail the same way the multipart upload route
    does, via :class:`ThreeMFParser`. Paths are stored as relative so the
    library is portable across installs.
    """
    # Source-URL-based dedupe: return the existing row untouched.
    if source_url:
        existing = await db.execute(LibraryFile.active().where(LibraryFile.source_url == source_url).limit(1))
        existing_row = existing.scalar_one_or_none()
        if existing_row is not None:
            return existing_row, True

    # Resolve target folder so writable-external destinations land on the
    # mount with the real filename, instead of being silently misrouted to
    # the internal library dir with a UUID name (#1645). Mirrors what the
    # multipart-upload path has done since #1112. ``_resolve_upload_destination``
    # also enforces the 403 read-only / 400 unwritable / 409 collision
    # rejections — the makerworld route layer already pre-checks read-only,
    # but the helper's checks remain as defence-in-depth for any future
    # caller that skips that route gate.
    target_folder: LibraryFolder | None = None
    if folder_id is not None:
        folder_q = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
        target_folder = folder_q.scalar_one_or_none()

    file_path, is_external = _resolve_upload_destination(target_folder, filename)
    ext = file_path.suffix.lower() or ".3mf"
    with open(file_path, "wb") as fh:
        fh.write(file_bytes)

    file_hash = calculate_file_hash(file_path)

    # Extract metadata + thumbnail from the 3MF.
    metadata: dict | None = None
    thumbnail_path: str | None = None
    if ext == ".3mf":
        try:
            parser = ThreeMFParser(str(file_path))
            raw_metadata = parser.parse()
            thumb_data = raw_metadata.get("_thumbnail_data")
            thumb_ext = raw_metadata.get("_thumbnail_ext", ".png")
            if thumb_data:
                thumbs_dir = get_library_thumbnails_dir()
                thumb_filename = f"{uuid.uuid4().hex}{thumb_ext}"
                thumb_path = thumbs_dir / thumb_filename  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + thumb_ext
                with open(thumb_path, "wb") as fh:
                    fh.write(thumb_data)
                thumbnail_path = str(thumb_path)
            metadata = _clean_3mf_metadata(raw_metadata) or None
        except Exception as exc:
            # Matches the multipart upload route's behaviour — a bad 3MF should
            # still land in the library so the user can see / delete it rather
            # than failing the whole request.
            logger.warning("Failed to parse 3MF %s: %s", filename, exc)

    library_file = LibraryFile(
        folder_id=folder_id,
        is_external=is_external,
        filename=filename,
        file_path=_stored_file_path(file_path, is_external),
        file_type=classify_file_type(filename),
        file_size=len(file_bytes),
        file_hash=file_hash,
        thumbnail_path=to_relative_path(thumbnail_path) if thumbnail_path else None,
        file_metadata=_without_print_name(metadata),
        source_type=source_type,
        source_url=source_url,
        created_by_id=owner_id,
    )
    db.add(library_file)
    await db.commit()
    await db.refresh(library_file)
    return library_file, False


def extract_gcode_thumbnail(file_path: Path) -> bytes | None:
    """Extract embedded thumbnail from gcode file.

    Supports PrusaSlicer/BambuStudio format:
    ; thumbnail begin WxH SIZE
    ; base64data...
    ; thumbnail end
    """
    try:
        thumbnail_data = None
        in_thumbnail = False
        thumbnail_lines = []
        best_size = 0

        with open(file_path, errors="ignore") as f:
            # Only read first 50KB for performance (thumbnails are at the start)
            content = f.read(50000)

        for line in content.split("\n"):
            line = line.strip()

            # Check for thumbnail start
            if line.startswith("; thumbnail begin"):
                in_thumbnail = True
                thumbnail_lines = []
                # Parse dimensions: "; thumbnail begin 300x300 12345"
                match = re.search(r"(\d+)x(\d+)", line)
                if match:
                    width = int(match.group(1))
                    # Prefer larger thumbnails (up to 300px)
                    if width > best_size and width <= 300:
                        best_size = width
                continue

            # Check for thumbnail end
            if line.startswith("; thumbnail end"):
                if in_thumbnail and thumbnail_lines:
                    try:
                        # Decode the base64 data
                        b64_data = "".join(thumbnail_lines)
                        decoded = base64.b64decode(b64_data)
                        # Only keep if this is the best size or first valid thumbnail
                        if thumbnail_data is None or best_size > 0:
                            thumbnail_data = decoded
                    except (binascii.Error, ValueError):
                        pass  # Skip thumbnail with invalid base64 data
                in_thumbnail = False
                thumbnail_lines = []
                continue

            # Collect thumbnail data
            if in_thumbnail and line.startswith(";"):
                # Remove the leading "; " or ";"
                data_line = line[1:].strip()
                if data_line:
                    thumbnail_lines.append(data_line)

        return thumbnail_data
    except Exception as e:
        logger.warning("Failed to extract gcode thumbnail: %s", e)
        return None


def create_image_thumbnail(file_path: Path, thumbnails_dir: Path, max_size: int = 256) -> str | None:
    """Create a thumbnail from an image file.

    For small images, copies directly. For larger images, resizes.
    Returns the thumbnail path or None on failure.
    """
    try:
        from PIL import Image

        thumb_filename = f"{uuid.uuid4().hex}.png"
        thumb_path = thumbnails_dir / thumb_filename  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + ".png"

        with Image.open(file_path) as img:
            # Convert to RGB if necessary (for PNG with transparency, etc.)
            if img.mode in ("RGBA", "LA", "P"):
                # Create white background for transparency
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Resize if larger than max_size
            if img.width > max_size or img.height > max_size:
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            img.save(thumb_path, "PNG", optimize=True)

        return str(thumb_path)
    except ImportError:
        # PIL not installed, just copy the file if it's small enough
        logger.warning("PIL not installed, copying image as thumbnail")
        try:
            file_size = file_path.stat().st_size
            if file_size < 500000:  # Less than 500KB
                thumb_filename = f"{uuid.uuid4().hex}{file_path.suffix}"
                thumb_path = (
                    thumbnails_dir / thumb_filename
                )  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + file_path.suffix
                shutil.copy2(file_path, thumb_path)
                return str(thumb_path)
        except OSError:
            pass  # File inaccessible; fall through to return None
        return None
    except Exception as e:
        logger.warning("Failed to create image thumbnail: %s", e)
        return None


# Supported image extensions for thumbnails
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}


async def _backfill_external_stl_thumbnails(folder_ids: list[int]) -> None:
    """Generate STL thumbnails for an external folder tree in the background.

    Spawned via ``asyncio.create_task`` from ``scan_external_folder`` so the
    HTTP request can return as soon as the filesystem walk + folder/file rows
    are committed. Thumbnails for thousands of STL files would otherwise hold
    the request open for many minutes (each file triggers a ``trimesh.load``
    + matplotlib render, ~1-5s each) and the FE modal times out before the
    final ``db.commit()`` runs — causing the original symptom in #1299 where
    subdirectories never showed up because nothing got committed.

    Opens its own session because the request session is closed by the time
    this task starts running. Commits per-file so a worker restart mid-run
    only loses the in-flight file. Caps STL load to a single file at a time
    to avoid memory pressure on systems with many huge STLs.
    """
    if not folder_ids:
        return
    thumbnails_dir = get_library_thumbnails_dir()
    async with async_session() as db:
        result = await db.execute(
            LibraryFile.active().where(
                LibraryFile.folder_id.in_(folder_ids),
                LibraryFile.file_type == "stl",
                LibraryFile.thumbnail_path.is_(None),
            )
        )
        stl_files = result.scalars().all()
        if not stl_files:
            return
        logger.info(
            "Backfilling STL thumbnails: %d file(s) across %d folder(s)",
            len(stl_files),
            len(folder_ids),
        )
        for stl_file in stl_files:
            abs_path = to_absolute_path(stl_file.file_path)
            if not abs_path or not abs_path.exists():
                continue
            # Pre-skip files too small to contain even a single triangle.
            # Bulk-uploaded ZIPs of stub STLs would otherwise trigger one
            # trimesh.load() call + one debug log line per stub.
            try:
                if abs_path.stat().st_size < MIN_USABLE_STL_BYTES:
                    continue
            except OSError:
                continue
            try:
                thumb_path = generate_stl_thumbnail(abs_path, thumbnails_dir)
            except Exception as exc:  # noqa: BLE001 — never let one bad STL kill the rest
                logger.debug("STL thumbnail backfill skipped %s: %s", abs_path, exc)
                continue
            if thumb_path:
                stl_file.thumbnail_path = to_relative_path(Path(thumb_path))
                await db.commit()


# ============ Folder Endpoints ============


@router.get("/folders", response_model=list[FolderTreeItem])
@router.get("/folders/", response_model=list[FolderTreeItem])
async def list_folders(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get all folders as a tree structure."""
    # Prevent browser caching of folder list
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    # Get all folders with project and archive joins
    result = await db.execute(
        select(LibraryFolder, Project.name, PrintArchive.print_name)
        .outerjoin(Project, LibraryFolder.project_id == Project.id)
        .outerjoin(PrintArchive, LibraryFolder.archive_id == PrintArchive.id)
        .order_by(LibraryFolder.name)
    )
    rows = result.all()

    # Get file counts per folder
    file_counts_result = await db.execute(
        select(LibraryFile.folder_id, func.count(LibraryFile.id))
        .where(LibraryFile.folder_id.isnot(None), LibraryFile.deleted_at.is_(None))
        .group_by(LibraryFile.folder_id)
    )
    file_counts = dict(file_counts_result.all())

    # Latest immediate-child file activity per folder (#1770). Sibling of the
    # file_counts subquery — same WHERE clause, MAX(updated_at) instead of
    # COUNT(id). Subfolder descent is not aggregated here; the frontend's
    # "sort by recent activity" mode is satisfied by immediate-parent bubble.
    latest_file_activity_result = await db.execute(
        select(LibraryFile.folder_id, func.max(LibraryFile.updated_at))
        .where(LibraryFile.folder_id.isnot(None), LibraryFile.deleted_at.is_(None))
        .group_by(LibraryFile.folder_id)
    )
    latest_file_activity = dict(latest_file_activity_result.all())

    # Build tree structure
    folder_map = {}
    root_folders = []

    for folder, project_name, archive_name in rows:
        latest_file = latest_file_activity.get(folder.id)
        latest_activity_at = max(folder.updated_at, latest_file) if latest_file is not None else folder.updated_at
        folder_item = FolderTreeItem(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            project_id=folder.project_id,
            archive_id=folder.archive_id,
            project_name=project_name,
            archive_name=archive_name,
            is_external=folder.is_external,
            external_path=folder.external_path,
            external_readonly=folder.external_readonly,
            file_count=file_counts.get(folder.id, 0),
            latest_activity_at=latest_activity_at,
            children=[],
        )
        folder_map[folder.id] = folder_item

    # Link children to parents
    for folder, _, _ in rows:
        folder_item = folder_map[folder.id]
        if folder.parent_id is None:
            root_folders.append(folder_item)
        elif folder.parent_id in folder_map:
            folder_map[folder.parent_id].children.append(folder_item)

    return root_folders


@router.get("/folders/by-project/{project_id}", response_model=list[FolderResponse])
async def get_folders_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get all folders linked to a specific project."""
    result = await db.execute(
        select(LibraryFolder, Project.name)
        .outerjoin(Project, LibraryFolder.project_id == Project.id)
        .where(LibraryFolder.project_id == project_id)
        .order_by(LibraryFolder.name)
    )
    rows = result.all()

    folders = []
    for folder, project_name in rows:
        # Get file count + latest file activity (#1770) in one trip
        agg_result = await db.execute(
            select(
                func.count(LibraryFile.id),
                func.max(LibraryFile.updated_at),
            ).where(
                LibraryFile.folder_id == folder.id,
                LibraryFile.deleted_at.is_(None),
            )
        )
        file_count, latest_file = agg_result.one()
        file_count = file_count or 0
        latest_activity_at = max(folder.updated_at, latest_file) if latest_file is not None else folder.updated_at

        folders.append(
            FolderResponse(
                id=folder.id,
                name=folder.name,
                parent_id=folder.parent_id,
                project_id=folder.project_id,
                archive_id=folder.archive_id,
                project_name=project_name,
                archive_name=None,
                is_external=folder.is_external,
                external_path=folder.external_path,
                external_readonly=folder.external_readonly,
                external_show_hidden=folder.external_show_hidden,
                file_count=file_count,
                latest_activity_at=latest_activity_at,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        )

    return folders


@router.get("/folders/by-archive/{archive_id}", response_model=list[FolderResponse])
async def get_folders_by_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    _: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get all folders linked to a specific archive."""
    result = await db.execute(
        select(LibraryFolder, PrintArchive.print_name)
        .outerjoin(PrintArchive, LibraryFolder.archive_id == PrintArchive.id)
        .where(LibraryFolder.archive_id == archive_id)
        .order_by(LibraryFolder.name)
    )
    rows = result.all()

    folders = []
    for folder, archive_name in rows:
        # Get file count + latest file activity (#1770) in one trip
        agg_result = await db.execute(
            select(
                func.count(LibraryFile.id),
                func.max(LibraryFile.updated_at),
            ).where(
                LibraryFile.folder_id == folder.id,
                LibraryFile.deleted_at.is_(None),
            )
        )
        file_count, latest_file = agg_result.one()
        file_count = file_count or 0
        latest_activity_at = max(folder.updated_at, latest_file) if latest_file is not None else folder.updated_at

        folders.append(
            FolderResponse(
                id=folder.id,
                name=folder.name,
                parent_id=folder.parent_id,
                project_id=folder.project_id,
                archive_id=folder.archive_id,
                project_name=None,
                archive_name=archive_name,
                is_external=folder.is_external,
                external_path=folder.external_path,
                external_readonly=folder.external_readonly,
                external_show_hidden=folder.external_show_hidden,
                file_count=file_count,
                latest_activity_at=latest_activity_at,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        )

    return folders


@router.post("/folders", response_model=FolderResponse)
@router.post("/folders/", response_model=FolderResponse)
async def create_folder(
    data: FolderCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
):
    """Create a new folder."""
    # Verify parent exists if specified
    if data.parent_id is not None:
        parent_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == data.parent_id))
        if not parent_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Parent folder not found")

    # Verify project exists if specified
    project_name = None
    if data.project_id is not None:
        project_result = await db.execute(select(Project).where(Project.id == data.project_id))
        project = project_result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project_name = project.name

    # Verify archive exists if specified
    archive_name = None
    if data.archive_id is not None:
        archive_result = await db.execute(select(PrintArchive).where(PrintArchive.id == data.archive_id))
        archive = archive_result.scalar_one_or_none()
        if not archive:
            raise HTTPException(status_code=404, detail="Archive not found")
        archive_name = archive.print_name

    folder = LibraryFolder(
        name=data.name,
        parent_id=data.parent_id,
        project_id=data.project_id,
        archive_id=data.archive_id,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        is_external=folder.is_external,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        file_count=0,
        # New folder has no files yet — fall back to the folder's own
        # updated_at so this matches the list-route semantics (#1770).
        latest_activity_at=folder.updated_at,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.get("/folders/{folder_id}", response_model=FolderResponse)
async def get_folder(
    folder_id: int,
    db: AsyncSession = Depends(get_db),
    _: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get a folder by ID."""
    result = await db.execute(
        select(LibraryFolder, Project.name, PrintArchive.print_name)
        .outerjoin(Project, LibraryFolder.project_id == Project.id)
        .outerjoin(PrintArchive, LibraryFolder.archive_id == PrintArchive.id)
        .where(LibraryFolder.id == folder_id)
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Folder not found")

    folder, project_name, archive_name = row

    # Get file count + latest file activity (#1770) in one trip
    agg_result = await db.execute(
        select(
            func.count(LibraryFile.id),
            func.max(LibraryFile.updated_at),
        ).where(
            LibraryFile.folder_id == folder_id,
            LibraryFile.deleted_at.is_(None),
        )
    )
    file_count, latest_file = agg_result.one()
    file_count = file_count or 0
    latest_activity_at = max(folder.updated_at, latest_file) if latest_file is not None else folder.updated_at

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        is_external=folder.is_external,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        file_count=file_count,
        latest_activity_at=latest_activity_at,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


_README_BYTES_CAP = 512 * 1024  # 512 KiB — model descriptions don't need more
_README_PREFERRED_STEMS = ("readme", "description")


@router.get("/folders/{folder_id}/readme", response_model=FolderReadmeResponse)
async def get_folder_readme(
    folder_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Return the first markdown description file for a folder (#1268).

    Picks ``README.md`` / ``readme.md`` / ``description.md`` first (any case),
    otherwise the alphabetically-first ``*.md`` in the folder. 404 when no
    markdown file is present so the FE can hide the side panel.
    """
    user, can_read_all = auth_result

    folder_row = await db.execute(select(LibraryFolder.id).where(LibraryFolder.id == folder_id))
    if folder_row.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Folder not found")

    query = LibraryFile.active().where(
        LibraryFile.folder_id == folder_id,
        func.lower(LibraryFile.filename).like("%.md"),
    )
    if user is not None and not can_read_all:
        query = query.where(LibraryFile.created_by_id == user.id)
    result = await db.execute(query)
    candidates = result.scalars().all()
    if not candidates:
        raise HTTPException(status_code=404, detail="No markdown description in folder")

    def sort_key(f: LibraryFile) -> tuple[int, str]:
        stem = os.path.splitext(f.filename.lower())[0]
        try:
            return (_README_PREFERRED_STEMS.index(stem), f.filename.lower())
        except ValueError:
            return (len(_README_PREFERRED_STEMS), f.filename.lower())

    pick = sorted(candidates, key=sort_key)[0]

    abs_path = to_absolute_path(pick.file_path)
    if not abs_path or not abs_path.exists():
        raise HTTPException(status_code=404, detail="Markdown file missing on disk")

    try:
        raw = abs_path.read_bytes()
    except OSError as e:
        logger.warning("Folder readme read failed for %s: %s", abs_path, e)
        raise HTTPException(status_code=500, detail="Could not read markdown file") from None

    truncated = len(raw) > _README_BYTES_CAP
    if truncated:
        raw = raw[:_README_BYTES_CAP]
    # `errors="replace"` so a single bad byte never blanks the panel.
    content = raw.decode("utf-8", errors="replace")

    return FolderReadmeResponse(filename=pick.filename, content=content, truncated=truncated)


@router.put("/folders/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: int,
    data: FolderUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPDATE_ALL)),
):
    """Update a folder.

    Note: Folders require library:update_all permission since they don't have
    ownership tracking.
    """
    result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
    folder = result.scalar_one_or_none()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if data.name is not None:
        folder.name = data.name

    if data.parent_id is not None:
        # Prevent circular reference
        if data.parent_id == folder_id:
            raise HTTPException(status_code=400, detail="Folder cannot be its own parent")

        # Check for circular reference in ancestors
        if data.parent_id != 0:  # 0 means move to root
            current_id = data.parent_id
            while current_id is not None:
                if current_id == folder_id:
                    raise HTTPException(status_code=400, detail="Cannot move folder into its own subtree")
                parent_result = await db.execute(select(LibraryFolder.parent_id).where(LibraryFolder.id == current_id))
                current_id = parent_result.scalar()

            folder.parent_id = data.parent_id
        else:
            folder.parent_id = None

    # Update project_id (0 to unlink)
    if data.project_id is not None:
        if data.project_id == 0:
            folder.project_id = None
        else:
            # Verify project exists
            project_result = await db.execute(select(Project).where(Project.id == data.project_id))
            if not project_result.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Project not found")
            folder.project_id = data.project_id

    # Update archive_id (0 to unlink)
    if data.archive_id is not None:
        if data.archive_id == 0:
            folder.archive_id = None
        else:
            # Verify archive exists
            archive_result = await db.execute(select(PrintArchive).where(PrintArchive.id == data.archive_id))
            if not archive_result.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Archive not found")
            folder.archive_id = data.archive_id

    await db.commit()
    await db.refresh(folder)

    # Get file count + latest file activity (#1770) and names
    agg_result = await db.execute(
        select(
            func.count(LibraryFile.id),
            func.max(LibraryFile.updated_at),
        ).where(
            LibraryFile.folder_id == folder_id,
            LibraryFile.deleted_at.is_(None),
        )
    )
    file_count, latest_file = agg_result.one()
    file_count = file_count or 0
    latest_activity_at = max(folder.updated_at, latest_file) if latest_file is not None else folder.updated_at

    # Get project and archive names
    project_name = None
    archive_name = None
    if folder.project_id:
        project_result = await db.execute(select(Project.name).where(Project.id == folder.project_id))
        project_name = project_result.scalar()
    if folder.archive_id:
        archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == folder.archive_id))
        archive_name = archive_result.scalar()

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        is_external=folder.is_external,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        file_count=file_count,
        latest_activity_at=latest_activity_at,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_DELETE_ALL)),
):
    """Delete a folder and all its contents (cascade).

    Note: Folders require library:delete_all permission since they don't have
    ownership tracking.
    """
    result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
    folder = result.scalar_one_or_none()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # External folders: only remove DB records, never delete files from external path
    is_ext = folder.is_external

    # Get all files in this folder and subfolders to delete from disk
    async def get_all_file_ids(fid: int) -> list[int]:
        """Recursively get all file IDs in a folder tree."""
        file_ids = []

        # Get files in this folder
        files_result = await db.execute(
            select(LibraryFile.id, LibraryFile.file_path, LibraryFile.thumbnail_path, LibraryFile.is_external).where(
                LibraryFile.folder_id == fid
            )
        )
        for fid_val, file_path, thumb_path, file_is_ext in files_result.all():
            file_ids.append(fid_val)
            # Only delete non-external files from disk
            if not is_ext and not file_is_ext:
                try:
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                    if thumb_path and os.path.exists(thumb_path):
                        os.remove(thumb_path)
                except OSError as e:
                    logger.warning("Failed to delete file: %s", e)

        # Get child folders and recurse
        children_result = await db.execute(select(LibraryFolder.id).where(LibraryFolder.parent_id == fid))
        for (child_id,) in children_result.all():
            file_ids.extend(await get_all_file_ids(child_id))

        return file_ids

    await get_all_file_ids(folder_id)

    # Delete folder (cascade will handle files and subfolders)
    await db.delete(folder)
    await db.commit()

    return {"status": "success", "message": "Folder deleted"}


# ============ External Folder Endpoints ============

# GHSA-r2qv follow-up (audit finding I1): external-folder mount path uses an
# allowlist of operator-opted-in roots rather than the original denylist of
# system directories. The denylist shape was fail-open-on-growth — anything
# not enumerated (``/data`` containing other users' archives, ``/root``,
# arbitrary NFS/SMB mounts, the Bambuddy ``LOG_DIR``) could be mounted by any
# user with ``LIBRARY_UPLOAD``. The allowlist defaults to empty and is
# extended via the ``BAMBUDDY_EXTERNAL_ROOTS`` env var (colon-separated
# absolute paths). The route is additionally gated on ``SETTINGS_UPDATE``
# (admin scope) rather than ``LIBRARY_UPLOAD`` because mounting host paths
# is an operator-level capability that crosses user boundaries.


# Bambuddy-owned data directories. Hardcode-rejected even if the operator
# tries to add them to ``BAMBUDDY_EXTERNAL_ROOTS`` — mounting these would
# allow reading other users' archives, log files, or the static assets path.
def _bambuddy_reserved_roots() -> tuple[Path, ...]:
    """Resolved Bambuddy-owned directories that may NEVER be mounted as an
    external folder regardless of the operator's allowlist.

    Resolved at call time because tests patch ``settings.base_dir`` /
    ``settings.log_dir`` to a temp dir; resolving lazily picks up the
    patched values rather than module-import-time values.
    """
    from backend.app.core.config import settings as app_settings

    reserved = [app_settings.base_dir, app_settings.log_dir, app_settings.static_dir, app_settings.archive_dir]
    return tuple(Path(p).resolve() for p in reserved if p is not None)


def _allowed_external_roots() -> tuple[Path, ...]:
    """Parse ``BAMBUDDY_EXTERNAL_ROOTS`` into resolved allowed roots.

    Empty env var (the default) means external folders are disabled.
    Operators opt in explicitly: ``BAMBUDDY_EXTERNAL_ROOTS=/mnt/library:/srv/3d``
    Returns a tuple of resolved ``Path`` objects; entries that don't
    resolve to absolute paths are silently dropped (operator error, not
    a security boundary). Resolved lazily so tests can monkeypatch.
    """
    raw = os.environ.get("BAMBUDDY_EXTERNAL_ROOTS", "")
    roots: list[Path] = []
    for entry in raw.split(":"):
        entry = entry.strip()
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except (OSError, RuntimeError):  # noqa: BLE001 — operator config error, not a security boundary
            continue
        if resolved.is_absolute():
            roots.append(resolved)
    return tuple(roots)


def _path_within(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is ``parent`` or any descendant.

    Uses ``Path.relative_to`` semantics (raises ``ValueError`` on miss)
    instead of string ``startswith``, which would falsely match
    ``/data-other`` against ``/data``. ``Path.is_relative_to`` is the
    sanctioned form on Python 3.9+; both are available here.
    """
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


# Supported file extensions for external folder scanning
_SCANNABLE_EXTENSIONS = {
    ".3mf",
    ".gcode",
    ".gcode.3mf",
    ".stl",
    ".obj",
    ".step",
    ".stp",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".md",
}


def _validate_external_path(path_str: str) -> Path:
    """Validate an external path is safe to mount.

    Allowlist semantics:
    1. Path must be absolute and resolve cleanly (symlink-escape rejected
       implicitly by the resolved-startswith check below).
    2. Path must fall under one of the roots enumerated in
       ``BAMBUDDY_EXTERNAL_ROOTS``; empty allowlist (the default)
       means external folders are not available on this deployment.
    3. Path must NOT fall under any Bambuddy-owned directory (``base_dir``,
       ``log_dir``, ``static_dir``, ``archive_dir``) — the reserved set
       takes precedence over the allowlist, so an operator who accidentally
       sets ``BAMBUDDY_EXTERNAL_ROOTS=/`` does not expose ``/data``.
    4. Existence + directory-type + readability gates remain.
    """
    path = Path(path_str).resolve()

    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute")

    allowed_roots = _allowed_external_roots()
    if not allowed_roots:
        raise HTTPException(
            status_code=400,
            detail=(
                "External folders are not enabled on this deployment. Ask the "
                "operator to set BAMBUDDY_EXTERNAL_ROOTS=<colon-separated paths>."
            ),
        )

    # Reserved (Bambuddy-owned) paths are rejected before the allowlist check
    # so an over-broad allowlist (e.g. operator set "/" for testing) cannot
    # expose Bambuddy's own data dir or log dir.
    for reserved in _bambuddy_reserved_roots():
        if _path_within(path, reserved):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mount Bambuddy-managed directory: {reserved}",
            )

    if not any(_path_within(path, root) for root in allowed_roots):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path '{path}' is not within an allowed external root. "
                f"Allowed roots: {', '.join(str(r) for r in allowed_roots)}"
            ),
        )

    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {path}")

    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}")

    # Check readability
    if not os.access(path, os.R_OK):
        raise HTTPException(status_code=400, detail=f"Path is not readable: {path}")

    return path


@router.post("/folders/external", response_model=FolderResponse)
async def create_external_folder(
    data: ExternalFolderCreate,
    db: AsyncSession = Depends(get_db),
    # GHSA-r2qv follow-up (I1): elevated from LIBRARY_UPLOAD to SETTINGS_UPDATE.
    # Registering a host filesystem path as a Bambuddy library folder is an
    # operator-level capability that crosses user boundaries (one user's
    # registered external folder is visible to every other user via
    # /api/v1/library/folders). LIBRARY_UPLOAD was always the wrong scope —
    # SETTINGS_UPDATE is the admin-class gate that already protects every
    # other host-affecting setting (SMTP, LDAP, cloud, smart plugs).
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_UPDATE)),
):
    """Create an external folder that points to a host directory."""
    resolved = _validate_external_path(data.external_path)

    # Check no other external folder already points to this path
    existing = await db.execute(
        select(LibraryFolder).where(
            LibraryFolder.is_external.is_(True),
            LibraryFolder.external_path == str(resolved),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An external folder already exists for this path")

    # Verify parent exists if specified
    if data.parent_id is not None:
        parent_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == data.parent_id))
        if not parent_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Parent folder not found")

    folder = LibraryFolder(
        name=data.name,
        parent_id=data.parent_id,
        is_external=True,
        external_path=str(resolved),
        external_readonly=data.readonly,
        external_show_hidden=data.show_hidden,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=None,
        archive_id=None,
        is_external=True,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        file_count=0,
        # Newly-created external folder hasn't been scanned yet — fall back
        # to the folder's own updated_at (#1770).
        latest_activity_at=folder.updated_at,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.post("/folders/{folder_id}/scan")
async def scan_external_folder(
    folder_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
):
    """Scan an external folder and sync files to the database.

    Discovers new files, removes DB entries for deleted files.
    Does not copy files — stores the external path directly.
    """
    result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
    folder = result.scalar_one_or_none()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if not folder.is_external or not folder.external_path:
        raise HTTPException(status_code=400, detail="Not an external folder")

    ext_path = Path(folder.external_path)
    if not ext_path.exists() or not ext_path.is_dir():
        raise HTTPException(status_code=400, detail=f"External path is not accessible: {folder.external_path}")

    # Collect all existing child external subfolder IDs (single query)
    all_folder_ids = [folder_id]
    child_result = await db.execute(
        select(LibraryFolder).where(
            LibraryFolder.is_external.is_(True),
            LibraryFolder.parent_id.isnot(None),
        )
    )
    all_child_folders = child_result.scalars().all()

    # Walk the parent chain to find all descendants of folder_id
    parent_to_children: dict[int, list] = {}
    for cf in all_child_folders:
        parent_to_children.setdefault(cf.parent_id, []).append(cf)

    queue = [folder_id]
    while queue:
        pid = queue.pop()
        for child in parent_to_children.get(pid, []):
            all_folder_ids.append(child.id)
            queue.append(child.id)

    # Get existing DB files across root and all subfolders
    existing_result = await db.execute(
        LibraryFile.active().where(
            LibraryFile.folder_id.in_(all_folder_ids),
            LibraryFile.is_external.is_(True),
        )
    )
    existing_files = {f.file_path: f for f in existing_result.scalars().all()}

    # Build folder cache: relative path -> folder_id (for resolving subfolders)
    # Pre-populate with existing child folders keyed by their external_path
    folder_cache: dict[str, int] = {"": folder_id}
    for fid in all_folder_ids:
        if fid == folder_id:
            continue
        # Find the child folder object
        for cf in all_child_folders:
            if cf.id == fid and cf.external_path:
                try:
                    rel = str(Path(cf.external_path).relative_to(ext_path))
                    if rel != ".":
                        folder_cache[rel] = cf.id
                except ValueError:
                    pass

    # Scan the directory
    added = 0
    removed = 0
    found_paths: set[str] = set()
    seen_rel_dirs: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(ext_path):
        # Filter hidden directories unless configured
        if not folder.external_show_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        rel_dir = str(Path(dirpath).relative_to(ext_path))
        if rel_dir == ".":
            rel_dir = ""
        seen_rel_dirs.add(rel_dir)

        # Resolve or create subfolder chain for this directory
        if rel_dir and rel_dir not in folder_cache:
            parts = Path(rel_dir).parts
            current_path = ""
            current_parent = folder_id
            for part in parts:
                current_path = f"{current_path}/{part}".lstrip("/")
                if current_path in folder_cache:
                    current_parent = folder_cache[current_path]
                else:
                    existing_sub = await db.execute(
                        select(LibraryFolder).where(
                            LibraryFolder.name == part,
                            LibraryFolder.parent_id == current_parent,
                            LibraryFolder.is_external.is_(True),
                        )
                    )
                    existing_folder = existing_sub.scalar_one_or_none()
                    if existing_folder:
                        current_parent = existing_folder.id
                    else:
                        new_folder = LibraryFolder(
                            name=part,
                            parent_id=current_parent,
                            is_external=True,
                            external_path=str(
                                ext_path / current_path
                            ),  # SEC-PATH-OK: current_path built from Path(rel_dir).parts of an os.walk descent under ext_path
                            external_readonly=folder.external_readonly,
                            external_show_hidden=folder.external_show_hidden,
                        )
                        db.add(new_folder)
                        await db.flush()
                        current_parent = new_folder.id
                    folder_cache[current_path] = current_parent

        target_folder_id = folder_cache.get(rel_dir, folder_id)

        for filename in filenames:
            # Skip hidden files unless configured
            if not folder.external_show_hidden and filename.startswith("."):
                continue

            filepath = (
                Path(dirpath) / filename
            )  # SEC-PATH-OK: dirpath + filename from os.walk(ext_path); filesystem-discovered, not user input
            ext = filepath.suffix.lower()

            # Check for compound extensions like .gcode.3mf
            if ext not in _SCANNABLE_EXTENSIONS:
                # Check compound
                compound = "".join(filepath.suffixes[-2:]).lower() if len(filepath.suffixes) >= 2 else ""
                if compound not in _SCANNABLE_EXTENSIONS:
                    continue

            # Resolve symlinks and ensure still under external_path
            try:
                real_path = filepath.resolve()
                real_path.relative_to(ext_path.resolve())
            except (ValueError, OSError):
                continue  # Symlink escapes the external dir

            file_path_str = str(filepath)
            found_paths.add(file_path_str)

            if file_path_str in existing_files:
                continue  # Already tracked

            # Get file info
            try:
                stat = filepath.stat()
            except OSError:
                continue

            file_type = classify_file_type(filename)

            # Extract thumbnail for 3mf files (including .gcode.3mf sliced
            # outputs — those are 3MF zips on disk and carry the same
            # thumbnail Metadata/plate_1.png the parser reads). Pre-#1600
            # the gate was `file_type == "3mf"` alone, so .gcode.3mf files
            # in external folders silently got no thumbnail.
            thumbnail_path = None
            file_metadata = None
            if file_type in ("3mf", "gcode.3mf"):
                try:
                    parser = ThreeMFParser(str(filepath))
                    raw_metadata = parser.parse()
                    if raw_metadata:
                        # Extract thumbnail before cleaning metadata
                        thumb_data = raw_metadata.get("_thumbnail_data")
                        thumbnail_ext = raw_metadata.get("_thumbnail_ext", ".png")
                        if thumb_data:
                            thumb_dir = get_library_thumbnails_dir()
                            thumb_filename = f"{uuid.uuid4().hex}{thumbnail_ext}"
                            thumb_full = (
                                thumb_dir / thumb_filename
                            )  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + thumbnail_ext
                            thumb_full.write_bytes(thumb_data)
                            thumbnail_path = to_relative_path(thumb_full)

                        # Clean metadata - remove non-JSON-serializable data (bytes, etc.)
                        def clean_metadata(obj):
                            if isinstance(obj, dict):
                                return {
                                    k: clean_metadata(v)
                                    for k, v in obj.items()
                                    if not isinstance(v, bytes) and k not in ("_thumbnail_data", "_thumbnail_ext")
                                }
                            elif isinstance(obj, list):
                                return [clean_metadata(i) for i in obj if not isinstance(i, bytes)]
                            elif isinstance(obj, bytes):
                                return None
                            return obj

                        file_metadata = clean_metadata(raw_metadata)
                except Exception as e:
                    logger.debug("Failed to extract metadata from external 3mf %s: %s", filepath, e)

            # STL thumbnails are deferred to a background task spawned after
            # the scan's db.commit() — see _backfill_external_stl_thumbnails.
            # Doing them inline would block the HTTP request for minutes on a
            # large NAS mount (#1299).

            # Extract gcode thumbnail
            if file_type == "gcode" and thumbnail_path is None:
                thumb_data = extract_gcode_thumbnail(filepath)
                if thumb_data:
                    thumb_dir = get_library_thumbnails_dir()
                    thumb_filename = f"{uuid.uuid4().hex}.png"
                    thumb_full = thumb_dir / thumb_filename  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + ".png"
                    thumb_full.write_bytes(thumb_data)
                    thumbnail_path = to_relative_path(thumb_full)

            # Create thumbnail for image files
            if ext.lower() in IMAGE_EXTENSIONS and thumbnail_path is None:
                thumbnail_path_str = create_image_thumbnail(filepath, get_library_thumbnails_dir())
                if thumbnail_path_str:
                    thumbnail_path = to_relative_path(Path(thumbnail_path_str))

            db_file = LibraryFile(
                folder_id=target_folder_id,
                is_external=True,
                filename=filename,
                file_path=file_path_str,
                file_type=file_type,
                file_size=stat.st_size,
                file_hash=None,  # Skip hashing external files for performance
                thumbnail_path=thumbnail_path,
                file_metadata=_without_print_name(file_metadata),
            )
            db.add(db_file)
            added += 1

    # Remove DB entries for files that no longer exist on disk.
    #
    # Gate on actual disk presence, NOT merely absence from found_paths:
    # found_paths only collects extensions in _SCANNABLE_EXTENSIONS, so a
    # record for any other file the upload path admitted (e.g. a .md README,
    # #2520) would otherwise be treated as "deleted from disk" and purged on
    # every scan even though the file is still there. os.path.exists keeps
    # such records; genuinely-deleted files (absent from disk) are still
    # cleaned up. External file_path is the absolute on-disk path.
    for path_str, db_file in existing_files.items():
        if path_str not in found_paths and not os.path.exists(path_str):
            # Clean up thumbnail if we generated one
            if db_file.thumbnail_path:
                try:
                    abs_thumb = to_absolute_path(db_file.thumbnail_path)
                    if abs_thumb and abs_thumb.exists():
                        abs_thumb.unlink()
                except OSError:
                    pass
            await db.delete(db_file)
            removed += 1

    # Remove empty subfolders whose directories no longer exist on disk
    # Process deepest-first by sorting on path depth (descending)
    subfolder_entries = [(rel, fid) for rel, fid in folder_cache.items() if rel and fid != folder_id]
    subfolder_entries.sort(key=lambda x: x[0].count("/"), reverse=True)
    for rel_path, sub_fid in subfolder_entries:
        if rel_path in seen_rel_dirs:
            continue  # Directory still exists on disk
        # Check if subfolder has any remaining files
        file_count_result = await db.execute(
            select(func.count(LibraryFile.id)).where(
                LibraryFile.folder_id == sub_fid,
                LibraryFile.deleted_at.is_(None),
            )
        )
        if (file_count_result.scalar() or 0) == 0:
            # Check if it has any remaining child folders
            child_count_result = await db.execute(
                select(func.count(LibraryFolder.id)).where(LibraryFolder.parent_id == sub_fid)
            )
            if (child_count_result.scalar() or 0) == 0:
                sub_folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == sub_fid))
                sub_folder_obj = sub_folder_result.scalar_one_or_none()
                if sub_folder_obj:
                    await db.delete(sub_folder_obj)

    await db.commit()

    # Spawn STL thumbnail backfill in the background — the scan endpoint
    # returns immediately so the FE modal closes and subdirectories are
    # visible right away; thumbnails fill in over the following seconds /
    # minutes as the task processes each STL file. Survives FE refresh —
    # the task lives in the FastAPI event loop, not the request scope.
    # folder_cache.values() covers the root + every pre-existing subfolder
    # + every subfolder created during this scan. all_folder_ids on its own
    # would miss the newly-created ones (it's snapshotted before the walk).
    spawn_background_task(
        _backfill_external_stl_thumbnails(list(set(folder_cache.values()))),
        name=f"stl-backfill-folder-{folder_id}",
    )

    return {"status": "success", "added": added, "removed": removed}


# ============ File Endpoints ============


@router.get("/files", response_model=list[FileListResponse])
@router.get("/files/", response_model=list[FileListResponse])
async def list_files(
    response: Response,
    folder_id: int | None = None,
    project_id: int | None = None,
    include_root: bool = True,
    internal_only: bool = False,
    external_only: bool = False,
    recursive: bool = False,
    tag_ids: list[int] = Query(default_factory=list),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """List files, optionally filtered by folder or project.

    Args:
        folder_id: Filter by folder ID. If None and include_root=True, returns root files.
        project_id: Return all files across folders linked to this project (bulk fetch, avoids N+1).
        include_root: If True and folder_id is None, returns files at root level.
                     If False and folder_id is None, returns all files.
        internal_only: Restrict the result to files in managed storage (`is_external=False`).
                       Used by the File Manager's "All Files" sidebar entry so a linked NAS
                       with hundreds of files doesn't drown the user's own uploads (#1621).
        external_only: Restrict the result to files under external folders
                       (`is_external=True`) — the symmetric combined view for users with
                       multiple linked external sources (#1621).
        recursive: When combined with ``folder_id``, also include files in every
                   descendant subfolder (#1268). Implemented via a recursive CTE
                   that walks ``library_folders.parent_id``. Default off so
                   existing callers (folder browsing, etc.) keep their narrow
                   single-folder semantics.
        tag_ids: Restrict the listing to files carrying ALL of these tags
                 (AND semantics, #1268). When non-empty the folder filter is
                 intentionally bypassed — tags are cross-cutting and the user
                 wants "every file with this tag" regardless of where it lives.
                 ``recursive`` becomes irrelevant in that case.
    """
    if internal_only and external_only:
        raise HTTPException(
            status_code=400,
            detail="internal_only and external_only are mutually exclusive",
        )

    user, can_read_all = auth_result
    query = LibraryFile.active().options(
        selectinload(LibraryFile.created_by),
        selectinload(LibraryFile.tags),
    )
    if user is not None and not can_read_all:
        query = query.where(LibraryFile.created_by_id == user.id)

    if tag_ids:
        # Cross-cutting filter — every requested tag must be present on the
        # file. JOIN + GROUP BY + HAVING COUNT(DISTINCT) is portable across
        # SQLite and Postgres without dialect tricks. We deliberately skip
        # the folder / project / include_root scoping below so the result
        # is the global "all files carrying these tags".
        unique_tag_ids = list(dict.fromkeys(tag_ids))
        query = (
            query.join(LibraryFileTag, LibraryFileTag.file_id == LibraryFile.id)
            .where(LibraryFileTag.tag_id.in_(unique_tag_ids))
            .group_by(LibraryFile.id)
            .having(func.count(distinct(LibraryFileTag.tag_id)) == len(unique_tag_ids))
        )
    elif folder_id is not None and recursive:
        # Walk the subtree starting at folder_id and collect every descendant
        # id. Recursive CTE works on both SQLite (>=3.8.3, shipped 2014) and
        # Postgres without dialect branching.
        roots = (
            select(LibraryFolder.id).where(LibraryFolder.id == folder_id).cte(name="folder_descendants", recursive=True)
        )
        descendants = roots.union_all(select(LibraryFolder.id).join(roots, LibraryFolder.parent_id == roots.c.id))
        query = query.where(LibraryFile.folder_id.in_(select(descendants.c.id)))
    elif folder_id is not None:
        query = query.where(LibraryFile.folder_id == folder_id)
    elif project_id is not None:
        # Single join instead of one query per folder (avoids N+1 pattern)
        query = query.join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
        query = query.where(LibraryFolder.project_id == project_id)
    elif include_root:
        query = query.where(LibraryFile.folder_id.is_(None))

    if internal_only:
        query = query.where(LibraryFile.is_external.is_(False))
    elif external_only:
        query = query.where(LibraryFile.is_external.is_(True))

    query = query.order_by(LibraryFile.filename)
    result = await db.execute(query)
    files = result.scalars().unique().all() if tag_ids else result.scalars().all()

    # Get duplicate counts
    hash_counts = {}
    if files:
        hashes = [f.file_hash for f in files if f.file_hash]
        if hashes:
            dup_result = await db.execute(
                select(LibraryFile.file_hash, func.count(LibraryFile.id))
                .where(LibraryFile.file_hash.in_(hashes), LibraryFile.deleted_at.is_(None))
                .group_by(LibraryFile.file_hash)
            )
            hash_counts = {h: c - 1 for h, c in dup_result.all()}  # -1 to exclude self

    # Prevent browser caching of file list
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    file_list = []
    for f in files:
        # Extract key metadata for display
        print_name = None
        print_time = None
        filament_grams = None
        sliced_for_model = None
        if f.file_metadata:
            print_name = f.file_metadata.get("print_name")
            print_time = f.file_metadata.get("print_time_seconds")
            filament_grams = f.file_metadata.get("filament_used_grams")
            sliced_for_model = f.file_metadata.get("sliced_for_model")

        file_list.append(
            FileListResponse(
                id=f.id,
                folder_id=f.folder_id,
                is_external=f.is_external,
                filename=f.filename,
                file_type=f.file_type,
                file_size=f.file_size,
                thumbnail_path=f.thumbnail_path,
                print_count=f.print_count,
                duplicate_count=hash_counts.get(f.file_hash, 0) if f.file_hash else 0,
                created_by_id=f.created_by_id,
                created_by_username=f.created_by.username if f.created_by else None,
                created_at=f.created_at,
                print_name=print_name,
                print_time_seconds=print_time,
                filament_used_grams=filament_grams,
                sliced_for_model=sliced_for_model,
                tags=[TagSummary(id=t.id, name=t.name) for t in f.tags],
            )
        )

    return file_list


@router.post("/files", response_model=FileUploadResponse)
@router.post("/files/", response_model=FileUploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    folder_id: int | None = None,
    generate_stl_thumbnails: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
):
    """Upload a file to the library."""
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")

        filename = file.filename
        # Reject FAT32/exFAT-incompatible filenames up front (#1540).
        try:
            validate_print_filename(filename)
        except InvalidFilenameError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        ext = os.path.splitext(filename)[1].lower()
        # `file_type` is compound-aware (`gcode.3mf` for sliced outputs).
        # `ext` stays the trailing extension because the on-disk filename
        # uses it directly and the 3MF-parse branch below still gates on
        # `ext == ".3mf"`, which is correct for both `.3mf` and `.gcode.3mf`.
        file_type = classify_file_type(filename)

        # Verify folder exists if specified
        target_folder = None
        if folder_id is not None:
            folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
            target_folder = folder_result.scalar_one_or_none()
            if not target_folder:
                raise HTTPException(status_code=404, detail="Folder not found")

        # Writable external folders write through to the mount so the file is
        # visible outside Bambuddy (#1112); everything else lands under the
        # internal library dir with a UUID-scoped filename. Resolved BEFORE
        # the content validation below so folder-permission rejections
        # (403 read-only, 400 missing path, 409 collision) still surface
        # before any "bad file format" 400 — preserves existing error
        # ordering / tests.
        file_path, is_external_upload = _resolve_upload_destination(target_folder, filename)

        # Read upload now so the validation can sniff magic bytes; the file
        # is written to disk only after the checks. #1401.
        content = await file.read()
        validate_print_file_upload(filename, content)

        # Save file
        with open(file_path, "wb") as f:
            f.write(content)

        # Calculate hash
        file_hash = calculate_file_hash(file_path)

        # Check for duplicates
        dup_result = await db.execute(
            select(LibraryFile.id).where(LibraryFile.file_hash == file_hash, LibraryFile.deleted_at.is_(None)).limit(1)
        )
        duplicate_of = dup_result.scalar()

        # Extract metadata and thumbnail
        metadata = {}
        thumbnail_path = None
        thumbnails_dir = get_library_thumbnails_dir()

        if ext == ".3mf":
            try:
                parser = ThreeMFParser(str(file_path))
                raw_metadata = parser.parse()

                # Extract thumbnail before cleaning metadata
                thumbnail_data = raw_metadata.get("_thumbnail_data")
                thumbnail_ext = raw_metadata.get("_thumbnail_ext", ".png")

                # Save thumbnail if extracted
                if thumbnail_data:
                    thumb_filename = f"{uuid.uuid4().hex}{thumbnail_ext}"
                    thumb_path = (
                        thumbnails_dir / thumb_filename
                    )  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + thumbnail_ext
                    with open(thumb_path, "wb") as f:
                        f.write(thumbnail_data)
                    thumbnail_path = str(thumb_path)

                # Clean metadata - remove non-JSON-serializable data (bytes, etc.)
                def clean_metadata(obj):
                    if isinstance(obj, dict):
                        return {
                            k: clean_metadata(v)
                            for k, v in obj.items()
                            if not isinstance(v, bytes) and k not in ("_thumbnail_data", "_thumbnail_ext")
                        }
                    elif isinstance(obj, list):
                        return [clean_metadata(i) for i in obj if not isinstance(i, bytes)]
                    elif isinstance(obj, bytes):
                        return None
                    return obj

                metadata = clean_metadata(raw_metadata)
            except Exception as e:
                logger.warning("Failed to parse 3MF: %s", e)

        elif ext == ".gcode":
            # Extract embedded thumbnail from gcode
            try:
                thumbnail_data = extract_gcode_thumbnail(file_path)
                if thumbnail_data:
                    thumb_filename = f"{uuid.uuid4().hex}.png"
                    thumb_path = (
                        thumbnails_dir / thumb_filename
                    )  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + ".png"
                    with open(thumb_path, "wb") as f:
                        f.write(thumbnail_data)
                    thumbnail_path = str(thumb_path)
            except Exception as e:
                logger.warning("Failed to extract gcode thumbnail: %s", e)

        elif ext.lower() in IMAGE_EXTENSIONS:
            # For image files, create a thumbnail from the image itself
            thumbnail_path = create_image_thumbnail(file_path, thumbnails_dir)

        elif ext == ".stl":
            # Generate STL thumbnail if enabled. Same MIN_USABLE_STL_BYTES
            # pre-skip as extract_zip_file — stubs / placeholders below this
            # size can't contain a triangle so trimesh would return an empty
            # mesh anyway.
            if generate_stl_thumbnails:
                try:
                    if file_path.stat().st_size >= MIN_USABLE_STL_BYTES:
                        thumbnail_path = generate_stl_thumbnail(file_path, thumbnails_dir)
                except OSError:
                    pass

        # Create database entry (managed files store relative paths for portability;
        # external files store the absolute mount path — same shape as scan produces)
        library_file = LibraryFile(
            folder_id=folder_id,
            is_external=is_external_upload,
            filename=filename,
            file_path=_stored_file_path(file_path, is_external_upload),
            file_type=file_type,
            file_size=len(content),
            file_hash=file_hash,
            thumbnail_path=to_relative_path(thumbnail_path) if thumbnail_path else None,
            file_metadata=_without_print_name(metadata) if metadata else None,
            created_by_id=current_user.id if current_user else None,
        )
        db.add(library_file)
        await db.commit()
        await db.refresh(library_file)

        return FileUploadResponse(
            id=library_file.id,
            filename=library_file.filename,
            file_type=library_file.file_type,
            file_size=library_file.file_size,
            thumbnail_path=library_file.thumbnail_path,
            duplicate_of=duplicate_of,
            metadata=library_file.file_metadata,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Upload failed for %s: %s", file.filename, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/files/extract-zip", response_model=ZipExtractResponse)
async def extract_zip_file(
    file: UploadFile = File(...),
    folder_id: int | None = Query(default=None),
    preserve_structure: bool = Query(default=True),
    create_folder_from_zip: bool = Query(default=False),
    generate_stl_thumbnails: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
):
    """Upload and extract a ZIP file to the library.

    Args:
        file: The ZIP file to extract
        folder_id: Target folder ID (None = root)
        preserve_structure: If True, recreate folder structure from ZIP; if False, extract all files flat
        create_folder_from_zip: If True, create a folder named after the ZIP file and extract into it
        generate_stl_thumbnails: If True, generate thumbnails for STL files
    """
    import tempfile

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are supported")

    # Verify target folder exists if specified
    if folder_id is not None:
        folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
        target_folder = folder_result.scalar_one_or_none()
        if not target_folder:
            raise HTTPException(status_code=404, detail="Target folder not found")
        if target_folder.is_external and target_folder.external_readonly:
            raise HTTPException(status_code=403, detail="Cannot extract ZIP to a read-only external folder")
        if target_folder.is_external:
            # Writable external folders aren't supported by extract-zip because the
            # nested-subfolder creation path would need to mkdir on the mount and
            # create matching is_external=True LibraryFolder rows — a separate
            # design. Direct the user at Scan, which already handles that shape
            # (#1112).
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot extract ZIP directly into an external folder. "
                    "Extract the ZIP on the external mount and run 'Scan External Folder' instead."
                ),
            )

    # Save ZIP to temp file
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save ZIP file: {str(e)}")

    extracted_files: list[ZipExtractResult] = []
    errors: list[ZipExtractError] = []
    folders_created = 0
    folder_cache: dict[str, int] = {}  # path -> folder_id

    # If create_folder_from_zip is True, create a folder named after the ZIP file
    zip_folder_id = folder_id
    logger.info(
        f"ZIP extraction: create_folder_from_zip={create_folder_from_zip}, folder_id={folder_id}, filename={file.filename}"
    )
    if create_folder_from_zip and file.filename:
        # Remove .zip extension to get folder name
        zip_folder_name = file.filename[:-4] if file.filename.lower().endswith(".zip") else file.filename
        # Check if folder already exists
        existing = await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.name == zip_folder_name,
                LibraryFolder.parent_id == folder_id if folder_id else LibraryFolder.parent_id.is_(None),
            )
        )
        existing_folder = existing.scalar_one_or_none()
        if existing_folder:
            zip_folder_id = existing_folder.id
            logger.info("Reusing existing folder '%s' with id=%s", zip_folder_name, zip_folder_id)
        else:
            # Create folder
            new_folder = LibraryFolder(name=zip_folder_name, parent_id=folder_id)
            db.add(new_folder)
            await db.flush()
            await db.commit()  # Commit folder creation immediately
            zip_folder_id = new_folder.id
            folders_created += 1
            logger.info("Created new folder '%s' with id=%s", zip_folder_name, zip_folder_id)

    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            # Filter out directories and hidden/system files
            file_list = [
                name
                for name in zf.namelist()
                if not name.endswith("/")
                and not name.startswith("__MACOSX")
                and not os.path.basename(name).startswith(".")
            ]

            for zip_path in file_list:
                try:
                    # Determine target folder (use zip_folder_id as base if create_folder_from_zip was used)
                    target_folder_id = zip_folder_id

                    if preserve_structure:
                        # Get directory path from ZIP
                        dir_path = os.path.dirname(zip_path)
                        if dir_path:
                            # Create folder structure
                            parts = dir_path.split("/")
                            current_parent = zip_folder_id
                            current_path = ""

                            for part in parts:
                                if not part:
                                    continue
                                current_path = f"{current_path}/{part}" if current_path else part

                                if current_path in folder_cache:
                                    current_parent = folder_cache[current_path]
                                else:
                                    # Check if folder exists
                                    existing = await db.execute(
                                        select(LibraryFolder).where(
                                            LibraryFolder.name == part,
                                            LibraryFolder.parent_id == current_parent
                                            if current_parent
                                            else LibraryFolder.parent_id.is_(None),
                                        )
                                    )
                                    existing_folder = existing.scalar_one_or_none()

                                    if existing_folder:
                                        current_parent = existing_folder.id
                                    else:
                                        # Create folder
                                        new_folder = LibraryFolder(name=part, parent_id=current_parent)
                                        db.add(new_folder)
                                        await db.flush()
                                        current_parent = new_folder.id
                                        folders_created += 1

                                    folder_cache[current_path] = current_parent

                            target_folder_id = current_parent

                    # Extract file
                    filename = os.path.basename(zip_path)
                    ext = os.path.splitext(filename)[1].lower()
                    file_type = classify_file_type(filename)

                    # Generate unique filename for storage
                    unique_filename = f"{uuid.uuid4().hex}{ext}"
                    file_path = (
                        get_library_files_dir() / unique_filename
                    )  # SEC-PATH-OK: unique_filename = uuid.uuid4().hex + ext

                    # Extract and save file
                    file_content = zf.read(zip_path)
                    with open(file_path, "wb") as f:
                        f.write(file_content)

                    # Calculate hash
                    file_hash = calculate_file_hash(file_path)

                    # Extract metadata and thumbnail for 3MF files
                    metadata = {}
                    thumbnail_path = None
                    thumbnails_dir = get_library_thumbnails_dir()

                    if ext == ".3mf":
                        try:
                            parser = ThreeMFParser(str(file_path))
                            raw_metadata = parser.parse()

                            thumbnail_data = raw_metadata.get("_thumbnail_data")
                            thumbnail_ext = raw_metadata.get("_thumbnail_ext", ".png")

                            if thumbnail_data:
                                thumb_filename = f"{uuid.uuid4().hex}{thumbnail_ext}"
                                thumb_path = (
                                    thumbnails_dir / thumb_filename
                                )  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + thumbnail_ext
                                with open(thumb_path, "wb") as f:
                                    f.write(thumbnail_data)
                                thumbnail_path = str(thumb_path)

                            def clean_metadata(obj):
                                if isinstance(obj, dict):
                                    return {
                                        k: clean_metadata(v)
                                        for k, v in obj.items()
                                        if not isinstance(v, bytes) and k not in ("_thumbnail_data", "_thumbnail_ext")
                                    }
                                elif isinstance(obj, list):
                                    return [clean_metadata(i) for i in obj if not isinstance(i, bytes)]
                                elif isinstance(obj, bytes):
                                    return None
                                return obj

                            metadata = clean_metadata(raw_metadata)
                        except Exception as e:
                            logger.warning("Failed to parse 3MF from ZIP: %s", e)

                    elif ext == ".gcode":
                        try:
                            thumbnail_data = extract_gcode_thumbnail(file_path)
                            if thumbnail_data:
                                thumb_filename = f"{uuid.uuid4().hex}.png"
                                thumb_path = (
                                    thumbnails_dir / thumb_filename
                                )  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + ".png"
                                with open(thumb_path, "wb") as f:
                                    f.write(thumbnail_data)
                                thumbnail_path = str(thumb_path)
                        except Exception as e:
                            logger.warning("Failed to extract gcode thumbnail from ZIP: %s", e)

                    elif ext.lower() in IMAGE_EXTENSIONS:
                        thumbnail_path = create_image_thumbnail(file_path, thumbnails_dir)

                    elif ext == ".stl":
                        # Generate STL thumbnail if enabled. Pre-skip files
                        # below MIN_USABLE_STL_BYTES — they can't contain
                        # even a single triangle, and bulk-uploaded ZIPs of
                        # stub STLs would otherwise log one debug line per
                        # file via the empty-mesh branch in trimesh.load.
                        if generate_stl_thumbnails and len(file_content) >= MIN_USABLE_STL_BYTES:
                            thumbnail_path = generate_stl_thumbnail(file_path, thumbnails_dir)

                    # Create database entry (store relative paths for portability)
                    library_file = LibraryFile(
                        folder_id=target_folder_id,
                        filename=filename,
                        file_path=to_relative_path(file_path),
                        file_type=file_type,
                        file_size=len(file_content),
                        file_hash=file_hash,
                        thumbnail_path=to_relative_path(thumbnail_path) if thumbnail_path else None,
                        file_metadata=_without_print_name(metadata) if metadata else None,
                        created_by_id=current_user.id if current_user else None,
                    )
                    db.add(library_file)
                    await db.flush()
                    await db.refresh(library_file)

                    extracted_files.append(
                        ZipExtractResult(
                            filename=filename,
                            file_id=library_file.id,
                            folder_id=target_folder_id,
                        )
                    )

                    # Commit after each file to release database lock
                    # This prevents long-running transactions from blocking other requests
                    await db.commit()

                except Exception as e:
                    logger.error("Failed to extract %s: %s", zip_path, e)
                    errors.append(ZipExtractError(filename=os.path.basename(zip_path), error=str(e)))
                    # Rollback the failed file but continue with others
                    await db.rollback()

        return ZipExtractResponse(
            extracted=len(extracted_files),
            folders_created=folders_created,
            files=extracted_files,
            errors=errors,
        )

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file")
    except Exception as e:
        logger.error("ZIP extraction failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"ZIP extraction failed: {str(e)}")
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass  # Best-effort temp file cleanup; ignore if already removed


# ============ STL Thumbnail Batch Generation ============


@router.post("/generate-stl-thumbnails", response_model=BatchThumbnailResponse)
async def batch_generate_stl_thumbnails(
    request: BatchThumbnailRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPDATE_ALL)),
):
    """Generate thumbnails for STL files in batch.

    Note: Requires library:update_all permission since this is a batch operation
    that may affect files owned by different users.

    Can generate thumbnails for:
    - Specific file IDs (file_ids)
    - All STL files in a folder (folder_id)
    - All STL files missing thumbnails (all_missing=True)
    """
    thumbnails_dir = get_library_thumbnails_dir()
    results: list[BatchThumbnailResult] = []

    # Build query based on request
    query = LibraryFile.active().where(LibraryFile.file_type == "stl")

    if request.file_ids:
        # Specific files
        query = query.where(LibraryFile.id.in_(request.file_ids))
    elif request.folder_id is not None:
        # All STL files in a specific folder
        query = query.where(LibraryFile.folder_id == request.folder_id)
        if not request.all_missing:
            # If not specifically asking for missing thumbnails, get all
            pass
        else:
            query = query.where(LibraryFile.thumbnail_path.is_(None))
    elif request.all_missing:
        # All STL files without thumbnails
        query = query.where(LibraryFile.thumbnail_path.is_(None))
    else:
        # No criteria specified - return empty
        return BatchThumbnailResponse(
            processed=0,
            succeeded=0,
            failed=0,
            results=[],
        )

    result = await db.execute(query)
    stl_files = result.scalars().all()

    succeeded = 0
    failed = 0

    for stl_file in stl_files:
        file_path = to_absolute_path(stl_file.file_path)

        if not file_path or not file_path.exists():
            results.append(
                BatchThumbnailResult(
                    file_id=stl_file.id,
                    filename=stl_file.filename,
                    success=False,
                    error="File not found on disk",
                )
            )
            failed += 1
            continue

        try:
            thumbnail_path = generate_stl_thumbnail(file_path, thumbnails_dir)

            if thumbnail_path:
                # Update database with relative path
                stl_file.thumbnail_path = to_relative_path(thumbnail_path)
                await db.flush()
                results.append(
                    BatchThumbnailResult(
                        file_id=stl_file.id,
                        filename=stl_file.filename,
                        success=True,
                    )
                )
                succeeded += 1
            else:
                results.append(
                    BatchThumbnailResult(
                        file_id=stl_file.id,
                        filename=stl_file.filename,
                        success=False,
                        error="Thumbnail generation failed",
                    )
                )
                failed += 1
        except Exception as e:
            logger.error("Failed to generate thumbnail for %s: %s", stl_file.filename, e)
            results.append(
                BatchThumbnailResult(
                    file_id=stl_file.id,
                    filename=stl_file.filename,
                    success=False,
                    error=str(e),
                )
            )
            failed += 1

    await db.commit()

    return BatchThumbnailResponse(
        processed=len(stl_files),
        succeeded=succeeded,
        failed=failed,
        results=results,
    )


# ============ Queue Operations ============
# NOTE: These routes must be defined BEFORE /files/{file_id} to avoid path parameter conflicts


def is_sliced_file(filename: str) -> bool:
    """Check if a file is a sliced (printable) file.

    Sliced files are:
    - .gcode files
    - .3mf files that contain '.gcode.' in the name (e.g., filename.gcode.3mf)
    """
    lower = filename.lower()
    return lower.endswith(".gcode") or ".gcode." in lower


@router.post("/files/add-to-queue", response_model=AddToQueueResponse)
async def add_files_to_queue(
    request: AddToQueueRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.QUEUE_CREATE)),
):
    """Add library files to the print queue.

    Only sliced files (.gcode or .gcode.3mf) can be added to the queue.
    The archive will be created automatically when the print starts.
    """
    added: list[AddToQueueResult] = []
    errors: list[AddToQueueError] = []

    # Get all requested files
    result = await db.execute(LibraryFile.active().where(LibraryFile.id.in_(request.file_ids)))
    files = {f.id: f for f in result.scalars().all()}

    # Get max position for queue ordering
    pos_result = await db.execute(select(func.coalesce(func.max(PrintQueueItem.position), 0)))
    max_position = pos_result.scalar() or 0

    for file_id in request.file_ids:
        lib_file = files.get(file_id)

        if not lib_file:
            errors.append(AddToQueueError(file_id=file_id, filename="(not found)", error="File not found"))
            continue

        # Validate file is sliced
        if not is_sliced_file(lib_file.filename):
            errors.append(
                AddToQueueError(
                    file_id=file_id,
                    filename=lib_file.filename,
                    error="Not a sliced file. Only .gcode or .gcode.3mf files can be printed.",
                )
            )
            continue

        try:
            # Verify file exists on disk
            file_path = Path(app_settings.base_dir) / lib_file.file_path

            if not file_path.exists():
                errors.append(
                    AddToQueueError(file_id=file_id, filename=lib_file.filename, error="File not found on disk")
                )
                continue

            # Create queue item referencing library file (archive created at print start)
            max_position += 1
            queue_item = PrintQueueItem(
                printer_id=None,  # Unassigned
                library_file_id=file_id,
                position=max_position,
                status="pending",
            )
            db.add(queue_item)

            await db.flush()  # Get queue_item.id

            added.append(
                AddToQueueResult(
                    file_id=file_id,
                    filename=lib_file.filename,
                    queue_item_id=queue_item.id,
                )
            )

        except Exception as e:
            logger.exception("Error adding file %s to queue", file_id)
            errors.append(AddToQueueError(file_id=file_id, filename=lib_file.filename, error=str(e)))

    await db.commit()

    return AddToQueueResponse(added=added, errors=errors)


@router.get("/files/{file_id}/plates")
async def get_library_file_plates(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get available plates from a multi-plate 3MF library file.

    Returns a list of plates with their index, name, thumbnail availability,
    and filament requirements. For single-plate exports, returns a single plate.
    """
    import json

    import defusedxml.ElementTree as ET

    user, can_read_all = auth_result
    # Get the library file
    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    lib_file = _ensure_library_file_visible(result.scalar_one_or_none(), user, can_read_all)

    if not lib_file:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(app_settings.base_dir) / lib_file.file_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Only 3MF files have plates
    if not lib_file.filename.lower().endswith(".3mf"):
        return {"file_id": file_id, "filename": lib_file.filename, "plates": [], "is_multi_plate": False}

    plates = []
    # Printer / process preset names the 3MF was prepared with — used by the
    # SliceModal to default its dropdowns (#1325). Initialised here so the
    # final return never raises NameError when the file isn't a valid zip.
    embedded_presets: dict[str, str | None] = {"printer": None, "process": None}

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = zf.namelist()
            embedded_presets = extract_embedded_presets_from_3mf(zf)

            # Find all plate gcode files to determine available plates
            gcode_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".gcode")]

            # If no gcode is present (source-only or unsliced), fall back to plate JSON/PNG
            plate_indices: list[int] = []
            if gcode_files:
                # Extract plate indices from gcode filenames
                for gf in gcode_files:
                    try:
                        plate_str = gf[15:-6]  # Remove "Metadata/plate_" and ".gcode"
                        plate_indices.append(int(plate_str))
                    except ValueError:
                        pass  # Skip gcode file with non-numeric plate index
            else:
                plate_json_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".json")]
                plate_png_files = [
                    n
                    for n in namelist
                    if n.startswith("Metadata/plate_")
                    and n.endswith(".png")
                    and "_small" not in n
                    and "no_light" not in n
                ]
                plate_name_candidates = plate_json_files + plate_png_files
                plate_re = re.compile(r"^Metadata/plate_(\d+)\.(json|png)$")
                seen_indices: set[int] = set()
                for name in plate_name_candidates:
                    match = plate_re.match(name)
                    if match:
                        try:
                            index = int(match.group(1))
                        except ValueError:
                            continue
                        if index in seen_indices:
                            continue
                        seen_indices.add(index)
                        plate_indices.append(index)

            if not plate_indices:
                # No plate metadata found
                return {"file_id": file_id, "filename": lib_file.filename, "plates": [], "is_multi_plate": False}

            plate_indices.sort()

            # Parse model_settings.config for plate names + object assignments
            plate_names = {}
            plate_object_ids: dict[int, list[str]] = {}
            object_names_by_id: dict[str, str] = {}
            if "Metadata/model_settings.config" in namelist:
                try:
                    model_content = zf.read("Metadata/model_settings.config").decode()
                    model_root = ET.fromstring(model_content)
                    for obj_elem in model_root.findall(".//object"):
                        obj_id = obj_elem.get("id")
                        if not obj_id:
                            continue
                        name_meta = obj_elem.find("metadata[@key='name']")
                        obj_name = name_meta.get("value") if name_meta is not None else None
                        if obj_name:
                            object_names_by_id[obj_id] = obj_name

                    for plate_elem in model_root.findall(".//plate"):
                        plater_id = None
                        plater_name = None
                        for meta in plate_elem.findall("metadata"):
                            key = meta.get("key")
                            value = meta.get("value")
                            if key == "plater_id" and value:
                                try:
                                    plater_id = int(value)
                                except ValueError:
                                    pass  # Ignore plate with non-numeric plater_id
                            elif key == "plater_name" and value:
                                plater_name = value.strip()
                        if plater_id is not None and plater_name:
                            plate_names[plater_id] = plater_name

                        if plater_id is not None:
                            for instance_elem in plate_elem.findall("model_instance"):
                                for inst_meta in instance_elem.findall("metadata"):
                                    if inst_meta.get("key") == "object_id":
                                        obj_id = inst_meta.get("value")
                                        if not obj_id:
                                            continue
                                        plate_object_ids.setdefault(plater_id, [])
                                        if obj_id not in plate_object_ids[plater_id]:
                                            plate_object_ids[plater_id].append(obj_id)
                except Exception:
                    pass  # model_settings.config is optional; skip if missing or malformed

            # Parse slice_info.config for plate metadata
            plate_metadata = {}
            if "Metadata/slice_info.config" in namelist:
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                for plate_elem in root.findall(".//plate"):
                    plate_info = {"filaments": [], "prediction": None, "weight": None, "name": None, "objects": []}

                    plate_index = None
                    for meta in plate_elem.findall("metadata"):
                        key = meta.get("key")
                        value = meta.get("value")
                        if key == "index" and value:
                            try:
                                plate_index = int(value)
                            except ValueError:
                                pass  # Ignore plate with non-numeric index
                        elif key == "prediction" and value:
                            try:
                                plate_info["prediction"] = int(value)
                            except ValueError:
                                pass  # Leave prediction as None if not a valid integer
                        elif key == "weight" and value:
                            try:
                                plate_info["weight"] = float(value)
                            except ValueError:
                                pass  # Leave weight as None if not a valid number

                    # Get filaments used in this plate
                    for filament_elem in plate_elem.findall("filament"):
                        filament_id = filament_elem.get("id")
                        filament_type = filament_elem.get("type", "")
                        filament_color = filament_elem.get("color", "")
                        used_g = filament_elem.get("used_g", "0")
                        used_m = filament_elem.get("used_m", "0")

                        try:
                            used_grams = float(used_g)
                        except (ValueError, TypeError):
                            used_grams = 0

                        if used_grams > 0 and filament_id:
                            plate_info["filaments"].append(
                                {
                                    "slot_id": int(filament_id),
                                    "type": filament_type,
                                    "color": filament_color,
                                    "used_grams": round(used_grams, 1),
                                    "used_meters": float(used_m) if used_m else 0,
                                }
                            )

                    plate_info["filaments"].sort(key=lambda x: x["slot_id"])

                    # Collect object names
                    for obj_elem in plate_elem.findall("object"):
                        obj_name = obj_elem.get("name")
                        if obj_name and obj_name not in plate_info["objects"]:
                            plate_info["objects"].append(obj_name)

                    # Set plate name
                    if plate_index is not None:
                        custom_name = plate_names.get(plate_index)
                        if custom_name:
                            plate_info["name"] = custom_name
                        elif plate_info["objects"]:
                            plate_info["name"] = plate_info["objects"][0]
                        plate_metadata[plate_index] = plate_info

            # Parse plate_*.json for object lists when slice_info is missing
            plate_json_objects: dict[int, list[str]] = {}
            for name in namelist:
                match = re.match(r"^Metadata/plate_(\d+)\.json$", name)
                if not match:
                    continue
                try:
                    plate_index = int(match.group(1))
                except ValueError:
                    continue
                try:
                    payload = json.loads(zf.read(name).decode())
                    bbox_objects = payload.get("bbox_objects", [])
                    names: list[str] = []
                    for obj in bbox_objects:
                        obj_name = obj.get("name") if isinstance(obj, dict) else None
                        if obj_name and obj_name not in names:
                            names.append(obj_name)
                    if names:
                        plate_json_objects[plate_index] = names
                except Exception:
                    continue

            # Build plate list
            for idx in plate_indices:
                meta = plate_metadata.get(idx, {})
                has_thumbnail = f"Metadata/plate_{idx}.png" in namelist
                objects = meta.get("objects", [])
                if not objects:
                    objects = plate_json_objects.get(idx, [])
                if not objects and plate_object_ids.get(idx):
                    objects = [
                        object_names_by_id.get(obj_id, f"Object {obj_id}") for obj_id in plate_object_ids.get(idx, [])
                    ]

                plate_name = meta.get("name")
                if not plate_name:
                    plate_name = plate_names.get(idx)
                if not plate_name and objects:
                    plate_name = objects[0]

                plates.append(
                    {
                        "index": idx,
                        "name": plate_name,
                        "objects": objects,
                        "object_count": len(objects),
                        "has_thumbnail": has_thumbnail,
                        "thumbnail_url": f"/api/v1/library/files/{file_id}/plate-thumbnail/{idx}"
                        if has_thumbnail
                        else None,
                        "print_time_seconds": meta.get("prediction"),
                        "filament_used_grams": meta.get("weight"),
                        "filaments": meta.get("filaments", []),
                    }
                )

    except Exception as e:
        logger.warning("Failed to parse plates from library file %s: %s", file_id, e)

    return {
        "file_id": file_id,
        "filename": lib_file.filename,
        "plates": plates,
        "is_multi_plate": len(plates) > 1,
        "embedded_printer": embedded_presets["printer"],
        "embedded_process": embedded_presets["process"],
    }


@router.get("/files/{file_id}/plate-thumbnail/{plate_index}")
async def get_library_file_plate_thumbnail(
    file_id: int,
    plate_index: int,
    db: AsyncSession = Depends(get_db),
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """Get the thumbnail image for a specific plate from a library file."""
    from starlette.responses import Response

    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    lib_file = result.scalar_one_or_none()

    if not lib_file:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(app_settings.base_dir) / lib_file.file_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            thumb_path = f"Metadata/plate_{plate_index}.png"
            if thumb_path in zf.namelist():
                data = zf.read(thumb_path)
                return Response(content=data, media_type="image/png")
    except Exception:
        pass  # Archive unreadable or thumbnail missing; fall through to 404

    raise HTTPException(status_code=404, detail=f"Thumbnail for plate {plate_index} not found")


async def _try_preview_slice_filaments(
    db: AsyncSession,
    *,
    kind: str,
    source_id: int,
    plate_id: int,
    file_path: Path,
    request_id: str | None = None,
) -> list[dict] | None:
    """Run a preview slice via the user's configured sidecar. Same shape as
    the matching helper in archives.py — see that module for rationale.

    ``request_id``: when supplied, forwarded to the sidecar so the
    SliceModal's inline spinner + toast can poll the matching progress
    endpoint and show "Generating G-code (45%)" for the preview as well.
    """
    from backend.app.api.routes.settings import get_setting
    from backend.app.services.slice_preview import get_preview_filaments

    preferred = (await get_setting(db, "preferred_slicer")) or "bambu_studio"
    if preferred == "orcaslicer":
        configured = await get_setting(db, "orcaslicer_api_url")
        api_url = (configured or app_settings.slicer_api_url).strip()
    elif preferred == "bambu_studio":
        configured = await get_setting(db, "bambu_studio_api_url")
        api_url = (configured or app_settings.bambu_studio_api_url).strip()
    else:
        return None
    if not api_url:
        return None

    try:
        file_bytes = file_path.read_bytes()
    except OSError:
        return None
    return await get_preview_filaments(
        kind=kind,
        source_id=source_id,
        plate_id=plate_id,
        file_bytes=file_bytes,
        file_name=file_path.name,
        api_url=api_url,
        request_id=request_id,
    )


@router.get("/files/{file_id}/filament-requirements")
async def get_library_file_filament_requirements(
    file_id: int,
    plate_id: int | None = None,
    request_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get filament requirements from a library file.

    Parses the 3MF file to extract filament slot IDs, types, colors, and usage.
    This enables AMS slot assignment when printing from the file manager.

    Args:
        file_id: The library file ID
        plate_id: Optional plate index to get filaments for a specific plate
    """
    import defusedxml.ElementTree as ET

    user, can_read_all = auth_result
    # Get the library file
    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    lib_file = _ensure_library_file_visible(result.scalar_one_or_none(), user, can_read_all)

    # Get the full file path
    file_path = Path(app_settings.base_dir) / lib_file.file_path

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Only 3MF files have parseable filament info
    if not lib_file.filename.lower().endswith(".3mf"):
        return {"file_id": file_id, "filename": lib_file.filename, "plate_id": plate_id, "filaments": []}

    filaments = []

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # Parse slice_info.config for filament requirements
            if "Metadata/slice_info.config" in zf.namelist():
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                if plate_id is not None:
                    # Find filaments for specific plate
                    for plate_elem in root.findall(".//plate"):
                        # Check if this is the requested plate
                        plate_index = None
                        for meta in plate_elem.findall("metadata"):
                            if meta.get("key") == "index":
                                try:
                                    plate_index = int(meta.get("value", ""))
                                except ValueError:
                                    pass  # Skip plate with non-numeric index value
                                break

                        if plate_index == plate_id:
                            # Extract filaments from this plate
                            for filament_elem in plate_elem.findall("filament"):
                                filament_id = filament_elem.get("id")
                                filament_type = filament_elem.get("type", "")
                                filament_color = filament_elem.get("color", "")
                                used_g = filament_elem.get("used_g", "0")
                                used_m = filament_elem.get("used_m", "0")

                                tray_info_idx = filament_elem.get("tray_info_idx", "")

                                try:
                                    used_grams = float(used_g)
                                except (ValueError, TypeError):
                                    used_grams = 0

                                if used_grams > 0 and filament_id:
                                    filaments.append(
                                        {
                                            "slot_id": int(filament_id),
                                            "type": filament_type,
                                            "color": filament_color,
                                            "used_grams": round(used_grams, 1),
                                            "used_meters": float(used_m) if used_m else 0,
                                            "tray_info_idx": tray_info_idx,
                                            # Sliced output already pre-filtered by used_g>0,
                                            # so every entry that survives is in fact used by
                                            # this plate. Print-dispatch consumers ignore the
                                            # flag; SliceModal uses it to enable/disable rows.
                                            "used_in_plate": True,
                                        }
                                    )
                            break
                else:
                    # Extract all filaments with used_g > 0 (for single-plate or overview)
                    for filament_elem in root.findall(".//filament"):
                        filament_id = filament_elem.get("id")
                        filament_type = filament_elem.get("type", "")
                        filament_color = filament_elem.get("color", "")
                        used_g = filament_elem.get("used_g", "0")
                        used_m = filament_elem.get("used_m", "0")

                        tray_info_idx = filament_elem.get("tray_info_idx", "")

                        try:
                            used_grams = float(used_g)
                        except (ValueError, TypeError):
                            used_grams = 0

                        if used_grams > 0 and filament_id:
                            filaments.append(
                                {
                                    "slot_id": int(filament_id),
                                    "type": filament_type,
                                    "color": filament_color,
                                    "used_grams": round(used_grams, 1),
                                    "used_meters": float(used_m) if used_m else 0,
                                    "tray_info_idx": tray_info_idx,
                                    "used_in_plate": True,
                                }
                            )

            # Unsliced project files: slice_info had no per-plate data.
            # Return the FULL project_settings.config AMS slot list so
            # the slicer CLI receives a profile for every project slot
            # (otherwise it silently fills the gap from embedded
            # defaults — surfaces as "I picked white but the print has
            # grey" because the source's grey support filament leaks
            # into the output). Use the preview slice to mark which
            # slots the picked plate actually consumes; the SliceModal
            # disables the unused rows so the user only interacts with
            # the dropdowns that matter, while the backend still has
            # the complete list to pass to the CLI.
            if not filaments:
                project_filaments = extract_project_filaments_from_3mf(zf)
                used_slot_ids: set[int] = set()
                if project_filaments and plate_id is not None:
                    preview = await _try_preview_slice_filaments(
                        db,
                        kind="library_file",
                        source_id=file_id,
                        plate_id=plate_id,
                        file_path=file_path,
                        request_id=request_id,
                    )
                    if preview is not None:
                        used_slot_ids = {f["slot_id"] for f in preview}
                # Default to "every slot is used" when preview-slice
                # didn't produce data: better to over-enable dropdowns
                # than under-enable and have the user unable to pick a
                # filament the plate actually uses.
                fallback_all_used = not used_slot_ids
                for f in project_filaments:
                    f["used_in_plate"] = fallback_all_used or f["slot_id"] in used_slot_ids
                filaments = project_filaments

            # Sort by slot ID
            filaments.sort(key=lambda x: x["slot_id"])

            # Enrich with nozzle mapping for dual-nozzle printers
            nozzle_mapping = extract_nozzle_mapping_from_3mf(zf)
            if nozzle_mapping:
                for filament in filaments:
                    filament["nozzle_id"] = nozzle_mapping.get(filament["slot_id"])

    except Exception as e:
        logger.warning("Failed to parse filament requirements from library file %s: %s", file_id, e)

    return {
        "file_id": file_id,
        "filename": lib_file.filename,
        "plate_id": plate_id,
        "filaments": filaments,
    }


_STRIPPABLE_3MF_CONFIGS = frozenset(
    {
        # Settings dump used by --load-settings validation; the CLI tries to
        # match its sentinel values (`prime_tower_brim_width: -1`, empty
        # arrays) against the supplied profile and rejects out-of-range.
        "Metadata/project_settings.config",
        # Per-object settings overrides referencing the source plate's
        # filament IDs / printer IDs. When the user picks a different
        # printer / filament triplet, the IDs no longer resolve and the
        # CLI exits non-zero on input validation.
        "Metadata/model_settings.config",
        # Slicer-version + plate-config + filament-mapping snapshot from
        # the original slice. Includes the original printer model and
        # filament references; mismatches against `--load-settings`
        # consistently surfaced as `Slicer CLI failed (500)` for every
        # 3MF in production. Removing it lets the CLI build a fresh slice
        # plan from the supplied profile triplet.
        "Metadata/slice_info.config",
        # Multi-part / split-mesh metadata referencing object IDs from the
        # original slice. Strip for the same reason — preserves the geometry
        # in `3D/3dmodel.model` while dropping the orphan references.
        "Metadata/cut_information.xml",
    }
)


def _strip_3mf_embedded_settings(zip_bytes: bytes) -> bytes:
    """Remove embedded slicer-config metadata from a 3MF.

    Bambuddy supplies the slicer profile triplet via the sidecar's
    ``--load-settings`` path; the 3MF's embedded settings would otherwise be
    validated by the CLI first and can fail with sentinel-value range
    checks (`prime_tower_brim_width: -1 not in range`, etc.) regardless of
    what we pass via ``--load-settings``. Stripping the embedded configs
    forces the CLI to use the supplied profiles only. Geometry
    (``3D/3dmodel.model``), thumbnails, color, and multi-part data inside
    the 3MF are preserved.

    The set of strippable filenames is centralised in
    ``_STRIPPABLE_3MF_CONFIGS`` — see that constant for the per-file
    rationale. Project-settings alone wasn't enough: real-world Bambu
    Studio 3MFs cross-reference printer / filament IDs from the other
    metadata configs, and any single leftover triggered the validation
    failure that made every profile-driven slice fall back to embedded
    settings.
    """
    from io import BytesIO

    src = BytesIO(zip_bytes)
    dst = BytesIO()
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename in _STRIPPABLE_3MF_CONFIGS:
                continue
            zout.writestr(item, zin.read(item.filename))
    return dst.getvalue()


# Keys in ``Metadata/project_settings.config`` that BambuStudio writes ``"-1"``
# to when the user wants the value inherited from the parent process preset.
# The CLI's ``StaticPrintConfig`` validator runs against the embedded settings
# *before* ``--load-settings`` overrides apply, so a sentinel ``"-1"`` trips
# the field's lower-bound range check and the CLI exits non-zero before our
# profile triplet is ever consulted (#1201 — MakerWorld P2S models).
#
# Allowlisted (rather than "strip every '-1' value") because some fields
# legitimately accept negative numbers (z_offset, translation values, etc.)
# and a blanket strip would silently corrupt those.
#
# Add new entries here as more reports surface — the slicer's error message
# names the offending field directly (`<field>: -1 not in range [...]`).
_PROJECT_SETTINGS_SENTINEL_KEYS = frozenset(
    {
        # Reported in #1201 (MakerWorld P2S 3MFs).
        "raft_first_layer_expansion",
        "tree_support_wall_count",
        # Cited in the strip-experiment comment block above as a known sentinel
        # case from earlier reports.
        "prime_tower_brim_width",
    }
)


def _sanitize_project_settings_sentinels(zip_bytes: bytes) -> bytes:
    """Strip ``"-1"`` inherit-from-parent sentinels from the 3MF's
    ``Metadata/project_settings.config`` so the slicer CLI's range validator
    accepts the file (#1201).

    Removes only allowlisted keys (see ``_PROJECT_SETTINGS_SENTINEL_KEYS``)
    when their value is exactly ``"-1"``. The rest of the config — and every
    other entry in the zip — is preserved byte-for-byte. Unlike the earlier
    full-strip experiment (see ``_strip_3mf_embedded_settings`` and the
    cautionary comment in ``_run_slicer_with_fallback``) this leaves
    ``StaticPrintConfig`` initialisation intact: the file is still present,
    still parses, and the slicer falls back to the supplied
    ``--load-settings`` value for the removed key.

    Returns the original bytes unchanged when no sanitisation is needed
    (input isn't a valid zip, no ``project_settings.config``, no allowlisted
    sentinels present, or any other parse failure) so the caller can pass
    the result on without further checks.
    """
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zin:
            if "Metadata/project_settings.config" not in zin.namelist():
                return zip_bytes
            try:
                config = json.loads(zin.read("Metadata/project_settings.config").decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return zip_bytes
            if not isinstance(config, dict):
                return zip_bytes
            removed = [key for key in _PROJECT_SETTINGS_SENTINEL_KEYS if config.get(key) == "-1"]
            if not removed:
                return zip_bytes
            for key in removed:
                config.pop(key, None)
            patched = json.dumps(config)
            logger.info(
                "3MF sanitiser: removed sentinel '-1' for keys %s — slicer will use --load-settings defaults",
                sorted(removed),
            )
            dst = BytesIO()
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename == "Metadata/project_settings.config":
                        zout.writestr(item, patched)
                    else:
                        zout.writestr(item, zin.read(item.filename))
            return dst.getvalue()
    except (zipfile.BadZipFile, OSError):
        return zip_bytes


def _patch_process_bed_type(process_json: str, bed_type: str) -> str:
    """Overwrite ``curr_bed_type`` in a process-profile JSON before forwarding
    to the slicer sidecar.

    The slicer CLI reads the build-plate type from the process profile's
    ``curr_bed_type`` field. When the user picks a non-default plate in the
    SliceModal (#1337), we patch the resolved JSON in place rather than
    asking them to clone the preset just to switch a plate. Returns the
    original string unchanged when the JSON can't be parsed or isn't a
    dict — the slicer will then run with whatever the preset originally
    specified, which is the safe fall-back path.
    """
    try:
        profile = json.loads(process_json)
    except json.JSONDecodeError:
        logger.warning("Bed-type override skipped: process profile is not valid JSON")
        return process_json
    if not isinstance(profile, dict):
        return process_json
    profile["curr_bed_type"] = bed_type
    return json.dumps(profile)


# Support-related keys we lift from the source 3MF's project_settings.config
# into the picked process preset before `--load-settings` sees it (#1881).
# BambuStudio's shipped process presets ("0.20mm Standard @BBL H2D" etc.)
# define `enable_support: 0` as their default — supports are a per-print
# decision, not a per-quality one. `--load-settings` is authoritative, so
# without preserving these fields the source's per-project support intent
# (supports on, PVA in the interface slot, tree vs normal) gets discarded
# and the slicer produces a single-material output with no supports at all.
_SOURCE_PROCESS_SUPPORT_KEYS_TO_PRESERVE = (
    "enable_support",
    "support_filament",
    "support_interface_filament",
    "support_type",
)


def _patch_process_support_settings(process_json: str, source_3mf_bytes: bytes) -> str:
    """Overlay the source 3MF's support configuration onto the process JSON.

    Only fires on 3MF sources — STL / STEP don't carry `project_settings.
    config`. Silently no-ops when the source doesn't have the config, has
    a malformed one, or when the process JSON isn't parseable — the slice
    then runs with the process preset's own defaults, which is the safe
    fall-back for both this bug and the pre-fix behaviour.
    """
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(source_3mf_bytes), "r") as zf:
            if "Metadata/project_settings.config" not in zf.namelist():
                return process_json
            src_cfg = json.loads(zf.read("Metadata/project_settings.config").decode("utf-8"))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError, KeyError):
        return process_json
    if not isinstance(src_cfg, dict):
        return process_json

    try:
        process_cfg = json.loads(process_json)
    except json.JSONDecodeError:
        return process_json
    if not isinstance(process_cfg, dict):
        return process_json

    for key in _SOURCE_PROCESS_SUPPORT_KEYS_TO_PRESERVE:
        if key in src_cfg:
            process_cfg[key] = src_cfg[key]

    return json.dumps(process_cfg)


# The sidecar prefixes the slicer CLI's own error_string with this when the
# slicer ran and rejected the job (model off the bed, incompatible filament
# temps, range validation) — as opposed to the CLI crashing before it could
# evaluate the job at all.
_SLICER_REJECTION_MARKER = "Slicing failed with error from slicer:"

# The CLI writes its real diagnostic to stdout/stderr on the `[error]` level.
# Format is `[<timestamp>] [error] run <NNNN>: <message>` (or sometimes without
# the `run NNNN:` prefix). The bracketed timestamp is optional; the `[error]`
# tag is what we anchor on. Used to recover the actual rejection reason for
# the `error_string: "The input preset file is invalid and can not be parsed."`
# case (#1851) — the CLI emits that generic placeholder for every -5 exit
# including real preset-compat rejections, and the per-incident specifics
# only live in the stdout dump.
_CLI_ERROR_LINE_RE = re.compile(r"\[error\]\s*(?:run\s+\d+:\s*)?(.+?)\s*$", re.MULTILINE)

# The placeholder error_string Bambu Studio writes to result.json for any
# `--load-settings` parse / compat rejection (-5 exit). When the sidecar
# surfaces this, the real reason lives in the stdout `[error]` line that we
# mine via _CLI_ERROR_LINE_RE.
_INPUT_PRESET_INVALID_PLACEHOLDER = "The input preset file is invalid and can not be parsed."


def _slicer_rejection_message(error_text: str) -> str | None:
    """Extract the slicer's own rejection reason from a sidecar error string,
    or ``None`` when the failure is not a slicer content rejection.

    A content rejection means ``--load-settings`` *was* applied — the slicer
    got far enough to evaluate the model against the chosen printer and say
    no. Retrying with the 3MF's embedded settings would then only "succeed"
    by silently reverting to the source file's original printer, masking the
    real problem; such failures must reach the user instead.

    When the sidecar's `error_string` is Bambu Studio's generic
    "The input preset file is invalid and can not be parsed." placeholder
    (#1851) — emitted for every -5 exit, including the actual preset-compat
    rejections whose real reason is logged to stdout as
    `[error] run NNNN: <diagnostic>` — prefer the stdout `[error]` line so
    the user sees which preset clashed with which printer.
    """
    if _SLICER_REJECTION_MARKER not in error_text:
        return None
    reason = error_text.split(_SLICER_REJECTION_MARKER, 1)[1]
    # Mine the stdout/stderr dump for a more specific CLI diagnostic before
    # we trim it off below. Done first so the lookup window covers the full
    # response, not just the headline.
    cli_diagnostic_match = _CLI_ERROR_LINE_RE.search(reason)
    cli_diagnostic = cli_diagnostic_match.group(1).strip() if cli_diagnostic_match else None
    # Trim the sidecar's trailing exit-code note and any stderr/stdout dump.
    for cut in (": Slicer process failed", "\nstderr:", "\nstdout:"):
        idx = reason.find(cut)
        if idx != -1:
            reason = reason[:idx]
    reason = reason.strip() or None
    # When the headline is Bambu Studio's catch-all placeholder, the real
    # reason is in the stdout `[error]` line. Substitute it. The placeholder
    # by itself tells the user nothing about why their slice was rejected.
    if cli_diagnostic and (reason is None or reason == _INPUT_PRESET_INVALID_PLACEHOLDER):
        return cli_diagnostic
    return reason


async def _run_slicer_with_fallback(
    db: AsyncSession,
    *,
    model_bytes: bytes,
    model_filename: str,
    request: SliceRequest,
    current_user_id: int | None = None,
    job_id: int | None = None,
):
    """Validate presets, dispatch to the right sidecar, run the slicer with
    the auto-fallback for 3MF inputs whose `--load-settings` path crashes the
    CLI. Returns ``(SliceResult, used_embedded_settings: bool)``. Raises
    ``HTTPException`` for any caller-facing error.

    `current_user_id` is needed to resolve **cloud** presets — the cloud token
    is per-user when auth is enabled. For the legacy / local-only path it can
    be left ``None``.

    `job_id`: when set, a request_id is generated and a parallel poller
    pushes the sidecar's --pipe-fed progress events onto
    ``slice_dispatch.set_progress(job_id, ...)`` so the UI's persistent
    toast can show "Generating G-code (75%)" instead of just elapsed
    time. Pass None for synchronous routes that aren't tracked by the
    dispatcher.
    """
    from backend.app.api.routes.settings import get_setting
    from backend.app.services.preset_resolver import resolve_preset_ref
    from backend.app.services.slicer_api import (
        SlicerApiServerError,
        SlicerApiService,
        SlicerApiUnavailableError,
        SlicerInputError,
    )

    user: User | None = None
    presets: dict[str, str] = {}
    filament_jsons: list[str] = []
    # Resolve each slot via the source-aware resolver. The schema
    # validator has already normalised legacy `*_preset_id: int`
    # fields into `PresetRef(source='local', id=str(int))`, so all
    # three are guaranteed non-None here.
    if current_user_id is not None:
        user = await db.get(User, current_user_id)

    refs = {
        "printer": request.printer_preset,
        "process": request.process_preset,
    }
    for slot, ref in refs.items():
        assert ref is not None, "schema validator guarantees PresetRef is set"
        presets[slot] = await resolve_preset_ref(db, user, ref, slot)
    # Multi-color: resolve each filament slot in plate order. The schema
    # validator backfilled `filament_presets` from the legacy `filament_preset`
    # field for single-color callers, so this list is always non-empty.
    for ref in request.filament_presets:
        assert ref is not None, "schema validator guarantees filament list is non-None"
        filament_jsons.append(await resolve_preset_ref(db, user, ref, "filament"))

    # Bed-type override (#1337): patch curr_bed_type onto the resolved
    # process JSON so the slicer's StaticPrintConfig pass picks up the
    # user's pick instead of whatever the process preset defaults to.
    # Without this, slicing an STL of ABS onto a process preset whose
    # default is "Cool Plate" fails with "Plate 1: Cool Plate does not
    # support filament 1" — the reporter's exact scenario.
    if request.bed_type:
        presets["process"] = _patch_process_bed_type(presets["process"], request.bed_type)

    # Slicer routing — pick the sidecar URL by preferred_slicer.
    # The per-install URL setting (Settings UI → Slicer card) wins; an
    # empty value falls back to the SLICER_API_URL / BAMBU_STUDIO_API_URL
    # env defaults defined in core/config.py.
    preferred = (await get_setting(db, "preferred_slicer")) or "bambu_studio"
    if preferred == "orcaslicer":
        configured = await get_setting(db, "orcaslicer_api_url")
        api_url = (configured or app_settings.slicer_api_url).strip()
    elif preferred == "bambu_studio":
        configured = await get_setting(db, "bambu_studio_api_url")
        api_url = (configured or app_settings.bambu_studio_api_url).strip()
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preferred_slicer setting: '{preferred}'. Expected 'orcaslicer' or 'bambu_studio'.",
        )

    # Note: an earlier version of this code stripped Metadata/project_settings.
    # config + model_settings.config + slice_info.config + cut_information.xml
    # before forwarding the 3MF, the theory being that --load-settings would
    # then take precedence cleanly. That theory was wrong: model_settings.
    # config carries the plate definitions the CLI needs to map `--slice N`
    # to a real plate, and slice_info / project_settings supply baseline
    # config the CLI's StaticPrintConfig pass needs at all. Stripping ANY
    # of them caused the CLI to silently exit immediately after
    # "Initializing StaticPrintConfigs" — exit code 0, no result.json, no
    # stderr — which Node's child_process treated as failure and Bambuddy
    # then masked by falling back to slice_without_profiles using the
    # un-stripped bytes (and the source's embedded printer). Net effect:
    # every 3MF slice with profiles silently produced wrong-printer output.
    # Forwarding the original bytes lets --load-settings override the
    # specific fields the user changed (printer/process/filament) while
    # the embedded plate / model definitions remain intact.
    is_3mf = model_filename.lower().endswith(".3mf")
    primary_bytes = model_bytes
    if is_3mf:
        # Strip "-1" inherit-from-parent sentinels from
        # Metadata/project_settings.config so the CLI's StaticPrintConfig
        # range validator accepts the file (#1201). Surgical — keeps the
        # config present, just removes the offending keys; the supplied
        # --load-settings (and the fallback's embedded values for keys we
        # didn't touch) still drive the slice.
        primary_bytes = _sanitize_project_settings_sentinels(primary_bytes)

        # #1881: preserve the source 3MF's support configuration on top of
        # the picked process preset. Bambu's shipped process presets set
        # `enable_support: 0` by default (supports are a per-print, not
        # per-quality, decision); `--load-settings` is authoritative so
        # without patching, the source's `enable_support: 1` + support-slot
        # assignments get discarded and the slice comes out single-material
        # with a PVA slot loaded but never used.
        presets["process"] = _patch_process_support_settings(presets["process"], primary_bytes)

    used_embedded_settings = False
    # "Slice as designed" (#2611): honour the file's embedded
    # project_settings.config instead of the picked profile triplet. Only
    # meaningful for a 3MF that actually carries embedded settings; the UI
    # gates the toggle on the picked printer matching the design's target,
    # so this path never re-targets across printer models.
    embedded_mode = bool(request.use_embedded_settings and is_3mf)
    service = SlicerApiService(api_url)

    # #1493: cross-nozzle-class re-slice (single <-> dual). Without
    # intervention the slicer rejects with either "G-code in unprintable
    # area of multi-extruder printers" (the source's X1C-coordinate layout
    # lands in the H2D's per-nozzle dead zone) or — worse — segfaults
    # inside ZFiller's polygon clipping when the geometry pipeline trips
    # on the cross-class transition. Forwarding the sidecar's --arrange
    # flag for these cases lets BambuStudio reposition objects for the
    # target bed and reconcile the embedded project_settings.config
    # against the new printer, the same way the GUI's "Switch Printer"
    # operation does. --arrange WILL reposition objects, so we only
    # enable it on a true class crossing — same-printer slices keep the
    # user's deliberate layout. The bed-type and arrange flags are
    # orthogonal so this decision doesn't interact with the #1337 build-
    # plate override.
    cross_class_arrange = False
    if is_3mf:
        from backend.app.services.slicer_3mf_convert import (
            extract_source_printer_model,
        )
        from backend.app.utils.printer_models import is_dual_nozzle_model

        source_model = extract_source_printer_model(primary_bytes)
        target_model = await _resolve_target_printer_model(db, user, request)
        if source_model and target_model and is_dual_nozzle_model(source_model) != is_dual_nozzle_model(target_model):
            logger.info(
                "Cross-nozzle-class re-slice (%s -> %s): enabling --arrange so BS reconciles "
                "the embedded project layout against the target printer",
                source_model,
                target_model,
            )
            cross_class_arrange = True
    # When this slice is dispatcher-tracked, generate a request_id so
    # the sidecar publishes progress under it, and wire a callback that
    # forwards each frame onto SliceDispatchService.set_progress for the
    # status-poll endpoint to surface to the UI.
    progress_request_id: str | None = None
    progress_callback = None
    if job_id is not None:
        from uuid import uuid4

        from backend.app.services.slice_dispatch import slice_dispatch as _dispatch

        progress_request_id = str(uuid4())

        def _on_progress(snapshot: dict) -> None:
            _dispatch.set_progress(job_id, snapshot)

        progress_callback = _on_progress
    # SliceModal lets the user pick a filament profile per slot, but each
    # plate uses only a subset of the slots. The unused-slot dropdowns get
    # whatever default the modal serves up — and a heterogeneous default
    # (e.g. ABS in slot 2 next to a PLA in the used slot 1) makes
    # BambuStudio reject the slice with "the temperature difference of
    # the filaments used is too large" (exit 194) even though the G-code
    # never touches the unused slot; a default scoped to another printer
    # gets it rejected with "filament preset (slot N) is not compatible
    # with printer …" (#2628). Replace unused-slot entries with the
    # plate's lowest used slot before the real slice so the loaded set is
    # materially homogeneous and printer-correct.
    if is_3mf and request.plate is not None:
        from backend.app.services.slicer_3mf_convert import substitute_unused_plate_filaments

        filament_jsons = substitute_unused_plate_filaments(primary_bytes, request.plate, filament_jsons)

    # Cross-class slice-all loop (#1493): when the user asks for
    # ``plate=0`` (all plates) AND the source's nozzle class differs from
    # the target's, ``--slice 0 --arrange 1`` consolidates every plate's
    # objects onto a single target bed (BS's ``--arrange`` is project-
    # wide) — either packing them all together or rejecting with "Some
    # objects are located over the boundary of the heated bed" when
    # nothing fits. Slice each plate independently with ``--arrange 1``
    # and merge the per-plate outputs into one multi-plate 3MF instead.
    # Same-class slice-all goes through the regular path below — the
    # sidecar's native ``--slice 0`` produces the right shape directly.
    use_cross_class_slice_all = cross_class_arrange and request.plate == 0 and request.export_3mf

    try:
        try:
            if embedded_mode:
                # No --load-settings: feed the CLI the file's own
                # project_settings.config untouched so the designer's tweaks
                # (walls, infill, etc.) drive the slice. primary_bytes is
                # already sentinel-sanitised above, the same bytes the
                # crash-fallback uses. The resolved presets go unused here.
                result = await service.slice_without_profiles(
                    model_bytes=primary_bytes,
                    model_filename=model_filename,
                    plate=request.plate,
                    export_3mf=request.export_3mf,
                    request_id=progress_request_id,
                    on_progress=progress_callback,
                )
                used_embedded_settings = True
            elif use_cross_class_slice_all:
                from backend.app.services.slicer_3mf_convert import (
                    count_plates_in_3mf,
                    merge_plate_3mfs,
                )

                plate_count = count_plates_in_3mf(primary_bytes)
                if plate_count == 0:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Couldn't read plate count from the source 3MF for cross-class "
                            "slice-all. The source may be malformed or missing "
                            "Metadata/model_settings.config."
                        ),
                    )
                logger.info(
                    "Cross-class slice-all: looping over %d plates with --arrange per plate, then merging",
                    plate_count,
                )
                from backend.app.services.slicer_api import SliceResult

                per_plate_results: list[tuple[int, SliceResult]] = []

                # Forward the same progress request_id + callback to each
                # per-plate sub-call so the toast keeps showing the
                # sidecar's stage messages ("Generating G-code 45%…").
                # The sub-calls run sequentially, so the poller for plate
                # N is cancelled before plate N+1's poller starts — no
                # cross-talk between plate streams. Wrap the callback to
                # surface "(plate N/M)" alongside the slicer's stage
                # message so the user sees progress through the whole
                # multi-plate loop, not just one plate at a time.
                def _wrap_progress_for_plate(plate_num: int, total: int):
                    if progress_callback is None:
                        return None

                    def _cb(snapshot: dict) -> None:
                        snapshot = dict(snapshot)
                        snapshot["multi_plate_index"] = plate_num
                        snapshot["multi_plate_count"] = total
                        progress_callback(snapshot)

                    return _cb

                for plate_num in range(1, plate_count + 1):
                    plate_cb = _wrap_progress_for_plate(plate_num, plate_count)
                    per_plate = await service.slice_with_profiles(
                        model_bytes=primary_bytes,
                        model_filename=model_filename,
                        printer_profile_json=presets["printer"],
                        process_profile_json=presets["process"],
                        filament_profile_jsons=filament_jsons,
                        plate=plate_num,
                        export_3mf=True,
                        arrange=True,
                        request_id=progress_request_id,
                        on_progress=plate_cb,
                    )
                    per_plate_results.append((plate_num, per_plate))

                # Merge the N single-plate 3MFs into one multi-plate 3MF.
                # ``primary_bytes`` is the source 3MF: it carries the
                # original per-plate previews the slicer's --arrange
                # pass doesn't regenerate, so the merger can fall back
                # to those for each plate's cover image.
                merged_bytes = merge_plate_3mfs(
                    [(n, r.content) for n, r in per_plate_results],
                    source_3mf_bytes=primary_bytes,
                )
                # Synthetic SliceResult: totals are the sum of each
                # plate's so the archive card shows the project's print
                # time and filament use, not just plate 1's.
                result = SliceResult(
                    content=merged_bytes,
                    print_time_seconds=sum(r.print_time_seconds for _, r in per_plate_results),
                    filament_used_g=sum(r.filament_used_g for _, r in per_plate_results),
                    filament_used_mm=sum(r.filament_used_mm for _, r in per_plate_results),
                )
            else:
                result = await service.slice_with_profiles(
                    model_bytes=primary_bytes,
                    model_filename=model_filename,
                    printer_profile_json=presets["printer"],
                    process_profile_json=presets["process"],
                    filament_profile_jsons=filament_jsons,
                    plate=request.plate,
                    export_3mf=request.export_3mf,
                    arrange=cross_class_arrange,
                    request_id=progress_request_id,
                    on_progress=progress_callback,
                )
        except SlicerApiServerError as exc:
            rejection = _slicer_rejection_message(str(exc))
            if rejection:
                # The slicer ran and rejected the job for a content reason —
                # the chosen printer/process/filament *were* applied. Falling
                # back to embedded settings would silently re-slice for the
                # source 3MF's original printer and hide the real problem
                # (e.g. re-slicing an H2D model for an X1C: the object is off
                # the smaller bed). Surface the slicer's reason instead.
                raise HTTPException(status_code=400, detail=rejection) from exc
            if not is_3mf or embedded_mode:
                # embedded_mode already sliced with the file's own settings —
                # there is nothing to fall back TO, so surface the server
                # error (the outer handler turns it into a 502) instead of
                # re-running the same embedded slice.
                raise
            logger.warning(
                "Slicer CLI failed on the --load-settings path for %s (%s); retrying with embedded settings",
                model_filename,
                exc,
            )
            # Forward the same request_id + callback so the toast's live
            # progress keeps updating across the fallback retry instead
            # of going blank for the rest of the slice. Use the sanitised
            # bytes — the embedded-settings path also reads the same
            # project_settings.config and the same range validator runs
            # there too, so without sanitisation the fallback would die
            # on the same sentinel error (#1201). The SliceModal flags
            # the difference to the user via used_embedded_settings.
            result = await service.slice_without_profiles(
                model_bytes=primary_bytes,
                model_filename=model_filename,
                plate=request.plate,
                export_3mf=request.export_3mf,
                request_id=progress_request_id,
                on_progress=progress_callback,
            )
            used_embedded_settings = True
    except SlicerInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SlicerApiServerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except SlicerApiUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await service.close()

    return result, used_embedded_settings


def _canonical_printer_model(raw: str | None) -> str | None:
    """Normalise a printer-preset name / ``printer_model`` field to a canonical
    model code. Strips the BambuStudio ``"# "`` user-clone prefix and the
    ``" 0.4 nozzle"`` variant suffix that preset names carry but bare model
    names don't — without this, ``"Bambu Lab H2D 0.4 nozzle"`` wouldn't
    normalise to ``H2D``."""
    import re

    from backend.app.utils.printer_models import normalize_printer_model

    if not raw:
        return None
    cleaned = str(raw).strip()
    if cleaned.startswith("# "):
        cleaned = cleaned[2:].strip()
    cleaned = re.sub(r"\s+0\.\d+\s+nozzle$", "", cleaned, flags=re.IGNORECASE)
    return normalize_printer_model(cleaned) if cleaned else None


async def _resolve_target_printer_model(db: AsyncSession, user: User | None, request: SliceRequest) -> str | None:
    """Best-effort: the printer model a slice request targets.

    Returns ``None`` when it can't be determined (the nozzle-class guard
    then simply doesn't fire — fail-open, never blocks a slice spuriously).
    """
    from backend.app.services.preset_resolver import resolve_preset_ref

    if request.printer_preset is None:
        return None
    try:
        printer_json = await resolve_preset_ref(db, user, request.printer_preset, "printer")
        data = json.loads(printer_json)
        if not isinstance(data, dict):
            return None
        return _canonical_printer_model(
            data.get("printer_model") or data.get("printer_settings_id") or data.get("name")
        )
    except Exception:
        return None


async def guard_nozzle_class_reslice(
    db: AsyncSession, user: User | None, request: SliceRequest, source_model: str | None
) -> None:
    """No-op guard, retained for call-site compatibility.

    Cross-nozzle-class re-slicing is handled by ``_run_slicer_with_fallback``'s
    two-pass conversion (#1493): a 1mm cube is sliced with the target triplet
    via ``slice_with_profiles`` to produce a fresh target-shaped
    ``Metadata/project_settings.config``, which is then spliced into the
    source 3MF before the real slice. So this guard never needs to block
    anymore.

    The function and its call sites in ``archives.py`` / the library re-slice
    route are kept so external pinned-version forks and downstream patches
    don't break, but it does nothing on a successful slice path. If the
    two-pass conversion fails inside the slicer, the existing
    ``SlicerApiServerError`` / ``_slicer_rejection_message`` plumbing
    surfaces the CLI's actual error to the user — which is more informative
    than the old "isn't supported yet" 400 the guard used to raise.
    """
    return None


async def slice_and_persist(
    db: AsyncSession,
    *,
    model_bytes: bytes,
    model_filename: str,
    folder_id: int | None,
    extra_metadata: dict | None,
    request: SliceRequest,
    current_user_id: int | None,
    job_id: int | None = None,
) -> SliceResponse:
    """Slice a model and save the result as a new ``LibraryFile`` in
    ``folder_id`` (same folder as the source by convention).

    Always exports as ``.gcode.3mf`` so the existing library thumbnail
    pipeline works on the new file. Plain ``.gcode`` would have no
    embedded thumbnail to extract.
    """
    from backend.app.services.archive import ThreeMFParser

    library_request = request.model_copy(update={"export_3mf": True})

    result, used_embedded_settings = await _run_slicer_with_fallback(
        db,
        model_bytes=model_bytes,
        model_filename=model_filename,
        request=library_request,
        current_user_id=current_user_id,
        job_id=job_id,
    )

    base_name = model_filename.rsplit(".", 1)[0]
    out_filename = f"{base_name}.gcode.3mf"
    unique_name = f"{uuid.uuid4().hex}.gcode.3mf"
    out_path = get_library_files_dir() / unique_name  # SEC-PATH-OK: unique_name = uuid.uuid4().hex + ".gcode.3mf"
    # BS/Orca CLIs skip plate_N.png in headless --export-3mf — render +
    # inject server-side so the library card has a thumbnail. Best-effort:
    # no-op when the slicer did embed thumbs (desktop Studio path), and
    # falls through to the unmodified bytes on any render error.
    result = result._replace(content=inject_plate_thumbnails_if_missing(result.content))
    out_path.write_bytes(result.content)

    # Extract thumbnail from the produced 3MF so the library card shows a
    # preview. Failures here aren't fatal — the file is still useful
    # without a thumbnail.
    thumbnail_relative: str | None = None
    parsed_metadata: dict = {}
    try:
        parser = ThreeMFParser(str(out_path))
        parsed = parser.parse()
        thumb_data = parsed.get("_thumbnail_data")
        thumb_ext = parsed.get("_thumbnail_ext", ".png")
        if thumb_data:
            thumb_filename = f"{uuid.uuid4().hex}{thumb_ext}"
            thumb_path = get_library_thumbnails_dir() / thumb_filename
            thumb_path.write_bytes(thumb_data)
            thumbnail_relative = to_relative_path(thumb_path)
        cleaned = _clean_3mf_metadata(parsed)
        if isinstance(cleaned, dict):
            parsed_metadata = cleaned
    except Exception as exc:
        logger.warning("Failed to parse sliced 3MF metadata for %s: %s", out_filename, exc)

    # Drop the embedded `print_name` (see _without_print_name) so the sliced
    # row's display falls back to its ".gcode.3mf" filename instead of the
    # source file's project title, which would make the two indistinguishable.
    metadata: dict = dict(_without_print_name(parsed_metadata) or {})
    # Some slicer-sidecar builds leave the X-Filament-Used-* response headers
    # unset, so result.filament_used_g/_mm arrive as 0 even for a real
    # multi-hour print. Fall back to the totals ThreeMFParser read from the
    # produced 3MF's own G-code header.
    filament_g = result.filament_used_g or parsed_metadata.get("filament_used_grams") or 0.0
    filament_mm = result.filament_used_mm or parsed_metadata.get("filament_used_mm") or 0.0
    metadata.update(
        {
            "print_time_seconds": result.print_time_seconds,
            "filament_used_g": filament_g,
            "filament_used_mm": filament_mm,
        }
    )
    if used_embedded_settings:
        metadata["used_embedded_settings"] = True
    if extra_metadata:
        metadata.update(extra_metadata)

    new_file = LibraryFile(
        folder_id=folder_id,
        filename=out_filename,
        file_path=to_relative_path(out_path),
        # The on-disk payload is a ZIP container — the file_type must
        # record that so the preview endpoint opens it as a 3MF instead
        # of returning the ZIP bytes as text/plain (#1709 / yanglei1980).
        # Earlier code mis-typed sliced rows as "gcode" to share the
        # plain-G-code badge; that broke the embedded viewer. UI badges
        # and gates for "gcode.3mf" are explicit at the call sites.
        file_type="gcode.3mf",
        file_size=len(result.content),
        file_hash=hashlib.sha256(result.content).hexdigest(),
        thumbnail_path=thumbnail_relative,
        file_metadata=metadata,
        source_type="sliced",
        created_by_id=current_user_id,
    )
    db.add(new_file)
    await db.commit()
    # No refresh: expire_on_commit=False keeps id/filename accessible, and
    # refreshing here flakes under pytest-xdist when teardown of a sibling
    # test races the SELECT.

    return SliceResponse(
        library_file_id=new_file.id,
        name=new_file.filename,
        print_time_seconds=result.print_time_seconds,
        filament_used_g=filament_g,
        filament_used_mm=filament_mm,
        used_embedded_settings=used_embedded_settings,
    )


async def slice_and_persist_as_archive(
    db: AsyncSession,
    *,
    model_bytes: bytes,
    model_filename: str,
    request: SliceRequest,
    source_archive,  # PrintArchive — hint kept loose to avoid cyclic import
    current_user_id: int | None,
    job_id: int | None = None,
):
    """Slice a model and save the result as a new ``PrintArchive`` row,
    inheriting printer / project / makerworld metadata from the source
    archive. Always exports as a `.gcode.3mf` so the existing thumbnail
    and plates infrastructure (which expects a zip-shaped 3MF) works on
    the new archive. Returns ``SliceArchiveResponse``.
    """
    from backend.app.models.archive import PrintArchive
    from backend.app.schemas.slicer import SliceArchiveResponse
    from backend.app.services.archive import ThreeMFParser

    # Archive sinks always want a 3MF. The library route still respects the
    # caller's `export_3mf` flag; here we override.
    archive_request = request.model_copy(update={"export_3mf": True})

    result, used_embedded_settings = await _run_slicer_with_fallback(
        db,
        model_bytes=model_bytes,
        model_filename=model_filename,
        request=archive_request,
        job_id=job_id,
        current_user_id=current_user_id,
    )

    base_name = model_filename.rsplit(".", 1)[0]
    out_filename = f"{base_name}.gcode.3mf"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    printer_folder = str(source_archive.printer_id) if source_archive.printer_id is not None else "unassigned"
    archive_subdir = f"{timestamp}_{base_name}_sliced"
    archive_dir = (
        app_settings.archive_dir / printer_folder / archive_subdir
    )  # SEC-PATH-OK: printer_folder = str(int|None), archive_subdir = f"{timestamp}_{base_name}_sliced" where base_name went through _safe_filename
    archive_dir.mkdir(parents=True, exist_ok=True)
    out_path = (
        archive_dir / out_filename
    )  # SEC-PATH-OK: out_filename = f"{base_name}.gcode.3mf" where base_name went through _safe_filename
    # See library-slice path: BS/Orca sidecar CLIs don't embed plate_N.png
    # in headless --export-3mf, so the produced 3MF often has no thumbnail
    # at all. Server-side render fills the gap; no-op when the slicer did
    # embed (desktop Studio path) and best-effort on any render error.
    result = result._replace(content=inject_plate_thumbnails_if_missing(result.content))
    out_path.write_bytes(result.content)

    # Extract a thumbnail for the new archive card. Priority order:
    #   1. Source archive's ``Metadata/plate_{N}.png`` — the GUI-rendered
    #      preview of the same plate the user is re-slicing. Closer to
    #      "what's actually printing" than any other available image
    #      (with --arrange the layout may differ slightly, but objects
    #      and colours match).
    #   2. ``ThreeMFParser`` fallback chain on the sliced output: the
    #      slicer's own per-plate render if it wrote one, then the
    #      project-wide thumbnail under ``Auxiliaries/.thumbnails/``.
    # BambuStudio CLI frequently doesn't emit a fresh per-plate render
    # (slice writes the new gcode but leaves the preview slot empty),
    # so without (1) the card falls all the way through to the
    # MakerWorld-style cover art — visually unrelated to what the user
    # picked, see #1493 follow-up. Failures don't fail the slice — the
    # archive row is still useful without a thumbnail.
    plate_num = request.plate or 1
    thumbnail_path: str | None = None
    parsed_metadata: dict = {}

    src_3mf_path = app_settings.base_dir / source_archive.file_path
    source_plate_bytes = _read_3mf_entry(src_3mf_path, f"Metadata/plate_{plate_num}.png")
    if source_plate_bytes:
        thumb_dest = archive_dir / "thumbnail.png"
        thumb_dest.write_bytes(source_plate_bytes)
        thumbnail_path = str(thumb_dest.relative_to(app_settings.base_dir))

    try:
        parser = ThreeMFParser(str(out_path), plate_number=plate_num)
        parsed = parser.parse()
        if thumbnail_path is None:
            thumb_data = parsed.get("_thumbnail_data")
            thumb_ext = parsed.get("_thumbnail_ext", ".png")
            if thumb_data:
                thumb_dest = archive_dir / f"thumbnail{thumb_ext}"
                thumb_dest.write_bytes(thumb_data)
                thumbnail_path = str(thumb_dest.relative_to(app_settings.base_dir))
        parsed_metadata = {k: v for k, v in parsed.items() if not k.startswith("_")}
    except Exception as exc:
        logger.warning("Failed to parse sliced 3MF metadata for %s: %s", out_filename, exc)

    metadata = dict(source_archive.extra_data) if source_archive.extra_data else {}
    metadata.update(parsed_metadata)
    # Fall back to the produced 3MF's G-code-header totals when the sidecar
    # leaves the X-Filament-Used-* headers unset (result.filament_used_g == 0
    # even for a real multi-hour print).
    filament_g = result.filament_used_g or parsed_metadata.get("filament_used_grams") or 0.0
    filament_mm = result.filament_used_mm or parsed_metadata.get("filament_used_mm") or 0.0
    metadata.update(
        {
            "sliced_from_archive_id": source_archive.id,
            "print_time_seconds": result.print_time_seconds,
            "filament_used_g": filament_g,
            "filament_used_mm": filament_mm,
        }
    )
    if used_embedded_settings:
        metadata["used_embedded_settings"] = True

    # Prefer the actually-used filament list from the sliced output's
    # slice_info.config (parsed_metadata.filament_* — only entries with
    # used_g > 0). Falling back to the source_archive's list would
    # surface every project-wide AMS slot, including ones the picked
    # plate doesn't use (16+ swatches on the card for a 2-color print).
    new_filament_type = parsed_metadata.get("filament_type") or source_archive.filament_type
    new_filament_color = parsed_metadata.get("filament_color") or source_archive.filament_color

    # When the user re-slices for a different printer model than the source,
    # the source's printer_id (e.g. an H2D's "Workshop H2C") no longer
    # represents where the new archive can be reprinted. The archive card
    # and reprint modal both read printer_id first and only fall back to
    # sliced_for_model when it's None, so leaving the inherited id makes
    # the X1C-sliced card display the source H2D's printer name.
    # Same pitfall as the sliced_for_model copy a few lines below.
    new_target_model = parsed_metadata.get("sliced_for_model") or source_archive.sliced_for_model
    is_cross_model_reslice = (
        new_target_model is not None
        and source_archive.sliced_for_model is not None
        and new_target_model != source_archive.sliced_for_model
    )
    new_printer_id = None if is_cross_model_reslice else source_archive.printer_id

    new_archive = PrintArchive(
        printer_id=new_printer_id,
        project_id=source_archive.project_id,
        filename=out_filename,
        file_path=str(out_path.relative_to(app_settings.base_dir)),
        file_size=len(result.content),
        content_hash=hashlib.sha256(result.content).hexdigest(),
        thumbnail_path=thumbnail_path,
        # Inherit identity from the source archive so the new entry shows
        # up alongside its sibling in the archives list.
        print_name=(source_archive.print_name or base_name) + " (re-sliced)",
        print_time_seconds=result.print_time_seconds,
        filament_used_grams=filament_g or None,
        filament_type=new_filament_type,
        filament_color=new_filament_color,
        layer_height=source_archive.layer_height,
        nozzle_diameter=source_archive.nozzle_diameter,
        # The re-sliced output is for whatever printer the user just picked,
        # not the source archive's printer — read the model the slicer baked
        # into the new 3MF, falling back to the source only if it's absent.
        # (Copying source_archive.sliced_for_model kept a cross-printer
        # re-slice, e.g. X1C→H2D, showing the old "X1C sliced" model.)
        sliced_for_model=parsed_metadata.get("sliced_for_model") or source_archive.sliced_for_model,
        # Build plate type that the sliced output was produced for (#1493
        # follow-up): the frontend's ArchiveCard reads ``archive.bed_type``
        # off the top-level column, not extra_data, so without this lift the
        # re-sliced card had no plate badge. ThreeMFParser pulls it from the
        # sliced 3MF's ``slice_info.config`` ``curr_bed_type``; if that's
        # absent (older sidecar / older slice profile) the source archive's
        # bed_type is the right default.
        bed_type=parsed_metadata.get("bed_type") or source_archive.bed_type,
        makerworld_url=source_archive.makerworld_url,
        designer=source_archive.designer,
        # Sliced-but-not-printed: keep status default ("completed") so it
        # surfaces in the normal archives list, but do not stamp
        # started/completed_at — the user hasn't actually printed it yet.
        extra_data=metadata,
        created_by_id=current_user_id,
    )
    db.add(new_archive)
    await db.commit()
    await db.refresh(new_archive)

    return SliceArchiveResponse(
        archive_id=new_archive.id,
        name=new_archive.print_name or out_filename,
        print_time_seconds=result.print_time_seconds,
        filament_used_g=filament_g,
        filament_used_mm=filament_mm,
        used_embedded_settings=used_embedded_settings,
    )


@router.post("/files/{file_id}/slice", status_code=202)
async def slice_library_file(
    file_id: int,
    request: SliceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
):
    """Enqueue a slice job for a library file. Returns 202 + job_id; the
    slice runs in the background, the caller polls `GET /slice-jobs/{id}`.
    """
    from backend.app.core.database import async_session
    from backend.app.services.slice_dispatch import (
        http_exception_to_job_error,
        slice_dispatch,
    )

    src_result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    lib_file = src_result.scalar_one_or_none()
    if not lib_file:
        raise HTTPException(status_code=404, detail="File not found")

    src_lower = (lib_file.filename or "").lower()
    if not (
        src_lower.endswith(".stl")
        or src_lower.endswith(".3mf")
        or src_lower.endswith(".step")
        or src_lower.endswith(".stp")
    ):
        raise HTTPException(status_code=400, detail="Source file must be STL, 3MF, or STEP")

    src_path = Path(app_settings.base_dir) / lib_file.file_path
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Source file missing on disk")

    # Capture inputs the bg task needs — the request DB session is closed
    # before the background task runs.
    model_bytes = src_path.read_bytes()
    folder_id = lib_file.folder_id
    source_lib_file_id = lib_file.id
    # API-keyed callers get None from the auth gate (auth.py keeps that
    # behaviour to avoid a wider scope expansion). Fall back to the API
    # key's owner so cloud-preset resolution can read the stored
    # cloud_token (#1182 follow-up).
    cloud_token_user = current_user or api_key_cloud_owner
    user_id = cloud_token_user.id if cloud_token_user else None

    # If the source has a `print_name` in its metadata (BambuStudio always
    # sets this; OrcaSlicer often leaves it blank), derive the sliced
    # output's filename from it instead of the raw filename. The source
    # row's display already prefers print_name, so the sliced row's
    # filename ("Piggo the piggy bank.gcode.3mf") will match the source's
    # display name ("Piggo the piggy bank") with the gcode extension added.
    src_print_name = None
    if lib_file.file_metadata:
        candidate = lib_file.file_metadata.get("print_name")
        if isinstance(candidate, str) and candidate.strip():
            src_print_name = candidate.strip()
    src_ext = Path(lib_file.filename).suffix.lower() or ".3mf"
    model_filename = f"{src_print_name}{src_ext}" if src_print_name else lib_file.filename

    # Block a cross-nozzle-class re-slice (single-nozzle <-> H2D) up front.
    # Fires only when the source is itself a sliced file (carries
    # sliced_for_model); a plain un-sliced model has no source nozzle class.
    await guard_nozzle_class_reslice(
        db,
        cloud_token_user,
        request,
        (lib_file.file_metadata or {}).get("sliced_for_model"),
    )

    async def _run(job_id: int):
        async with async_session() as task_db:
            try:
                response = await slice_and_persist(
                    task_db,
                    model_bytes=model_bytes,
                    model_filename=model_filename,
                    folder_id=folder_id,
                    extra_metadata={"sliced_from_library_file_id": source_lib_file_id},
                    request=request,
                    current_user_id=user_id,
                    job_id=job_id,
                )
            except HTTPException as exc:
                raise http_exception_to_job_error(exc) from exc
        return response.model_dump()

    job = await slice_dispatch.enqueue(
        kind="library_file",
        source_id=lib_file.id,
        source_name=lib_file.filename,
        run=_run,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "status_url": f"/api/v1/slice-jobs/{job.id}",
    }


@router.post("/files/{file_id}/print")
async def print_library_file(
    file_id: int,
    printer_id: int,
    # SECURITY.md SEC-AUTH-1: every route either has an explicit auth dep or
    # is in the route-auth-coverage allowlist. Gating the deprecation stub on
    # QUEUE_CREATE matches the replacement route (POST /queue/) and means
    # anonymous callers bounce at auth instead of seeing the deprecation
    # message.
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.QUEUE_CREATE)),
):
    """Legacy direct library print endpoint. Use POST /queue/ instead."""
    logger.warning(
        "Gone API used: POST /library/files/%s/print?printer_id=%s; use POST /queue/ instead",
        file_id,
        printer_id,
    )
    raise HTTPException(
        status_code=410,
        detail="Direct library-file print has been removed. Create a print queue item with POST /queue/.",
    )


# ============ File Detail Endpoints ============


@router.get("/files/{file_id}", response_model=FileResponseSchema)
async def get_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get a file by ID with full details."""
    user, can_read_all = auth_result
    result = await db.execute(
        LibraryFile.active().options(selectinload(LibraryFile.created_by)).where(LibraryFile.id == file_id)
    )
    file = _ensure_library_file_visible(result.scalar_one_or_none(), user, can_read_all)

    # Get folder name
    folder_name = None
    if file.folder_id:
        folder_result = await db.execute(select(LibraryFolder.name).where(LibraryFolder.id == file.folder_id))
        folder_name = folder_result.scalar()

    # Get project name
    project_name = None
    if file.project_id:
        project_result = await db.execute(select(Project.name).where(Project.id == file.project_id))
        project_name = project_result.scalar()

    # Get duplicates
    duplicates = []
    duplicate_count = 0
    if file.file_hash:
        dup_result = await db.execute(
            select(LibraryFile, LibraryFolder.name)
            .outerjoin(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .where(
                LibraryFile.file_hash == file.file_hash,
                LibraryFile.id != file.id,
                LibraryFile.deleted_at.is_(None),
            )
        )
        for dup_file, dup_folder_name in dup_result.all():
            duplicates.append(
                FileDuplicate(
                    id=dup_file.id,
                    filename=dup_file.filename,
                    folder_id=dup_file.folder_id,
                    folder_name=dup_folder_name,
                    created_at=dup_file.created_at,
                )
            )
        duplicate_count = len(duplicates)

    # Extract key metadata fields
    print_name = None
    print_time = None
    filament_grams = None
    sliced_for_model = None
    if file.file_metadata:
        print_name = file.file_metadata.get("print_name")
        print_time = file.file_metadata.get("print_time_seconds")
        filament_grams = file.file_metadata.get("filament_used_grams")
        sliced_for_model = file.file_metadata.get("sliced_for_model")

    return FileResponseSchema(
        id=file.id,
        folder_id=file.folder_id,
        folder_name=folder_name,
        project_id=file.project_id,
        project_name=project_name,
        filename=file.filename,
        file_path=file.file_path,
        file_type=file.file_type,
        file_size=file.file_size,
        file_hash=file.file_hash,
        thumbnail_path=file.thumbnail_path,
        metadata=file.file_metadata,
        print_count=file.print_count,
        last_printed_at=file.last_printed_at,
        notes=file.notes,
        duplicates=duplicates if duplicates else None,
        duplicate_count=duplicate_count,
        created_by_id=file.created_by_id,
        created_by_username=file.created_by.username if file.created_by else None,
        created_at=file.created_at,
        updated_at=file.updated_at,
        print_name=print_name,
        print_time_seconds=print_time,
        filament_used_grams=filament_grams,
        sliced_for_model=sliced_for_model,
    )


@router.put("/files/{file_id}", response_model=FileResponseSchema)
async def update_file(
    file_id: int,
    data: FileUpdate,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
):
    """Update a file's metadata."""
    user, can_modify_all = auth_result

    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # Ownership check
    if not can_modify_all:
        if file.created_by_id != user.id:
            raise HTTPException(status_code=403, detail="You can only update your own files")

    if data.filename is not None:
        # Bambu printer SD cards are FAT32/exFAT; reject the same set Bambu
        # Studio refuses on save so we fail here with a clear message
        # instead of an obscure FTP 553 at print time (#1540).
        try:
            validate_print_filename(data.filename)
        except InvalidFilenameError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        file.filename = data.filename
        # No print_name to keep in sync — library files display by filename,
        # and _without_print_name strips the embedded 3MF Title on import (#1489).

    if data.folder_id is not None:
        if data.folder_id == 0:
            file.folder_id = None
        else:
            # Verify folder exists
            folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == data.folder_id))
            if not folder_result.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Folder not found")
            file.folder_id = data.folder_id

    if data.project_id is not None:
        if data.project_id == 0:
            file.project_id = None
        else:
            # Verify project exists
            project_result = await db.execute(select(Project).where(Project.id == data.project_id))
            if not project_result.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Project not found")
            file.project_id = data.project_id

    if data.notes is not None:
        file.notes = data.notes if data.notes else None

    await db.commit()
    await db.refresh(file)

    # Return full response. Bypass get_file's ownership gate — caller already
    # passed update_file's ownership gate above, so we re-fetch + serialise
    # directly instead of calling the route function (which would try to
    # evaluate its own Depends() at call time and trip a TypeError).
    return await get_file(file_id, db, auth_result=(None, True))


@router.delete("/files/{file_id}")
async def delete_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_DELETE_ALL,
            Permission.LIBRARY_DELETE_OWN,
        )
    ),
):
    """Move a file to the trash (soft-delete).

    The file's bytes and thumbnail stay on disk until the trash sweeper
    hard-deletes the row after the retention window (see #1008). External
    files skip the trash entirely — they can't be restored from disk and the
    underlying file is outside Bambuddy's control, so we just drop the DB
    record and thumbnail.
    """
    user, can_modify_all = auth_result

    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # Ownership check
    if not can_modify_all:
        if file.created_by_id != user.id:
            raise HTTPException(status_code=403, detail="You can only delete your own files")

    if file.is_external:
        # External files bypass the trash — just drop the DB row + our thumbnail.
        abs_thumb_path = to_absolute_path(file.thumbnail_path)
        if abs_thumb_path and abs_thumb_path.exists():
            try:
                abs_thumb_path.unlink()
            except OSError as e:
                logger.warning("Failed to delete thumbnail from disk: %s", e)
        await db.delete(file)
        await db.commit()
        return {"status": "success", "message": "File deleted", "trashed": False}

    # Managed file: soft-delete. Sweeper removes bytes + thumbnail after retention.
    file.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "success", "message": "File moved to trash", "trashed": True}


# ============ File Content Endpoints ============


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Download a file."""
    user, can_read_all = auth_result
    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    file = _ensure_library_file_visible(result.scalar_one_or_none(), user, can_read_all)

    abs_path = to_absolute_path(file.file_path)
    if not abs_path or not abs_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FastAPIFileResponse(
        str(abs_path),
        filename=file.filename,
        media_type="application/octet-stream",
    )


@router.post("/files/{file_id}/slicer-token")
async def create_library_slicer_token(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Create a short-lived download token for opening files in slicer applications.

    Slicer protocol handlers (bambustudioopen://, orcaslicer://) cannot send
    auth headers, so they use this token in the URL path instead.
    """
    from backend.app.core.auth import create_slicer_download_token

    user, can_read_all = auth_result
    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    _ensure_library_file_visible(result.scalar_one_or_none(), user, can_read_all)

    token = await create_slicer_download_token("library", file_id)
    return {"token": token}


@router.get("/files/{file_id}/dl/{token}/{filename}")
async def download_library_file_for_slicer(
    file_id: int,
    token: str,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Download a library file using a slicer download token.

    Token-authenticated (no auth headers needed). The token is short-lived
    and single-use, created by POST /files/{file_id}/slicer-token.
    Filename is at the end of the URL so slicers can detect the file format.
    """
    from backend.app.core.auth import verify_slicer_download_token

    if not await verify_slicer_download_token(token, "library", file_id):
        raise HTTPException(status_code=403, detail="Invalid or expired download token")

    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    abs_path = to_absolute_path(file.file_path)
    if not abs_path or not abs_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FastAPIFileResponse(
        str(abs_path),
        filename=file.filename,
        media_type="application/octet-stream",
    )


@router.get("/files/{file_id}/thumbnail")
async def get_thumbnail(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """Get a file's thumbnail."""
    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    abs_thumb_path = to_absolute_path(file.thumbnail_path)
    if not abs_thumb_path or not abs_thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    # Detect media type from extension
    thumb_ext = abs_thumb_path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(thumb_ext, "image/png")

    return FastAPIFileResponse(str(abs_thumb_path), media_type=media_type)


@router.get("/files/{file_id}/gcode")
async def get_gcode(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get gcode for a file (for preview)."""
    user, can_read_all = auth_result
    result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
    file = _ensure_library_file_visible(result.scalar_one_or_none(), user, can_read_all)

    abs_path = to_absolute_path(file.file_path)
    if not abs_path or not abs_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Legacy sliced rows from before #1709 stored a `.gcode.3mf` ZIP body
    # under file_type="gcode" — the on-disk filename is the truth in that
    # case, so detect by suffix before checking the type column.
    is_gcode_3mf = file.file_type in ("3mf", "gcode.3mf") or file.filename.lower().endswith(".gcode.3mf")

    if is_gcode_3mf:
        try:
            with zipfile.ZipFile(str(abs_path), "r") as zf:
                gcode_files = [n for n in zf.namelist() if n.endswith(".gcode")]
                if not gcode_files:
                    raise HTTPException(status_code=404, detail="No gcode found in 3MF file")
                gcode_content = zf.read(gcode_files[0])
                from fastapi.responses import Response

                return Response(content=gcode_content, media_type="text/plain")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid 3MF file")
    elif file.file_type == "gcode":
        return FastAPIFileResponse(str(abs_path), media_type="text/plain")
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type")


# ============ Bulk Operations ============


@router.post("/files/move")
async def move_files(
    data: FileMoveRequest,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_UPDATE_ALL,
            Permission.LIBRARY_UPDATE_OWN,
        )
    ),
):
    """Move multiple files to a folder.

    Cross-boundary moves (managed ↔ external, or external ↔ external)
    physically relocate the bytes — see ``_move_file_bytes``. Same-boundary
    moves stay DB-only because the file's on-disk location doesn't depend
    on which managed folder owns it.

    Files not owned by the user are skipped (unless user has ``*_all``
    permission). Each skip carries a structured reason so the UI can
    surface "5 of 10 files were skipped: 3 had filename collisions on
    the NAS, 2 are no longer on disk" rather than a blank "skipped: 5".
    """
    user, can_modify_all = auth_result

    # Verify folder exists if specified
    target_folder: LibraryFolder | None = None
    if data.folder_id is not None:
        folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == data.folder_id))
        target_folder = folder_result.scalar_one_or_none()
        if not target_folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        if target_folder.is_external and target_folder.external_readonly:
            raise HTTPException(status_code=403, detail="Cannot move files to a read-only external folder")

    target_is_external = target_folder is not None and target_folder.is_external

    moved = 0
    skipped = 0
    skipped_reasons: list[dict] = []

    for file_id in data.file_ids:
        result = await db.execute(
            LibraryFile.active().options(selectinload(LibraryFile.folder)).where(LibraryFile.id == file_id)
        )
        file = result.scalar_one_or_none()
        if not file:
            continue
        # Ownership check
        if not can_modify_all and file.created_by_id != user.id:
            skipped += 1
            skipped_reasons.append({"file_id": file_id, "code": "not_owner", "reason": "not the file owner"})
            continue

        # No bytes need to move when both ends are managed (same-boundary).
        if not file.is_external and not target_is_external:
            file.folder_id = data.folder_id
            moved += 1
            continue

        # Block moves out of a read-only external mount. The user only has
        # read access to the source, and a move is semantically a delete on
        # the source — which a read-only mount can't fulfil. Without this
        # guard we'd succeed at copying to the target, fail to unlink the
        # source, and the same file would now exist in two places (with
        # the DB pointing at only one).
        if file.is_external and file.folder is not None and file.folder.external_readonly:
            skipped += 1
            skipped_reasons.append(
                {"file_id": file_id, "code": "source_readonly", "reason": "source is on a read-only external folder"}
            )
            continue

        # Otherwise relocate the bytes, then update the DB row to match.
        try:
            new_file_path = _move_file_bytes(file, target_folder)
        except _MoveSkip as e:
            skipped += 1
            skipped_reasons.append({"file_id": file_id, "code": e.code, "reason": e.reason})
            continue

        file.is_external = target_is_external
        file.folder_id = data.folder_id
        file.file_path = new_file_path
        # External rows historically carry `file_hash=None` (scan skips
        # hashing). When pulling an external file into managed storage,
        # compute the hash so dedup detection works for future uploads
        # of the same content.
        if not target_is_external and file.file_hash is None:
            try:
                abs_path = to_absolute_path(new_file_path)
                if abs_path:
                    file.file_hash = calculate_file_hash(abs_path)
            except OSError:
                pass  # leave hash null; dedup just won't match this row
        moved += 1

    await db.commit()

    return {
        "status": "success",
        "moved": moved,
        "skipped": skipped,
        "skipped_reasons": skipped_reasons,
    }


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete(
    data: BulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_DELETE_ALL,
            Permission.LIBRARY_DELETE_OWN,
        )
    ),
):
    """Delete multiple files and/or folders.

    Files not owned by the user are skipped (unless user has *_all permission).
    """
    user, can_modify_all = auth_result
    deleted_files = 0
    deleted_folders = 0
    skipped_files = 0

    # Delete files first. Managed files go to trash (sweeper hard-deletes bytes
    # later); external files bypass trash since their disk state is outside our
    # control and can't be restored from trash anyway.
    now = datetime.now(timezone.utc)
    for file_id in data.file_ids:
        result = await db.execute(LibraryFile.active().where(LibraryFile.id == file_id))
        file = result.scalar_one_or_none()
        if not file:
            continue
        if not can_modify_all and file.created_by_id != user.id:
            skipped_files += 1
            continue

        if file.is_external:
            abs_thumb_path = to_absolute_path(file.thumbnail_path)
            if abs_thumb_path and abs_thumb_path.exists():
                try:
                    abs_thumb_path.unlink()
                except OSError as e:
                    logger.warning("Failed to delete thumbnail from disk: %s", e)
            await db.delete(file)
        else:
            file.deleted_at = now
        deleted_files += 1

    # Delete folders (cascade will handle contents)
    # Note: Folders don't have ownership tracking currently, require *_all permission
    for folder_id in data.folder_ids:
        if not can_modify_all:
            # Users without *_all permission cannot delete folders
            continue

        result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
        folder = result.scalar_one_or_none()
        if folder:
            # Count files that will be deleted
            file_count_result = await db.execute(
                select(func.count(LibraryFile.id)).where(
                    LibraryFile.folder_id == folder_id,
                    LibraryFile.deleted_at.is_(None),
                )
            )
            deleted_files += file_count_result.scalar() or 0
            await db.delete(folder)
            deleted_folders += 1

    await db.commit()

    return BulkDeleteResponse(deleted_files=deleted_files, deleted_folders=deleted_folders)


# ============ Stats Endpoint ============


@router.get("/stats")
async def get_library_stats(
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """Get library statistics."""
    user, can_read_all = auth_result
    # Stats exclude trashed files — users see counts/sizes for what's actually in the library.
    # Without LIBRARY_READ_ALL the stats reflect only the caller's own files —
    # match what the file list endpoint shows so the numbers stay consistent.
    file_filters = [LibraryFile.deleted_at.is_(None)]
    if user is not None and not can_read_all:
        file_filters.append(LibraryFile.created_by_id == user.id)

    # Total files
    total_files_result = await db.execute(select(func.count(LibraryFile.id)).where(*file_filters))
    total_files = total_files_result.scalar() or 0

    # Total folders (folders are shared org structure, not per-user — count all)
    total_folders_result = await db.execute(select(func.count(LibraryFolder.id)))
    total_folders = total_folders_result.scalar() or 0

    # Total size
    total_size_result = await db.execute(select(func.sum(LibraryFile.file_size)).where(*file_filters))
    total_size = total_size_result.scalar() or 0

    # Files by type
    type_result = await db.execute(
        select(LibraryFile.file_type, func.count(LibraryFile.id)).where(*file_filters).group_by(LibraryFile.file_type)
    )
    files_by_type = dict(type_result.all())

    # Total prints
    total_prints_result = await db.execute(select(func.sum(LibraryFile.print_count)).where(*file_filters))
    total_prints = total_prints_result.scalar() or 0

    # Disk space info
    library_dir = get_library_dir()
    try:
        disk_stat = shutil.disk_usage(library_dir)
        disk_free_bytes = disk_stat.free
        disk_total_bytes = disk_stat.total
        disk_used_bytes = disk_stat.used
    except OSError:
        disk_free_bytes = 0
        disk_total_bytes = 0
        disk_used_bytes = 0

    return {
        "total_files": total_files,
        "total_folders": total_folders,
        "total_size_bytes": total_size,
        "files_by_type": files_by_type,
        "total_prints": total_prints,
        "disk_free_bytes": disk_free_bytes,
        "disk_total_bytes": disk_total_bytes,
        "disk_used_bytes": disk_used_bytes,
    }
