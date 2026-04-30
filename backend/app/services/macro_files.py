"""Raw file I/O for .cfg macro files.

Each .cfg file may contain multiple [macro name] blocks.
This service only handles disk operations — parsing and DB sync
is handled by macro_cfg_parser and macro_cfg_watcher respectively.
"""

import re
from pathlib import Path

from backend.app.core.config import settings as app_settings


def _macros_dir() -> Path:
    d = Path(app_settings.macros_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_path(macros_dir: Path, relative: str) -> Path:
    """Resolve path and reject anything that escapes macros_dir."""
    full = (macros_dir / relative).resolve()
    if not str(full).startswith(str(macros_dir.resolve()) + "/") and full != macros_dir.resolve():
        raise ValueError(f"Path traversal rejected: {relative!r}")
    return full


def _slug(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug or "macros"


def read(relative_path: str) -> str:
    """Read and return the raw text of a .cfg file."""
    d = _macros_dir()
    full = _safe_path(d, relative_path)
    if not full.exists():
        raise FileNotFoundError(f"Macro cfg file not found: {relative_path}")
    return full.read_text(encoding="utf-8")


def write(relative_path: str, content: str) -> None:
    """Overwrite an existing .cfg file with new content."""
    d = _macros_dir()
    full = _safe_path(d, relative_path)
    full.write_text(content, encoding="utf-8")


def create(name: str, content: str = "") -> str:
    """Create a new .cfg file. Returns the relative path.

    Derives filename from name slug, avoids collisions by appending a counter.
    """
    d = _macros_dir()
    base = _slug(name)
    candidate = Path(f"{base}.cfg")
    counter = 1
    while (d / candidate).exists():
        candidate = Path(f"{base}_{counter}.cfg")
        counter += 1
    (d / candidate).write_text(content, encoding="utf-8")
    return str(candidate)


def delete(relative_path: str) -> None:
    """Delete a .cfg file. Silently ignores missing files."""
    try:
        full = _safe_path(_macros_dir(), relative_path)
        full.unlink(missing_ok=True)
    except ValueError:
        pass  # traversal attempt on delete is a no-op


def list_cfg_files() -> list[str]:
    """Return relative paths of all .cfg files in the macros directory."""
    d = _macros_dir()
    return [str(p.relative_to(d)) for p in sorted(d.glob("*.cfg"))]
