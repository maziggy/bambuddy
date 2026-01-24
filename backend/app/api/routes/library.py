"""API routes for File Manager (Library) functionality."""

import base64
import hashlib
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse as FastAPIFileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project
from backend.app.schemas.library import (
    AddToQueueError,
    AddToQueueRequest,
    AddToQueueResponse,
    AddToQueueResult,
    BulkDeleteRequest,
    BulkDeleteResponse,
    ExternalFolderCreate,
    ExternalFolderScanResponse,
    ExternalFolderUpdate,
    ExternalFolderValidateRequest,
    ExternalFolderValidateResponse,
    FileDuplicate,
    FileListResponse,
    FileMoveRequest,
    FilePrintRequest,
    FileResponse as FileResponseSchema,
    FileUpdate,
    FileUploadResponse,
    FolderCreate,
    FolderResponse,
    FolderTreeItem,
    FolderUpdate,
    ZipExtractError,
    ZipExtractResponse,
    ZipExtractResult,
)
from backend.app.services.archive import ArchiveService, ThreeMFParser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])


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


def get_library_thumbnails_dir() -> Path:
    """Get the directory for library thumbnails."""
    thumbnails_dir = get_library_dir() / "thumbnails"
    thumbnails_dir.mkdir(parents=True, exist_ok=True)
    return thumbnails_dir


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


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
                    except Exception:
                        pass
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
        logger.warning(f"Failed to extract gcode thumbnail: {e}")
        return None


def create_image_thumbnail(file_path: Path, thumbnails_dir: Path, max_size: int = 256) -> str | None:
    """Create a thumbnail from an image file.

    For small images, copies directly. For larger images, resizes.
    Returns the thumbnail path or None on failure.
    """
    try:
        from PIL import Image

        thumb_filename = f"{uuid.uuid4().hex}.png"
        thumb_path = thumbnails_dir / thumb_filename

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
                thumb_path = thumbnails_dir / thumb_filename
                shutil.copy2(file_path, thumb_path)
                return str(thumb_path)
        except Exception:
            pass
        return None
    except Exception as e:
        logger.warning(f"Failed to create image thumbnail: {e}")
        return None


# Supported image extensions for thumbnails
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}


# ============ Folder Endpoints ============


@router.get("/folders", response_model=list[FolderTreeItem])
@router.get("/folders/", response_model=list[FolderTreeItem])
async def list_folders(response: Response, db: AsyncSession = Depends(get_db)):
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
        .where(LibraryFile.folder_id.isnot(None))
        .group_by(LibraryFile.folder_id)
    )
    file_counts = dict(file_counts_result.all())

    # Build tree structure
    folder_map = {}
    root_folders = []

    for folder, project_name, archive_name in rows:
        # Check if external path still exists
        external_accessible = True
        if folder.is_external and folder.external_path:
            try:
                external_accessible = os.path.exists(folder.external_path)
            except Exception:
                external_accessible = False

        folder_item = FolderTreeItem(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            project_id=folder.project_id,
            archive_id=folder.archive_id,
            project_name=project_name,
            archive_name=archive_name,
            file_count=file_counts.get(folder.id, 0),
            is_external=folder.is_external,
            external_path=folder.external_path,
            external_readonly=folder.external_readonly,
            external_show_hidden=folder.external_show_hidden,
            external_extensions=folder.external_extensions,
            external_last_scan=folder.external_last_scan,
            external_accessible=external_accessible,
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
async def get_folders_by_project(project_id: int, db: AsyncSession = Depends(get_db)):
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
        # Get file count
        file_count_result = await db.execute(
            select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder.id)
        )
        file_count = file_count_result.scalar() or 0

        folders.append(
            FolderResponse(
                id=folder.id,
                name=folder.name,
                parent_id=folder.parent_id,
                project_id=folder.project_id,
                archive_id=folder.archive_id,
                project_name=project_name,
                archive_name=None,
                file_count=file_count,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        )

    return folders


@router.get("/folders/by-archive/{archive_id}", response_model=list[FolderResponse])
async def get_folders_by_archive(archive_id: int, db: AsyncSession = Depends(get_db)):
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
        # Get file count
        file_count_result = await db.execute(
            select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder.id)
        )
        file_count = file_count_result.scalar() or 0

        folders.append(
            FolderResponse(
                id=folder.id,
                name=folder.name,
                parent_id=folder.parent_id,
                project_id=folder.project_id,
                archive_id=folder.archive_id,
                project_name=None,
                archive_name=archive_name,
                file_count=file_count,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        )

    return folders


@router.post("/folders", response_model=FolderResponse)
@router.post("/folders/", response_model=FolderResponse)
async def create_folder(data: FolderCreate, db: AsyncSession = Depends(get_db)):
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
    await db.flush()
    await db.refresh(folder)

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        file_count=0,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.get("/folders/{folder_id}", response_model=FolderResponse)
async def get_folder(folder_id: int, db: AsyncSession = Depends(get_db)):
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

    # Get file count
    file_count_result = await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder_id))
    file_count = file_count_result.scalar() or 0

    # Check if external path still exists
    external_accessible = True
    if folder.is_external and folder.external_path:
        try:
            external_accessible = os.path.exists(folder.external_path)
        except Exception:
            external_accessible = False

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        file_count=file_count,
        is_external=folder.is_external,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        external_extensions=folder.external_extensions,
        external_last_scan=folder.external_last_scan,
        external_accessible=external_accessible,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.put("/folders/{folder_id}", response_model=FolderResponse)
async def update_folder(folder_id: int, data: FolderUpdate, db: AsyncSession = Depends(get_db)):
    """Update a folder."""
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

    await db.flush()
    await db.refresh(folder)

    # Get file count and names
    file_count_result = await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder_id))
    file_count = file_count_result.scalar() or 0

    # Get project and archive names
    project_name = None
    archive_name = None
    if folder.project_id:
        project_result = await db.execute(select(Project.name).where(Project.id == folder.project_id))
        project_name = project_result.scalar()
    if folder.archive_id:
        archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == folder.archive_id))
        archive_name = archive_result.scalar()

    # Check if external path still exists
    external_accessible = True
    if folder.is_external and folder.external_path:
        try:
            external_accessible = os.path.exists(folder.external_path)
        except Exception:
            external_accessible = False

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        file_count=file_count,
        is_external=folder.is_external,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        external_extensions=folder.external_extensions,
        external_last_scan=folder.external_last_scan,
        external_accessible=external_accessible,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a folder and all its contents (cascade)."""
    result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
    folder = result.scalar_one_or_none()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Get all files in this folder and subfolders to delete from disk
    async def get_all_file_ids(fid: int) -> list[int]:
        """Recursively get all file IDs in a folder tree."""
        file_ids = []

        # Get files in this folder
        files_result = await db.execute(
            select(LibraryFile.id, LibraryFile.file_path, LibraryFile.thumbnail_path).where(
                LibraryFile.folder_id == fid
            )
        )
        for file_id, file_path, thumb_path in files_result.all():
            file_ids.append(file_id)
            # Delete actual files
            try:
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except Exception as e:
                logger.warning(f"Failed to delete file: {e}")

        # Get child folders and recurse
        children_result = await db.execute(select(LibraryFolder.id).where(LibraryFolder.parent_id == fid))
        for (child_id,) in children_result.all():
            file_ids.extend(await get_all_file_ids(child_id))

        return file_ids

    await get_all_file_ids(folder_id)

    # Delete folder (cascade will handle files and subfolders)
    await db.delete(folder)

    return {"status": "success", "message": "Folder deleted"}


# ============ External Folder Endpoints ============


@router.post("/folders/external/validate", response_model=ExternalFolderValidateResponse)
async def validate_external_path(
    data: ExternalFolderValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Validate external path before creating folder.

    Returns preview info: file count, accessibility, validation errors.
    """
    from backend.app.services.external_library import ExternalLibraryService

    service = ExternalLibraryService(db)
    is_valid, error = await service.validate_external_path(data.path)

    if not is_valid:
        return ExternalFolderValidateResponse(
            valid=False,
            error=error,
            accessible=False,
        )

    # Get file count preview
    file_count = 0
    directory_size_mb = 0.0
    try:
        from pathlib import Path
        import asyncio

        path = Path(data.path)
        files = await service.enumerate_files_recursive(path, True, None, 2)
        file_count = len(files)
        directory_size_mb = sum(f["size"] for f in files) / (1024 * 1024)
    except Exception as e:
        logger.warning(f"Failed to get file count preview: {e}")

    return ExternalFolderValidateResponse(
        valid=True,
        accessible=True,
        file_count=file_count,
        directory_size_mb=round(directory_size_mb, 2),
    )


@router.post("/folders/external", response_model=FolderResponse)
async def create_external_folder(
    data: ExternalFolderCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create external folder mount.

    Steps:
    1. Validate path via ExternalLibraryService
    2. Check parent folder exists if parent_id provided
    3. Create LibraryFolder with is_external=True
    4. Run initial scan (smart refresh - only if needed)
    5. Return folder response
    """
    from backend.app.services.external_library import ExternalLibraryService

    service = ExternalLibraryService(db)

    # Validate path
    is_valid, error = await service.validate_external_path(data.external_path)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Verify parent exists if specified
    if data.parent_id is not None:
        parent_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == data.parent_id))
        if not parent_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Parent folder not found")

    # Create external folder
    folder = LibraryFolder(
        name=data.name,
        parent_id=data.parent_id,
        is_external=True,
        external_path=data.external_path,
        external_readonly=data.external_readonly,
        external_show_hidden=data.external_show_hidden,
        external_extensions=data.external_extensions,
    )
    db.add(folder)
    await db.flush()
    await db.refresh(folder)

    # Run initial scan
    scan_result = await service.scan_external_folder(folder.id, force_rescan=True)
    logger.info(f"Created external folder {folder.id} with initial scan: {scan_result}")

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        is_external=True,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        external_extensions=folder.external_extensions,
        external_last_scan=folder.external_last_scan,
        external_accessible=True,
        file_count=0,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.post("/folders/{folder_id}/scan", response_model=ExternalFolderScanResponse)
async def scan_external_folder(
    folder_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Scan external folder to sync with filesystem.

    Smart refresh: Only rescans if directory mtime changed (unless force=True).

    Returns stats: added, updated, removed file counts.
    """
    import time

    from backend.app.services.external_library import ExternalLibraryService

    result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
    folder = result.scalar_one_or_none()

    if not folder or not folder.is_external:
        raise HTTPException(status_code=404, detail="External folder not found")

    service = ExternalLibraryService(db)
    start_time = time.time()
    scan_result = await service.scan_external_folder(folder_id, force_rescan=force)
    duration = time.time() - start_time

    # Get updated folder
    await db.refresh(folder)

    return ExternalFolderScanResponse(
        added=scan_result["added"],
        updated=scan_result["updated"],
        removed=scan_result["removed"],
        scan_duration_seconds=round(duration, 2),
        last_scan=folder.external_last_scan or datetime.utcnow(),
    )


@router.put("/folders/{folder_id}/external", response_model=FolderResponse)
async def update_external_folder(
    folder_id: int,
    data: ExternalFolderUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update external folder settings.

    Note: Cannot change external_path (security). Delete and recreate instead.

    If show_hidden or extensions changed, triggers background rescan.
    """
    result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
    folder = result.scalar_one_or_none()

    if not folder or not folder.is_external:
        raise HTTPException(status_code=404, detail="External folder not found")

    # Track if settings changed (requires rescan)
    settings_changed = False

    if data.name is not None:
        folder.name = data.name

    if data.external_readonly is not None:
        folder.external_readonly = data.external_readonly

    if data.external_show_hidden is not None:
        if folder.external_show_hidden != data.external_show_hidden:
            settings_changed = True
        folder.external_show_hidden = data.external_show_hidden

    if data.external_extensions is not None:
        if folder.external_extensions != data.external_extensions:
            settings_changed = True
        folder.external_extensions = data.external_extensions

    folder.updated_at = datetime.utcnow()
    await db.commit()

    # Trigger rescan if settings changed
    if settings_changed:
        from backend.app.services.external_library import ExternalLibraryService

        service = ExternalLibraryService(db)
        await service.scan_external_folder(folder_id, force_rescan=True)

    # Get file count
    file_count_result = await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder_id))
    file_count = file_count_result.scalar() or 0

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        is_external=True,
        external_path=folder.external_path,
        external_readonly=folder.external_readonly,
        external_show_hidden=folder.external_show_hidden,
        external_extensions=folder.external_extensions,
        external_last_scan=folder.external_last_scan,
        external_accessible=True,
        file_count=file_count,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


# ============ File Endpoints ============


@router.get("/files", response_model=list[FileListResponse])
@router.get("/files/", response_model=list[FileListResponse])
async def list_files(
    response: Response,
    folder_id: int | None = None,
    include_root: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """List files, optionally filtered by folder.

    Args:
        folder_id: Filter by folder ID. If None and include_root=True, returns root files.
        include_root: If True and folder_id is None, returns files at root level.
                     If False and folder_id is None, returns all files.
    """
    query = select(LibraryFile)

    if folder_id is not None:
        query = query.where(LibraryFile.folder_id == folder_id)
    elif include_root:
        query = query.where(LibraryFile.folder_id.is_(None))

    query = query.order_by(LibraryFile.filename)
    result = await db.execute(query)
    files = result.scalars().all()

    # Get duplicate counts
    hash_counts = {}
    if files:
        hashes = [f.file_hash for f in files if f.file_hash]
        if hashes:
            dup_result = await db.execute(
                select(LibraryFile.file_hash, func.count(LibraryFile.id))
                .where(LibraryFile.file_hash.in_(hashes))
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
        if f.file_metadata:
            print_name = f.file_metadata.get("print_name")
            print_time = f.file_metadata.get("print_time_seconds")
            filament_grams = f.file_metadata.get("filament_used_grams")

        file_list.append(
            FileListResponse(
                id=f.id,
                folder_id=f.folder_id,
                filename=f.filename,
                file_type=f.file_type,
                file_size=f.file_size,
                thumbnail_path=f.thumbnail_path,
                print_count=f.print_count,
                duplicate_count=hash_counts.get(f.file_hash, 0) if f.file_hash else 0,
                created_at=f.created_at,
                print_name=print_name,
                print_time_seconds=print_time,
                filament_used_grams=filament_grams,
            )
        )

    return file_list


@router.post("/files", response_model=FileUploadResponse)
@router.post("/files/", response_model=FileUploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    folder_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Upload a file to the library."""
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")

        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()
        # Handle files without extension
        file_type = ext[1:] if ext else "unknown"

        # Verify folder exists if specified
        if folder_id is not None:
            folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
            if not folder_result.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Folder not found")

        # Generate unique filename for storage
        unique_filename = f"{uuid.uuid4().hex}{ext}"
        file_path = get_library_files_dir() / unique_filename

        # Save file
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Calculate hash
        file_hash = calculate_file_hash(file_path)

        # Check for duplicates
        dup_result = await db.execute(select(LibraryFile.id).where(LibraryFile.file_hash == file_hash).limit(1))
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
                    thumb_path = thumbnails_dir / thumb_filename
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
                logger.warning(f"Failed to parse 3MF: {e}")

        elif ext == ".gcode":
            # Extract embedded thumbnail from gcode
            try:
                thumbnail_data = extract_gcode_thumbnail(file_path)
                if thumbnail_data:
                    thumb_filename = f"{uuid.uuid4().hex}.png"
                    thumb_path = thumbnails_dir / thumb_filename
                    with open(thumb_path, "wb") as f:
                        f.write(thumbnail_data)
                    thumbnail_path = str(thumb_path)
            except Exception as e:
                logger.warning(f"Failed to extract gcode thumbnail: {e}")

        elif ext.lower() in IMAGE_EXTENSIONS:
            # For image files, create a thumbnail from the image itself
            thumbnail_path = create_image_thumbnail(file_path, thumbnails_dir)

        # Create database entry
        library_file = LibraryFile(
            folder_id=folder_id,
            filename=filename,
            file_path=str(file_path),
            file_type=file_type,
            file_size=len(content),
            file_hash=file_hash,
            thumbnail_path=thumbnail_path,
            file_metadata=metadata if metadata else None,
        )
        db.add(library_file)
        await db.flush()
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
        logger.error(f"Upload failed for {file.filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/files/extract-zip", response_model=ZipExtractResponse)
async def extract_zip_file(
    file: UploadFile = File(...),
    folder_id: int | None = None,
    preserve_structure: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Upload and extract a ZIP file to the library.

    Args:
        file: The ZIP file to extract
        folder_id: Target folder ID (None = root)
        preserve_structure: If True, recreate folder structure from ZIP; if False, extract all files flat
    """
    import tempfile
    import zipfile

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are supported")

    # Verify target folder exists if specified
    if folder_id is not None:
        folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
        if not folder_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Target folder not found")

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
                    # Determine target folder
                    target_folder_id = folder_id

                    if preserve_structure:
                        # Get directory path from ZIP
                        dir_path = os.path.dirname(zip_path)
                        if dir_path:
                            # Create folder structure
                            parts = dir_path.split("/")
                            current_parent = folder_id
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
                    file_type = ext[1:] if ext else "unknown"

                    # Generate unique filename for storage
                    unique_filename = f"{uuid.uuid4().hex}{ext}"
                    file_path = get_library_files_dir() / unique_filename

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
                                thumb_path = thumbnails_dir / thumb_filename
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
                            logger.warning(f"Failed to parse 3MF from ZIP: {e}")

                    elif ext == ".gcode":
                        try:
                            thumbnail_data = extract_gcode_thumbnail(file_path)
                            if thumbnail_data:
                                thumb_filename = f"{uuid.uuid4().hex}.png"
                                thumb_path = thumbnails_dir / thumb_filename
                                with open(thumb_path, "wb") as f:
                                    f.write(thumbnail_data)
                                thumbnail_path = str(thumb_path)
                        except Exception as e:
                            logger.warning(f"Failed to extract gcode thumbnail from ZIP: {e}")

                    elif ext.lower() in IMAGE_EXTENSIONS:
                        thumbnail_path = create_image_thumbnail(file_path, thumbnails_dir)

                    # Create database entry
                    library_file = LibraryFile(
                        folder_id=target_folder_id,
                        filename=filename,
                        file_path=str(file_path),
                        file_type=file_type,
                        file_size=len(file_content),
                        file_hash=file_hash,
                        thumbnail_path=thumbnail_path,
                        file_metadata=metadata if metadata else None,
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
                    logger.error(f"Failed to extract {zip_path}: {e}")
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
        logger.error(f"ZIP extraction failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"ZIP extraction failed: {str(e)}")
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


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
):
    """Add library files to the print queue.

    Only sliced files (.gcode or .gcode.3mf) can be added to the queue.
    The archive will be created automatically when the print starts.
    """
    added: list[AddToQueueResult] = []
    errors: list[AddToQueueError] = []

    # Get all requested files
    result = await db.execute(select(LibraryFile).where(LibraryFile.id.in_(request.file_ids)))
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
            logger.exception(f"Error adding file {file_id} to queue")
            errors.append(AddToQueueError(file_id=file_id, filename=lib_file.filename, error=str(e)))

    await db.commit()

    return AddToQueueResponse(added=added, errors=errors)


@router.get("/files/{file_id}/plates")
async def get_library_file_plates(
    file_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get available plates from a multi-plate 3MF library file.

    Returns a list of plates with their index, name, thumbnail availability,
    and filament requirements. For single-plate exports, returns a single plate.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    # Get the library file
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    lib_file = result.scalar_one_or_none()

    if not lib_file:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(app_settings.base_dir) / lib_file.file_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Only 3MF files have plates
    if not lib_file.filename.lower().endswith(".3mf"):
        return {"file_id": file_id, "filename": lib_file.filename, "plates": [], "is_multi_plate": False}

    plates = []

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = zf.namelist()

            # Find all plate gcode files to determine available plates
            gcode_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".gcode")]

            if not gcode_files:
                # No sliced plates found
                return {"file_id": file_id, "filename": lib_file.filename, "plates": [], "is_multi_plate": False}

            # Extract plate indices from gcode filenames
            plate_indices = []
            for gf in gcode_files:
                try:
                    plate_str = gf[15:-6]  # Remove "Metadata/plate_" and ".gcode"
                    plate_indices.append(int(plate_str))
                except ValueError:
                    pass

            plate_indices.sort()

            # Parse model_settings.config for plate names
            plate_names = {}
            if "Metadata/model_settings.config" in namelist:
                try:
                    model_content = zf.read("Metadata/model_settings.config").decode()
                    model_root = ET.fromstring(model_content)
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
                                    pass
                            elif key == "plater_name" and value:
                                plater_name = value.strip()
                        if plater_id is not None and plater_name:
                            plate_names[plater_id] = plater_name
                except Exception:
                    pass

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
                                pass
                        elif key == "prediction" and value:
                            try:
                                plate_info["prediction"] = int(value)
                            except ValueError:
                                pass
                        elif key == "weight" and value:
                            try:
                                plate_info["weight"] = float(value)
                            except ValueError:
                                pass

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

            # Build plate list
            for idx in plate_indices:
                meta = plate_metadata.get(idx, {})
                has_thumbnail = f"Metadata/plate_{idx}.png" in namelist

                plates.append(
                    {
                        "index": idx,
                        "name": meta.get("name"),
                        "objects": meta.get("objects", []),
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
        logger.warning(f"Failed to parse plates from library file {file_id}: {e}")

    return {
        "file_id": file_id,
        "filename": lib_file.filename,
        "plates": plates,
        "is_multi_plate": len(plates) > 1,
    }


@router.get("/files/{file_id}/plate-thumbnail/{plate_index}")
async def get_library_file_plate_thumbnail(
    file_id: int,
    plate_index: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the thumbnail image for a specific plate from a library file."""
    import zipfile

    from starlette.responses import Response

    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
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
        pass

    raise HTTPException(status_code=404, detail=f"Thumbnail for plate {plate_index} not found")


@router.get("/files/{file_id}/filament-requirements")
async def get_library_file_filament_requirements(
    file_id: int,
    plate_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get filament requirements from a library file.

    Parses the 3MF file to extract filament slot IDs, types, colors, and usage.
    This enables AMS slot assignment when printing from the file manager.

    Args:
        file_id: The library file ID
        plate_id: Optional plate index to get filaments for a specific plate
    """
    import xml.etree.ElementTree as ET
    import zipfile

    # Get the library file
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    lib_file = result.scalar_one_or_none()

    if not lib_file:
        raise HTTPException(status_code=404, detail="File not found")

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
                                    pass
                                break

                        if plate_index == plate_id:
                            # Extract filaments from this plate
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
                                    filaments.append(
                                        {
                                            "slot_id": int(filament_id),
                                            "type": filament_type,
                                            "color": filament_color,
                                            "used_grams": round(used_grams, 1),
                                            "used_meters": float(used_m) if used_m else 0,
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
                                }
                            )

            # Sort by slot ID
            filaments.sort(key=lambda x: x["slot_id"])

    except Exception as e:
        logger.warning(f"Failed to parse filament requirements from library file {file_id}: {e}")

    return {
        "file_id": file_id,
        "filename": lib_file.filename,
        "plate_id": plate_id,
        "filaments": filaments,
    }


@router.post("/files/{file_id}/print")
async def print_library_file(
    file_id: int,
    printer_id: int,
    body: FilePrintRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Print a library file directly.

    This endpoint:
    1. Creates an archive from the library file
    2. Uploads the file to the printer
    3. Starts the print

    Only sliced files (.gcode or .gcode.3mf) can be printed.
    """
    import zipfile

    from backend.app.main import register_expected_print
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import (
        delete_file_async,
        get_ftp_retry_settings,
        upload_file_async,
        with_ftp_retry,
    )
    from backend.app.services.printer_manager import printer_manager

    # Use defaults if no body provided
    if body is None:
        body = FilePrintRequest()

    # Get the library file
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    lib_file = result.scalar_one_or_none()

    if not lib_file:
        raise HTTPException(status_code=404, detail="File not found")

    # Validate file is sliced
    if not is_sliced_file(lib_file.filename):
        raise HTTPException(
            status_code=400,
            detail="Not a sliced file. Only .gcode or .gcode.3mf files can be printed.",
        )

    # Get the full file path
    file_path = Path(app_settings.base_dir) / lib_file.file_path

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    # Check printer is connected
    if not printer_manager.is_connected(printer_id):
        raise HTTPException(status_code=400, detail="Printer is not connected")

    # Create archive from the library file
    archive_service = ArchiveService(db)
    archive = await archive_service.archive_print(
        printer_id=printer_id,
        source_file=file_path,
    )

    if not archive:
        raise HTTPException(status_code=500, detail="Failed to create archive")

    await db.flush()

    # Prepare remote filename
    base_name = lib_file.filename
    if base_name.endswith(".gcode.3mf"):
        base_name = base_name[:-10]
    elif base_name.endswith(".3mf"):
        base_name = base_name[:-4]
    remote_filename = f"{base_name}.3mf"
    remote_path = f"/{remote_filename}"

    # Get FTP retry settings
    ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

    # Delete existing file if present (avoids 553 error)
    await delete_file_async(
        printer.ip_address,
        printer.access_code,
        remote_path,
        socket_timeout=ftp_timeout,
        printer_model=printer.model,
    )

    # Upload file to printer
    if ftp_retry_enabled:
        uploaded = await with_ftp_retry(
            upload_file_async,
            printer.ip_address,
            printer.access_code,
            file_path,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
            max_retries=ftp_retry_count,
            retry_delay=ftp_retry_delay,
            operation_name=f"Upload for print to {printer.name}",
        )
    else:
        uploaded = await upload_file_async(
            printer.ip_address,
            printer.access_code,
            file_path,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
        )

    if not uploaded:
        raise HTTPException(status_code=500, detail="Failed to upload file to printer")

    # Register this as an expected print so we don't create a duplicate archive
    register_expected_print(printer_id, remote_filename, archive.id)

    # Determine plate ID
    if body.plate_id is not None:
        plate_id = body.plate_id
    else:
        plate_id = 1
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in zf.namelist():
                    if name.startswith("Metadata/plate_") and name.endswith(".gcode"):
                        plate_str = name[15:-6]
                        plate_id = int(plate_str)
                        break
        except Exception:
            pass

    logger.info(
        f"Print library file {file_id}: archive_id={archive.id}, plate_id={plate_id}, "
        f"ams_mapping={body.ams_mapping}, bed_levelling={body.bed_levelling}"
    )

    # Start the print
    started = printer_manager.start_print(
        printer_id,
        remote_filename,
        plate_id,
        ams_mapping=body.ams_mapping,
        timelapse=body.timelapse,
        bed_levelling=body.bed_levelling,
        flow_cali=body.flow_cali,
        vibration_cali=body.vibration_cali,
        layer_inspect=body.layer_inspect,
        use_ams=body.use_ams,
    )

    if not started:
        raise HTTPException(status_code=500, detail="Failed to start print")

    await db.commit()

    return {
        "status": "printing",
        "printer_id": printer_id,
        "archive_id": archive.id,
        "filename": lib_file.filename,
    }


# ============ File Detail Endpoints ============


@router.get("/files/{file_id}", response_model=FileResponseSchema)
async def get_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Get a file by ID with full details."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

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
            .where(LibraryFile.file_hash == file.file_hash, LibraryFile.id != file.id)
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
        created_at=file.created_at,
        updated_at=file.updated_at,
    )


@router.put("/files/{file_id}", response_model=FileResponseSchema)
async def update_file(file_id: int, data: FileUpdate, db: AsyncSession = Depends(get_db)):
    """Update a file's metadata."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if data.filename is not None:
        # Validate filename doesn't contain path separators
        if "/" in data.filename or "\\" in data.filename:
            raise HTTPException(status_code=400, detail="Filename cannot contain path separators")
        file.filename = data.filename

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

    await db.flush()
    await db.refresh(file)

    # Return full response (reuse get_file logic)
    return await get_file(file_id, db)


@router.delete("/files/{file_id}")
async def delete_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a file."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # Delete actual files
    try:
        if file.file_path and os.path.exists(file.file_path):
            os.remove(file.file_path)
        if file.thumbnail_path and os.path.exists(file.thumbnail_path):
            os.remove(file.thumbnail_path)
    except Exception as e:
        logger.warning(f"Failed to delete file from disk: {e}")

    await db.delete(file)

    return {"status": "success", "message": "File deleted"}


# ============ File Content Endpoints ============


@router.get("/files/{file_id}/download")
async def download_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Download a file."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if not file.file_path or not os.path.exists(file.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FastAPIFileResponse(
        file.file_path,
        filename=file.filename,
        media_type="application/octet-stream",
    )


@router.get("/files/{file_id}/thumbnail")
async def get_thumbnail(file_id: int, db: AsyncSession = Depends(get_db)):
    """Get a file's thumbnail."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if not file.thumbnail_path or not os.path.exists(file.thumbnail_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    # Detect media type from extension
    thumb_ext = os.path.splitext(file.thumbnail_path)[1].lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(thumb_ext, "image/png")

    return FastAPIFileResponse(file.thumbnail_path, media_type=media_type)


@router.get("/files/{file_id}/gcode")
async def get_gcode(file_id: int, db: AsyncSession = Depends(get_db)):
    """Get gcode for a file (for preview)."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if not file.file_path or not os.path.exists(file.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    if file.file_type == "gcode":
        return FastAPIFileResponse(file.file_path, media_type="text/plain")
    elif file.file_type == "3mf":
        # Extract gcode from 3mf
        import zipfile

        try:
            with zipfile.ZipFile(file.file_path, "r") as zf:
                # Find gcode file
                gcode_files = [n for n in zf.namelist() if n.endswith(".gcode")]
                if not gcode_files:
                    raise HTTPException(status_code=404, detail="No gcode found in 3MF file")
                gcode_content = zf.read(gcode_files[0])
                from fastapi.responses import Response

                return Response(content=gcode_content, media_type="text/plain")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid 3MF file")
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type")


# ============ Bulk Operations ============


@router.post("/files/move")
async def move_files(data: FileMoveRequest, db: AsyncSession = Depends(get_db)):
    """Move multiple files to a folder."""
    # Verify folder exists if specified
    if data.folder_id is not None:
        folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == data.folder_id))
        if not folder_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Folder not found")

    # Update files
    moved = 0
    for file_id in data.file_ids:
        result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
        file = result.scalar_one_or_none()
        if file:
            file.folder_id = data.folder_id
            moved += 1

    return {"status": "success", "moved": moved}


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete(data: BulkDeleteRequest, db: AsyncSession = Depends(get_db)):
    """Delete multiple files and/or folders."""
    deleted_files = 0
    deleted_folders = 0

    # Delete files first
    for file_id in data.file_ids:
        result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
        file = result.scalar_one_or_none()
        if file:
            try:
                if file.file_path and os.path.exists(file.file_path):
                    os.remove(file.file_path)
                if file.thumbnail_path and os.path.exists(file.thumbnail_path):
                    os.remove(file.thumbnail_path)
            except Exception as e:
                logger.warning(f"Failed to delete file from disk: {e}")
            await db.delete(file)
            deleted_files += 1

    # Delete folders (cascade will handle contents)
    for folder_id in data.folder_ids:
        result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
        folder = result.scalar_one_or_none()
        if folder:
            # Count files that will be deleted
            file_count_result = await db.execute(
                select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder_id)
            )
            deleted_files += file_count_result.scalar() or 0
            await db.delete(folder)
            deleted_folders += 1

    return BulkDeleteResponse(deleted_files=deleted_files, deleted_folders=deleted_folders)


# ============ Stats Endpoint ============


@router.get("/stats")
async def get_library_stats(db: AsyncSession = Depends(get_db)):
    """Get library statistics."""
    # Total files
    total_files_result = await db.execute(select(func.count(LibraryFile.id)))
    total_files = total_files_result.scalar() or 0

    # Total folders
    total_folders_result = await db.execute(select(func.count(LibraryFolder.id)))
    total_folders = total_folders_result.scalar() or 0

    # Total size
    total_size_result = await db.execute(select(func.sum(LibraryFile.file_size)))
    total_size = total_size_result.scalar() or 0

    # Files by type
    type_result = await db.execute(
        select(LibraryFile.file_type, func.count(LibraryFile.id)).group_by(LibraryFile.file_type)
    )
    files_by_type = dict(type_result.all())

    # Total prints
    total_prints_result = await db.execute(select(func.sum(LibraryFile.print_count)))
    total_prints = total_prints_result.scalar() or 0

    # Disk space info
    library_dir = get_library_dir()
    try:
        disk_stat = shutil.disk_usage(library_dir)
        disk_free_bytes = disk_stat.free
        disk_total_bytes = disk_stat.total
        disk_used_bytes = disk_stat.used
    except Exception:
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
