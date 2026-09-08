"""Where ad-hoc notification snapshots live on disk.

Most events (first layer complete, plate not empty, printer errors, ...) hand
their captured camera frame straight to providers as raw bytes. Home Assistant
and Bark need an HTTP URL instead, since they fetch it themselves, so those
bytes have to land somewhere servable first.

These aren't tied to a PrintArchive (plate-not-empty runs before one exists)
and shouldn't show up in an archive's photo gallery, so they get their own
flat directory instead of reusing archive_paths.py. Nothing links to them
beyond the notification that triggered the capture, so they're just pruned by
age on write rather than tracked in the database.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.utils.safe_path import PathTraversalError, safe_join_under

logger = logging.getLogger(__name__)

# These only need to survive long enough for a provider to fetch them once
# after the notification goes out, so a few days of slack is plenty.
_MAX_AGE_SECONDS = 3 * 24 * 60 * 60  # 3 days


def notification_photos_dir() -> Path:
    return settings.base_dir / "notification_photos"  # SEC-PATH-OK: constant subdirectory


def _prune_old_photos(directory: Path) -> None:
    """Best-effort deletion of files older than ``_MAX_AGE_SECONDS``.

    Failures here must never block a notification from sending, so every
    error is swallowed after a debug log.
    """
    try:
        cutoff = time.time() - _MAX_AGE_SECONDS
        for entry in directory.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue
    except OSError as e:
        logger.debug("Failed to prune notification photos: %s", e)


def save_notification_photo(image_data: bytes, event_type: str) -> str:
    """Write *image_data* to the notification photos dir and return its filename.

    Runs synchronously — callers on the async path should wrap this in
    ``asyncio.to_thread``.
    """
    directory = notification_photos_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _prune_old_photos(directory)

    safe_event = "".join(c for c in event_type if c.isalnum() or c == "_") or "event"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_event}_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
    path = directory / filename  # SEC-PATH-OK: filename generated above, not user input
    path.write_bytes(image_data)
    return filename


def find_notification_photo(filename: str) -> Path | None:
    """Resolve *filename* under the notification photos dir, or None if missing/unsafe."""
    try:
        candidate = safe_join_under(notification_photos_dir(), filename, http=False)
    except PathTraversalError:
        return None
    return candidate if candidate.exists() else None
