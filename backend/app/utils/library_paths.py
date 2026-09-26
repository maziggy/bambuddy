"""Where a library file's user photos live on disk (#3077).

Photos are Bambuddy-side metadata, so they sit inside the library data dir
regardless of whether the file itself is managed or external:
``<archive_dir>/library/photos/<file_id>/``. The routes, the trash sweeper,
the external-folder scan and the dispatch cleanup all derive the directory
from here — see ``archive_paths`` for why one path derived in several places
is a bug waiting to happen.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from backend.app.core.config import settings
from backend.app.utils.safe_path import PathTraversalError, safe_join_under

logger = logging.getLogger(__name__)


def library_photos_dir(file_id: int) -> Path:
    """The photo directory for library file *file_id* (not created)."""
    library_dir = Path(settings.archive_dir) / "library"
    return library_dir / "photos" / str(file_id)  # SEC-PATH-OK: file_id is an int primary key


def remove_library_photos_dir(file_id: int) -> None:
    """Best-effort removal of a file's photo directory and everything in it."""
    photos_dir = library_photos_dir(file_id)
    if not photos_dir.is_dir():
        return
    try:
        shutil.rmtree(photos_dir)
    except OSError as e:
        logger.warning("Failed to remove library photos dir %s: %s", photos_dir, e)


def move_library_photos(file_id: int, photos: Sequence[str], destination: Path) -> list[str]:
    """Move a library file's photos into *destination*, emptying its directory.

    Used where a library row is consumed by the archive that replaces it
    (``cleanup_library_after_dispatch``): the photos follow the file instead
    of being orphaned under an id nothing points at any more. Returns the
    names the photos ended up under, in order — a name already taken in
    *destination* gets a fresh one, because both sides draw photo names from
    the same 8-hex-digit alphabet.

    Best-effort: a photo that cannot be moved is left out of the returned
    list, so it is never named by an archive that does not have it. The
    caller is mid-dispatch and has nowhere to report to. The source
    directory is only removed once everything in the list did move, so a
    failure orphans the pictures rather than destroying them.
    """
    source_dir = library_photos_dir(file_id)
    if not source_dir.is_dir():
        return []
    moved: list[str] = []
    failed = False
    for filename in photos:
        try:
            source = safe_join_under(source_dir, filename, http=False)
        except PathTraversalError:
            failed = True
            continue
        if not source.is_file():
            continue
        target_name = filename
        try:
            destination.mkdir(parents=True, exist_ok=True)
            target = safe_join_under(destination, target_name, http=False)
            if target.exists():
                target_name = f"{uuid.uuid4().hex[:8]}{source.suffix.lower()}"
                target = destination / target_name  # SEC-PATH-OK: uuid.uuid4().hex[:8] + suffix
            shutil.move(str(source), str(target))
        except (OSError, PathTraversalError) as e:
            logger.warning("Failed to move library photo %s to %s: %s", source, destination, e)
            failed = True
            continue
        moved.append(target_name)
    if failed:
        logger.warning("Kept library photos dir %s: not every photo reached %s", source_dir, destination)
    else:
        remove_library_photos_dir(file_id)
    return moved
