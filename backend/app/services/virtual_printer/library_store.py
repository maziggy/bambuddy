"""Store a virtual printer's upload in the File Manager.

Every server mode keeps what the slicer sends in the File Manager: library
mode right away, review mode once the upload is approved, queue mode for the
queue items that print it. The archive only gets a file once it prints.
"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.library import LibraryFile, LibraryFolder, LibraryTag
from backend.app.models.print_queue import PrintQueueItem

logger = logging.getLogger(__name__)

SOURCE_TYPE = "virtual_printer"
DEFAULT_FOLDER_NAME = "Virtual Printer"


async def upload_folder(db: AsyncSession, folder_id: int | None) -> LibraryFolder:
    """The folder a virtual printer's uploads land in.

    Its configured folder, or the shared top-level "Virtual Printer" folder
    when it has none, the folder was deleted, or it is an external folder (an
    external mount refuses a second file of the same name, and slicer names
    like "Plate_1.gcode.3mf" repeat). Only a shared or ownerless folder of
    that name counts: a user's private one of the same name would take every
    such upload out of everyone else's sight (#3201). None yet: one is made.
    """
    if folder_id is not None:
        folder = await db.get(LibraryFolder, folder_id)
        if folder is not None and not folder.is_external:
            return folder
        logger.warning("Virtual printer folder %s is gone or external; using '%s'", folder_id, DEFAULT_FOLDER_NAME)
    shared = (
        await db.execute(
            select(LibraryFolder)
            .where(
                LibraryFolder.name == DEFAULT_FOLDER_NAME,
                LibraryFolder.parent_id.is_(None),
                LibraryFolder.is_external.is_(False),
                LibraryFolder.shared.is_(True) | LibraryFolder.created_by_id.is_(None),
            )
            .order_by(LibraryFolder.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if shared is not None:
        return shared
    folder = LibraryFolder(name=DEFAULT_FOLDER_NAME, parent_id=None, created_by_id=None, shared=True)
    db.add(folder)
    await db.flush()
    return folder


async def _apply_tags(db: AsyncSession, library_file: LibraryFile, tags: str | None) -> None:
    """Attach the comma-separated ``tags`` (review mode's field), creating missing ones."""
    from backend.app.api.routes.library_tags import _name_key

    names = []
    for raw in (tags or "").split(","):
        name = raw.strip()[:64]
        if name and _name_key(name) not in {_name_key(n) for n in names}:
            names.append(name)
    if not names:
        return
    await db.refresh(library_file, ["tags"])
    have = {tag.name_key for tag in library_file.tags}
    for name in names:
        key = _name_key(name)
        if key in have:
            continue
        tag = (await db.execute(select(LibraryTag).where(LibraryTag.name_key == key))).scalar_one_or_none()
        if tag is None:
            tag = LibraryTag(name=name, name_key=key)
            db.add(tag)
            await db.flush()
        library_file.tags.append(tag)
        have.add(key)


async def upload_settings(db: AsyncSession, vp_id: int) -> tuple[int | None, bool]:
    """``(library_folder_id, queue_remove_after_print)`` of a virtual printer.

    Read at each upload rather than held by the running instance, so changing
    them takes effect without restarting the virtual printer.
    """
    from backend.app.models.virtual_printer import VirtualPrinter

    row = (
        await db.execute(
            select(VirtualPrinter.library_folder_id, VirtualPrinter.queue_remove_after_print).where(
                VirtualPrinter.id == vp_id
            )
        )
    ).one_or_none()
    if row is None:
        return None, False
    return row[0], bool(row[1])


async def store_upload(
    db: AsyncSession,
    *,
    source_path: Path,
    filename: str,
    folder_id: int | None,
    owner_id: int | None = None,
    notes: str | None = None,
    project_id: int | None = None,
    tags: str | None = None,
    reuse: bool = True,
) -> tuple[LibraryFile, bool]:
    """Put an upload into the File Manager; returns ``(file, reused)``.

    The same file sent again under the same name into the same folder is the
    file already there (``reused``), so sending a plate twice does not leave
    two copies; a queue gets a job for each send all the same. Only files a
    virtual printer stored count: a file someone put there by hand is never
    taken over, and neither is another sender's (``owner_id``, the user whose
    personal access code sent it, #1430). Notes, project and tags are applied
    to the file either way.

    A file a queue job removes once it prints ("Remove from File Manager once
    it prints") is never shared: such a job asks for a file of its own
    (``reuse=False``), and no other upload takes that one, or the job's print
    would remove a file someone else stored to keep. Commits.
    """
    from backend.app.api.routes.library import save_3mf_bytes_to_library
    from backend.app.services.archive import ArchiveService

    folder = await upload_folder(db, folder_id)
    file_hash = await asyncio.to_thread(ArchiveService.compute_file_hash, source_path)
    # The job that removes this file at dispatch: not a file to share.
    removed_on_print = exists().where(
        PrintQueueItem.library_file_id == LibraryFile.id,
        PrintQueueItem.cleanup_library_after_dispatch.is_(True),
    )
    existing = (
        None
        if not reuse
        else (
            await db.execute(
                LibraryFile.active()
                .where(
                    LibraryFile.folder_id == folder.id,
                    LibraryFile.filename == filename,
                    LibraryFile.file_hash == file_hash,
                    LibraryFile.source_type == SOURCE_TYPE,
                    ~removed_on_print,
                    # Only the same sender's: someone else's copy stays theirs.
                    LibraryFile.created_by_id.is_(None) if owner_id is None else LibraryFile.created_by_id == owner_id,
                )
                .order_by(LibraryFile.id)
                .limit(1)
            )
        ).scalar_one_or_none()
    )
    if existing is not None:
        library_file, reused = existing, True
    else:
        library_file, reused = await save_3mf_bytes_to_library(
            db,
            source_path=source_path,
            filename=filename,
            folder_id=folder.id,
            source_type=SOURCE_TYPE,
            owner_id=owner_id,
            # No source URL: duplicates are found by content above.
            dedupe_within=None,
        )
    if notes:
        library_file.notes = notes
    if project_id is not None:
        library_file.project_id = project_id
    await _apply_tags(db, library_file, tags)
    await db.commit()
    return library_file, reused
