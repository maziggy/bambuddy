"""Service for managing external directory mounts in the file manager."""

import asyncio
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.core.config import settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def is_safe_path(path: Path, allowed_bases: list[Path]) -> bool:
    """Verify path is within allowed base directories.

    Uses resolve().relative_to() pattern for security.
    """
    try:
        resolved = path.resolve()
        for base in allowed_bases:
            base_resolved = base.resolve()
            try:
                resolved.relative_to(base_resolved)
                return True
            except ValueError:
                continue
        return False
    except Exception:
        return False


def is_system_directory(path: Path) -> bool:
    """Check if path is a sensitive system directory."""
    system_dirs = ["/etc", "/sys", "/proc", "/dev", "/root", "/boot", "/var", "/usr", "/bin", "/sbin"]
    try:
        resolved = path.resolve()
        for sys_dir in system_dirs:
            if str(resolved).startswith(sys_dir):
                return True
    except Exception:
        pass
    return False


def parse_extensions_filter(extensions_str: str | None) -> list[str] | None:
    """Parse comma-separated extensions.

    Input: ".3mf,.stl,.gcode" or "3mf, stl"
    Output: [".3mf", ".stl", ".gcode"]
    """
    if not extensions_str:
        return None

    extensions = []
    for ext in extensions_str.split(","):
        ext = ext.strip().lower()
        if ext:
            if not ext.startswith("."):
                ext = "." + ext
            extensions.append(ext)

    return extensions if extensions else None


async def get_allowed_paths(db: AsyncSession) -> list[Path]:
    """Get allowed paths from settings, parsed and validated."""
    from backend.app.api.routes.settings import get_settings

    try:
        app_settings = await get_settings(db)
        allowed_str = app_settings.external_library_allowed_paths
        paths = [Path(p.strip()) for p in allowed_str.split(",") if p.strip()]
        return paths
    except Exception as e:
        logger.error(f"Failed to get allowed paths from settings: {e}")
        return [Path("/mnt/external")]


class ExternalLibraryService:
    """Service for managing external directory mounts."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def validate_external_path(self, path_str: str) -> tuple[bool, str]:
        """Validate external path against security rules and allowed paths.

        Security checks:
        - Path must be absolute
        - Path must exist and be a directory
        - Path must be within allowed_paths from settings
        - Path must not be a system directory
        - Path must be readable
        - Resolve symlinks and validate final location

        Returns: (is_valid, error_message)
        """
        try:
            path = Path(path_str)

            # Check if absolute
            if not path.is_absolute():
                return False, "Path must be absolute"

            # Check if exists
            if not await asyncio.to_thread(path.exists):
                return False, "Path does not exist"

            # Check if directory
            if not await asyncio.to_thread(path.is_dir):
                return False, "Path is not a directory"

            # Check if system directory
            if is_system_directory(path):
                return False, "Cannot mount system directories"

            # Check allowed paths
            allowed = await get_allowed_paths(self.db)
            if not is_safe_path(path, allowed):
                return False, f"Path is not within allowed directories: {[str(p) for p in allowed]}"

            # Check readability
            if not await asyncio.to_thread(os.access, path, os.R_OK):
                return False, "Path is not readable"

            logger.info(f"External path validation successful: {path}")
            return True, ""

        except Exception as e:
            logger.error(f"Error validating external path {path_str}: {e}")
            return False, f"Validation error: {str(e)}"

    async def scan_external_folder(self, folder_id: int, force_rescan: bool = False) -> dict[str, int]:
        """Scan external folder and sync database with filesystem state.

        Smart refresh logic:
        - Check directory mtime
        - Skip scan if mtime unchanged (unless force_rescan=True)
        - Recursively enumerate files (respecting max_depth, hidden files, extensions)
        - Add new files, update modified files, mark deleted files
        - Batch database commits (every 100 files)
        - Update folder.external_last_scan and folder.external_dir_mtime

        Returns: {"added": X, "updated": Y, "removed": Z}
        """
        # Get folder
        result = await self.db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
        folder = result.scalar_one_or_none()

        if not folder or not folder.is_external or not folder.external_path:
            return {"added": 0, "updated": 0, "removed": 0}

        folder_path = Path(folder.external_path)

        # Check if path still exists
        if not await asyncio.to_thread(folder_path.exists):
            logger.warning(f"External folder path no longer exists: {folder.external_path}")
            return {"added": 0, "updated": 0, "removed": 0}

        # Check mtime for smart refresh
        try:
            current_mtime = int((await asyncio.to_thread(folder_path.stat)).st_mtime)
        except Exception as e:
            logger.error(f"Failed to get folder mtime: {e}")
            return {"added": 0, "updated": 0, "removed": 0}

        if not force_rescan and folder.external_dir_mtime == current_mtime:
            # Directory hasn't changed, skip scan
            logger.debug(f"Skipping rescan of folder {folder_id} - mtime unchanged")
            return {"added": 0, "updated": 0, "removed": 0}

        # Get settings for scan
        from backend.app.api.routes.settings import get_settings

        app_settings = await get_settings(self.db)
        max_depth = app_settings.external_library_max_scan_depth

        # Enumerate files
        files_found = await self.enumerate_files_recursive(
            folder_path,
            folder.external_show_hidden,
            parse_extensions_filter(folder.external_extensions),
            max_depth,
        )

        # Get existing files in database
        result = await self.db.execute(
            select(LibraryFile).where(
                and_(
                    LibraryFile.folder_id == folder_id,
                    LibraryFile.is_external == True,  # noqa: E712
                )
            )
        )
        existing_files = {f.filename: f for f in result.scalars()}

        # Track changes
        added = 0
        updated = 0
        files_added = set()

        # Process found files
        for file_data in files_found:
            filename = file_data["relative_path"]
            file_path = folder_path / filename

            if filename in existing_files:
                # Update if mtime changed
                existing = existing_files[filename]
                if existing.external_mtime != file_data["mtime"]:
                    existing.file_size = file_data["size"]
                    existing.external_mtime = file_data["mtime"]
                    existing.updated_at = datetime.utcnow()
                    updated += 1
                    logger.debug(f"Updated external file: {filename}")
            else:
                # Add new file
                new_file = LibraryFile(
                    folder_id=folder_id,
                    filename=filename,
                    file_path=str(file_path),  # Absolute path for external files
                    file_type=filename.split(".")[-1].lower() if "." in filename else "unknown",
                    file_size=file_data["size"],
                    file_hash=None,  # Don't hash external files
                    is_external=True,
                    external_mtime=file_data["mtime"],
                    created_at=datetime.utcnow(),
                )
                self.db.add(new_file)
                added += 1
                files_added.add(filename)
                logger.debug(f"Added external file: {filename}")

            # Batch commit every 100 files
            if (added + updated) % 100 == 0:
                await self.db.commit()

        # Find removed files
        removed = 0
        for filename, existing_file in existing_files.items():
            if filename not in files_added and filename not in {f["relative_path"] for f in files_found}:
                await self.db.delete(existing_file)
                removed += 1
                logger.debug(f"Removed external file: {filename}")

            # Batch commit
            if removed % 100 == 0:
                await self.db.commit()

        # Update folder metadata
        folder.external_last_scan = datetime.utcnow()
        folder.external_dir_mtime = current_mtime
        folder.updated_at = datetime.utcnow()

        await self.db.commit()

        logger.info(f"Scanned external folder {folder_id}: +{added}, ~{updated}, -{removed}")
        return {"added": added, "updated": updated, "removed": removed}

    async def enumerate_files_recursive(
        self,
        base_path: Path,
        show_hidden: bool,
        extensions: list[str] | None,
        max_depth: int,
        current_depth: int = 0,
    ) -> list[dict]:
        """Recursively enumerate files in directory.

        Returns: List of dicts with keys: relative_path, size, mtime
        Filters: dotfiles, extensions, max_depth
        """
        files = []

        if current_depth >= max_depth:
            return files

        try:
            entries = await asyncio.to_thread(base_path.iterdir)
        except PermissionError:
            logger.warning(f"Permission denied accessing {base_path}")
            return files

        for entry in entries:
            try:
                # Skip hidden files if not showing them
                if not show_hidden and entry.name.startswith("."):
                    continue

                if await asyncio.to_thread(entry.is_dir):
                    # Recurse into subdirectory
                    subfiles = await self.enumerate_files_recursive(
                        entry, show_hidden, extensions, max_depth, current_depth + 1
                    )
                    files.extend(subfiles)
                elif await asyncio.to_thread(entry.is_file):
                    # Check extension filter
                    if extensions:
                        file_ext = "".join(entry.suffixes).lower()
                        if not any(file_ext.endswith(ext) or ("." + entry.suffix.lower()) in extensions for ext in extensions):
                            continue

                    # Get file stats
                    try:
                        stat = await asyncio.to_thread(entry.stat)
                        rel_path = entry.relative_to(base_path)
                        files.append(
                            {
                                "relative_path": str(rel_path),
                                "size": stat.st_size,
                                "mtime": int(stat.st_mtime),
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to stat file {entry}: {e}")

            except Exception as e:
                logger.warning(f"Error processing entry {entry}: {e}")

        return files

    async def get_external_file_path(self, file_id: int) -> Path | None:
        """Get validated filesystem path for external file.

        Security: Validates path is still within allowed directories
        Returns: Path object or None if file deleted/inaccessible
        """
        result = await self.db.execute(select(LibraryFile).where(LibraryFile.id == file_id))
        file = result.scalar_one_or_none()

        if not file or not file.is_external:
            return None

        file_path = Path(file.file_path)

        # Validate path is still allowed
        allowed = await get_allowed_paths(self.db)
        if not is_safe_path(file_path, allowed):
            logger.warning(f"External file path no longer in allowed directories: {file_path}")
            return None

        # Check if file still exists
        if not await asyncio.to_thread(file_path.exists):
            logger.warning(f"External file no longer exists: {file_path}")
            return None

        return file_path

    async def generate_external_thumbnail(self, file_path: Path, file_type: str) -> str | None:
        """Generate and cache thumbnail for external file.

        Stores in: /archive/library/thumbnails/external/{hash}.png
        Uses file hash for deduplication
        Returns: Relative path to thumbnail or None
        """
        try:
            # For now, just return None - thumbnail generation would be complex
            # and depends on 3MF parsing library
            # This can be enhanced later
            return None
        except Exception as e:
            logger.error(f"Failed to generate thumbnail for {file_path}: {e}")
            return None

    async def validate_operation_allowed(self, folder_id: int, operation: str) -> tuple[bool, str]:
        """Check if operation (upload, delete, rename) is allowed on external folder.

        Returns: (is_allowed, error_message)
        """
        result = await self.db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))
        folder = result.scalar_one_or_none()

        if not folder or not folder.is_external:
            return True, ""

        if folder.external_readonly and operation in ["upload", "delete", "rename"]:
            return False, f"Cannot {operation} files in read-only external folder"

        return True, ""

    async def check_directory_changed(self, folder: LibraryFolder) -> bool:
        """Check if external directory mtime changed since last scan.

        Smart refresh optimization
        """
        if not folder.is_external or not folder.external_path:
            return False

        try:
            folder_path = Path(folder.external_path)
            if not await asyncio.to_thread(folder_path.exists):
                return False

            current_mtime = int((await asyncio.to_thread(folder_path.stat)).st_mtime)
            return folder.external_dir_mtime != current_mtime
        except Exception:
            return False
