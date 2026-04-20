"""File storage service for macro .jinja2 scripts.

Macros are stored as real files on disk so they can be version-controlled,
shared, and edited externally. The DB record holds metadata and a relative
file path; this service handles all file I/O.
"""

import re
from pathlib import Path

from backend.app.core.config import settings as app_settings


def _slug(name: str) -> str:
    """Convert a macro name to a safe filename slug."""
    slug = name.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug or "macro"


def _macros_dir() -> Path:
    d = Path(app_settings.macros_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def read(file_path: str) -> str:
    """Read and return the script content of a macro file."""
    full = _macros_dir() / file_path
    if not full.exists():
        raise FileNotFoundError(f"Macro file not found: {file_path}")
    return full.read_text(encoding="utf-8")


def write(name: str, content: str, existing_path: str | None = None) -> str:
    """Write macro content to disk.

    If existing_path is given, overwrites that file (update case).
    Otherwise creates a new uniquely-named file. Returns the relative path.
    """
    d = _macros_dir()
    if existing_path:
        full = d / existing_path
        full.write_text(content, encoding="utf-8")
        return existing_path
    # New file — derive name from slug, avoid collisions
    base = _slug(name)
    candidate = Path(f"{base}.jinja2")
    counter = 1
    while (d / candidate).exists():
        candidate = Path(f"{base}_{counter}.jinja2")
        counter += 1
    (d / candidate).write_text(content, encoding="utf-8")
    return str(candidate)


def delete(file_path: str) -> None:
    """Delete a macro file. Silently ignores missing files."""
    full = _macros_dir() / file_path
    full.unlink(missing_ok=True)


def list_files() -> list[str]:
    """Return relative paths of all .jinja2 files in the macros directory."""
    d = _macros_dir()
    return [str(p.relative_to(d)) for p in sorted(d.glob("*.jinja2"))]
