"""The G-code fingerprint of an archive's file (#1634).

``content_hash`` is the SHA256 of the archived file's bytes. A slicer that sends
the same slice again writes a new 3MF around it (zip entry times, metadata), so
the two files differ in every byte but their G-code. ``gcode_hash`` is the
SHA256 of the G-code alone: every ``Metadata/plate_N.gcode`` of a 3MF in plate
order, each with its name and size, or the bytes of a plain ``.gcode`` file.

A print sent from the slicer whose G-code matches an archive adds a run to that
archive instead of archiving the file again (``main._claim_slicer_resend_archive``).
A file sliced again with other settings has other G-code and stays separate.
OrcaSlicer writes the slicing time into the G-code header, so with it only the
same slice sent again matches, not the same model sliced again.

``NO_GCODE`` ("") marks an archive read without G-code (no file, not a sliced
file, unreadable), so it is read once; NULL is not read yet. Reading unzips the
file, which takes seconds for a large 3MF: callers on the event loop go through
``asyncio.to_thread`` (``refresh_gcode_hash_async``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from backend.app.core.config import settings
from backend.app.models.archive import PrintArchive

logger = logging.getLogger(__name__)

NO_GCODE = ""

_PLATE_GCODE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
_CHUNK = 1 << 20


def gcode_fingerprint(path: Path) -> str | None:
    """SHA256 of the G-code in the file at ``path``, or None when it holds none
    or cannot be read."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                plates = sorted(
                    (int(m.group(1)), info)
                    for info in zf.infolist()
                    if (m := _PLATE_GCODE.match(info.filename)) and not info.is_dir()
                )
                if not plates:
                    return None
                digest = hashlib.sha256()
                for _number, info in plates:
                    # Name and length first: two files splitting the same bytes
                    # over other plates are not one.
                    digest.update(f"{info.filename}\0{info.file_size}\0".encode())
                    with zf.open(info) as handle:
                        for chunk in iter(lambda: handle.read(_CHUNK), b""):
                            digest.update(chunk)
                return digest.hexdigest()
        if path.suffix.lower() == ".gcode":
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(_CHUNK), b""):
                    digest.update(chunk)
            return digest.hexdigest()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, ValueError) as exc:
        logger.debug("G-code fingerprint of %s failed: %s", path, exc)
    return None


def read_gcode_hash(archive: Any) -> str | None:
    """The G-code fingerprint of the archive's file on disk: ``NO_GCODE`` for a
    file without readable G-code, None when there is no file to read."""
    if not archive.file_path:
        return None
    path = settings.base_dir / archive.file_path  # SEC-PATH-OK: archive.file_path is DB-stored, internally generated
    if not path.is_file():
        return None
    return gcode_fingerprint(path) or NO_GCODE


def refresh_gcode_hash(archive: PrintArchive) -> None:
    """Read the G-code fingerprint of the archive's file into it: when the
    archive is made, or given a file. Does not commit."""
    fingerprint = read_gcode_hash(archive)
    if fingerprint is not None:
        archive.gcode_hash = fingerprint


async def refresh_gcode_hash_async(archive: PrintArchive) -> None:
    """``refresh_gcode_hash`` with the read off the event loop."""
    fingerprint = await asyncio.to_thread(read_gcode_hash, archive)
    if fingerprint is not None:
        archive.gcode_hash = fingerprint


async def fill_gcode_hashes(session_factory, batch: int = 100) -> int:
    """Give every archive not yet read its G-code fingerprint, from its file.
    An archive whose file is missing or unreadable gets ``NO_GCODE``: it is
    read once. Returns how many were read.

    Each is written only while it is still unread: an archive given its file
    meanwhile (``refresh_gcode_hash``) keeps what that read."""
    filled = 0
    last_id = 0
    while True:
        async with session_factory() as db:
            rows = (
                await db.execute(
                    select(PrintArchive.id, PrintArchive.file_path)
                    .where(PrintArchive.gcode_hash.is_(None), PrintArchive.id > last_id)
                    .order_by(PrintArchive.id)
                    .limit(batch)
                )
            ).all()
            if not rows:
                return filled
            for row in rows:
                fingerprint = await asyncio.to_thread(read_gcode_hash, row)
                written = await db.execute(
                    update(PrintArchive)
                    .where(PrintArchive.id == row.id, PrintArchive.gcode_hash.is_(None))
                    .values(gcode_hash=fingerprint if fingerprint is not None else NO_GCODE)
                    .execution_options(synchronize_session=False)
                )
                filled += written.rowcount or 0
            last_id = rows[-1].id
            await db.commit()
