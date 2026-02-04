"""API routes for File Manager (Library) functionality."""

import base64
import hashlib
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse as FastAPIFileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import (
    require_ownership_permission,
    require_permission_if_auth_enabled,
)
from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile, LibraryFolder
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
from backend.app.services.stl_thumbnail import generate_stl_thumbnail

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
    # Handle already-absolute paths (for backwards compatibility during migration)
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return Path(app_settings.base_dir) / relative_path


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
async def list_folders(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
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
        .where(LibraryFile.folder_id.isnot(None))
        .group_by(LibraryFile.folder_id)
    )
    file_counts = dict(file_counts_result.all())

    # Build tree structure
    folder_map = {}
    root_folders = []

    for folder, project_name, archive_name in rows:
        folder_item = FolderTreeItem(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            project_id=folder.project_id,
            archive_id=folder.archive_id,
            project_name=project_name,
            archive_name=archive_name,
            file_count=file_counts.get(folder.id, 0),
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
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
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
async def get_folders_by_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
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
async def get_folder(
    folder_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
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

    # Get file count
    file_count_result = await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder_id))
    file_count = file_count_result.scalar() or 0

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        file_count=file_count,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


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

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        project_id=folder.project_id,
        archive_id=folder.archive_id,
        project_name=project_name,
        archive_name=archive_name,
        file_count=file_count,
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


# ============ File Endpoints ============


@router.get("/files", response_model=list[FileListResponse])
@router.get("/files/", response_model=list[FileListResponse])
async def list_files(
    response: Response,
    folder_id: int | None = None,
    include_root: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
):
    """List files, optionally filtered by folder.

    Args:
        folder_id: Filter by folder ID. If None and include_root=True, returns root files.
        include_root: If True and folder_id is None, returns files at root level.
                     If False and folder_id is None, returns all files.
    """
    query = select(LibraryFile).options(selectinload(LibraryFile.created_by))

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

        elif ext == ".stl":
            # Generate STL thumbnail if enabled
            if generate_stl_thumbnails:
                thumbnail_path = generate_stl_thumbnail(file_path, thumbnails_dir)

        # Create database entry (store relative paths for portability)
        library_file = LibraryFile(
            folder_id=folder_id,
            filename=filename,
            file_path=to_relative_path(file_path),
            file_type=file_type,
            file_size=len(content),
            file_hash=file_hash,
            thumbnail_path=to_relative_path(thumbnail_path) if thumbnail_path else None,
            file_metadata=metadata if metadata else None,
            created_by_id=current_user.id if current_user else None,
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
            logger.info(f"Reusing existing folder '{zip_folder_name}' with id={zip_folder_id}")
        else:
            # Create folder
            new_folder = LibraryFolder(name=zip_folder_name, parent_id=folder_id)
            db.add(new_folder)
            await db.flush()
            await db.commit()  # Commit folder creation immediately
            zip_folder_id = new_folder.id
            folders_created += 1
            logger.info(f"Created new folder '{zip_folder_name}' with id={zip_folder_id}")

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

                    elif ext == ".stl":
                        # Generate STL thumbnail if enabled
                        if generate_stl_thumbnails:
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
                        file_metadata=metadata if metadata else None,
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
    query = select(LibraryFile).where(LibraryFile.file_type == "stl")

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
            logger.error(f"Failed to generate thumbnail for {stl_file.filename}: {e}")
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
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
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
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
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
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.PRINTERS_CONTROL)),
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

    logger.info(
        f"Library print FTP upload starting: printer={printer.name} ({printer.model}), "
        f"ip={printer.ip_address}, file={remote_filename}, local_path={file_path}, "
        f"retry_enabled={ftp_retry_enabled}, retry_count={ftp_retry_count}, timeout={ftp_timeout}"
    )

    # Delete existing file if present (avoids 553 error)
    logger.debug(f"Deleting existing file {remote_path} if present...")
    delete_result = await delete_file_async(
        printer.ip_address,
        printer.access_code,
        remote_path,
        socket_timeout=ftp_timeout,
        printer_model=printer.model,
    )
    logger.debug(f"Delete result: {delete_result}")

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
        logger.error(
            f"FTP upload failed for library print: printer={printer.name}, model={printer.model}, "
            f"ip={printer.ip_address}, file={remote_filename}. "
            "Check logs above for storage diagnostics and specific error codes."
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to upload file to printer. Check if SD card is inserted and properly formatted (FAT32/exFAT). "
            "See server logs for detailed diagnostics.",
        )

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
async def get_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
):
    """Get a file by ID with full details."""
    result = await db.execute(
        select(LibraryFile).options(selectinload(LibraryFile.created_by)).where(LibraryFile.id == file_id)
    )
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

    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # Ownership check
    if not can_modify_all:
        if file.created_by_id != user.id:
            raise HTTPException(status_code=403, detail="You can only update your own files")

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
    """Delete a file."""
    user, can_modify_all = auth_result

    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # Ownership check
    if not can_modify_all:
        if file.created_by_id != user.id:
            raise HTTPException(status_code=403, detail="You can only delete your own files")

    # Delete actual files
    try:
        abs_file_path = to_absolute_path(file.file_path)
        abs_thumb_path = to_absolute_path(file.thumbnail_path)
        if abs_file_path and abs_file_path.exists():
            abs_file_path.unlink()
        if abs_thumb_path and abs_thumb_path.exists():
            abs_thumb_path.unlink()
    except Exception as e:
        logger.warning(f"Failed to delete file from disk: {e}")

    await db.delete(file)

    return {"status": "success", "message": "File deleted"}


# ============ File Content Endpoints ============


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
):
    """Download a file."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
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
async def get_thumbnail(file_id: int, db: AsyncSession = Depends(get_db)):
    """Get a file's thumbnail."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
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
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
):
    """Get gcode for a file (for preview)."""
    result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    abs_path = to_absolute_path(file.file_path)
    if not abs_path or not abs_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    if file.file_type == "gcode":
        return FastAPIFileResponse(str(abs_path), media_type="text/plain")
    elif file.file_type == "3mf":
        # Extract gcode from 3mf
        import zipfile

        try:
            with zipfile.ZipFile(str(abs_path), "r") as zf:
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

    Files not owned by the user are skipped (unless user has *_all permission).
    """
    user, can_modify_all = auth_result

    # Verify folder exists if specified
    if data.folder_id is not None:
        folder_result = await db.execute(select(LibraryFolder).where(LibraryFolder.id == data.folder_id))
        if not folder_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Folder not found")

    # Update files
    moved = 0
    skipped = 0
    for file_id in data.file_ids:
        result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
        file = result.scalar_one_or_none()
        if file:
            # Ownership check
            if not can_modify_all and file.created_by_id != user.id:
                skipped += 1
                continue
            file.folder_id = data.folder_id
            moved += 1

    return {"status": "success", "moved": moved, "skipped": skipped}


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

    # Delete files first
    for file_id in data.file_ids:
        result = await db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
        file = result.scalar_one_or_none()
        if file:
            # Ownership check
            if not can_modify_all and file.created_by_id != user.id:
                skipped_files += 1
                continue

            try:
                abs_file_path = to_absolute_path(file.file_path)
                abs_thumb_path = to_absolute_path(file.thumbnail_path)
                if abs_file_path and abs_file_path.exists():
                    abs_file_path.unlink()
                if abs_thumb_path and abs_thumb_path.exists():
                    abs_thumb_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete file from disk: {e}")
            await db.delete(file)
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
                select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder_id)
            )
            deleted_files += file_count_result.scalar() or 0
            await db.delete(folder)
            deleted_folders += 1

    return BulkDeleteResponse(deleted_files=deleted_files, deleted_folders=deleted_folders)


# ============ Stats Endpoint ============


@router.get("/stats")
async def get_library_stats(
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
):
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
