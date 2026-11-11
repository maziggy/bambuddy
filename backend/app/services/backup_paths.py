"""Make file paths from a Windows backup usable on Linux and macOS (#3501).

Bambuddy stores file locations relative to its data folder, written with the
platform's separator: a Windows install stores ``archive\\12\\print.3mf``.
Windows reads either separator, Linux and macOS read the backslashes as part
of one file name, so after a Windows backup is restored there every archive,
library file and print log entry points at nothing.

Runs once at the end of a restore, never at startup.
"""

import logging
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# Columns holding a path relative to the data folder. Photos and project
# attachments store bare file names; pending uploads and external folders
# hold absolute host paths, which mean nothing on another machine anyway.
PATH_COLUMNS: dict[str, tuple[str, ...]] = {
    "print_archives": ("file_path", "thumbnail_path", "timelapse_path", "source_3mf_path", "f3d_path"),
    "library_files": ("file_path", "thumbnail_path"),
    "print_log_entries": ("thumbnail_path", "timelapse_path"),
}


async def convert_windows_paths(conn: AsyncConnection, base_dir: Path, *, windows: bool | None = None) -> int:
    """Turn the backslashes in stored paths into slashes; returns the rows changed.

    A path that exists as written is left alone: on Linux a backslash can be
    part of a real file name. Nothing happens on Windows, where backslashes
    are the separator.
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return 0

    changed = 0
    for table, columns in PATH_COLUMNS.items():
        # Filtered here rather than with LIKE: Postgres reads a backslash in a
        # LIKE pattern as an escape, SQLite does not.
        # SQL from the table and column names in PATH_COLUMNS only; values are bound.
        rows = (await conn.execute(text(f"SELECT id, {', '.join(columns)} FROM {table}"))).all()  # nosec B608
        for row in rows:
            updates = {}
            for column, value in zip(columns, row[1:], strict=True):
                if not value or "\\" not in value:
                    continue
                # Only stat()ed, to decide whether to rewrite the row; nothing is read or written through it.
                if (base_dir / value).exists():  # SEC-PATH-OK: stat only, see above
                    continue
                updates[column] = value.replace("\\", "/")
            if updates:
                assignments = ", ".join(f"{column} = :{column}" for column in updates)
                # Column names from PATH_COLUMNS, values bound.
                await conn.execute(text(f"UPDATE {table} SET {assignments} WHERE id = :id"), {**updates, "id": row[0]})  # nosec B608
                changed += 1
    if changed:
        logger.info("Converted Windows file paths in %d restored row(s) to this platform's separator", changed)
    return changed
