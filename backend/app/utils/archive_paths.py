"""Where an archive's files live on disk (#1820).

An archive normally owns a directory, derived from its ``file_path``:
``<base_dir>/<dirname of file_path>/``. An archive created without a 3MF has
``file_path == ""``, and ``Path("").parent`` is ``Path(".")`` -- so every site
that derived the directory that way silently resolved to ``base_dir`` itself,
and all such archives shared one pile.

The finish-photo capture path spotted that and used ``<archive_dir>/<id>/``
instead. Nothing else did, so a captured photo was written to one directory and
then looked for in another: the read 404'd, the delete removed the name and
left the file, and the notification attachment never found the image. Four
sites deriving the same directory four times is what let them drift, so they
now all ask here.

Photos written before this are still where they were put, which is why lookups
check both locations rather than only the current one.

Scope note: a *source 3MF* uploaded onto a no-3MF archive has its own layout,
``archive/no_source/<id>/``, chosen separately and stored in its own column.
This module does not model that -- do not reach for ``archive_dir`` to find one.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.core.config import settings
from backend.app.utils.safe_path import PathTraversalError, safe_join_under


def archive_dir(archive: object) -> Path:
    """The directory belonging to *archive*.

    Falls back to ``<archive_dir>/<id>`` for an archive with no 3MF, matching
    what the finish-photo capture has always written.
    """
    file_path = getattr(archive, "file_path", "") or ""
    if file_path:
        return settings.base_dir / Path(file_path).parent
    return settings.archive_dir / str(archive.id)  # SEC-PATH-OK: archive.id is an int primary key


def archive_photos_dir(archive: object) -> Path:
    """Where photos for *archive* are written."""
    return archive_dir(archive) / "photos"  # SEC-PATH-OK: constant subdirectory


def _legacy_shared_photos_dir(archive: object) -> Path | None:
    """Where a no-3MF archive's photos used to be read from, and uploaded to.

    ``<base_dir>/photos``, shared by every no-3MF archive at once. Only ever
    consulted for an archive that has no ``file_path``; one with a real path
    always resolved correctly and has no second location to check.
    """
    if getattr(archive, "file_path", "") or "":
        return None
    return settings.base_dir / "photos"  # SEC-PATH-OK: constant subdirectory


def _pre_recovery_photos_dir(archive: object) -> Path | None:
    """Where this archive's photos were written while it had no 3MF.

    An archive that started as a no-3MF fallback and was later filled in from a
    3MF that turned up (#2957) changes directory: ``archive_dir`` derives from
    ``file_path``, which goes from empty to a real path. Anything written to
    ``<archive_dir>/<id>/photos`` before that moment is still there, so it stays
    a lookup candidate afterwards -- the same reason
    :func:`_legacy_shared_photos_dir` exists.

    None while ``file_path`` is empty, where this *is* the current directory and
    the caller already checks it.
    """
    if not (getattr(archive, "file_path", "") or ""):
        return None
    return settings.archive_dir / str(archive.id) / "photos"  # SEC-PATH-OK: archive.id is an int primary key


def find_archive_photo(archive: object, filename: str) -> Path | None:
    """Locate an existing photo, or None if it is in neither location.

    *filename* must already have been checked for membership in
    ``archive.photos``; it is joined containment-checked regardless. A name
    that fails that check is treated as not found rather than raised on --
    one caller is a background notification task, where an HTTP error would
    have nowhere to go.
    """
    for directory in (
        archive_photos_dir(archive),
        _legacy_shared_photos_dir(archive),
        _pre_recovery_photos_dir(archive),
    ):
        if directory is None:
            continue
        try:
            candidate = safe_join_under(directory, filename, http=False)
        except PathTraversalError:
            return None
        if candidate.exists():
            return candidate
    return None
