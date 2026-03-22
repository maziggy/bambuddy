import re
from pathlib import Path


def _read_app_version() -> str:
    """Read APP_VERSION from backend config — single source of truth."""
    config_path = Path(__file__).resolve().parent.parent.parent / "backend" / "app" / "core" / "config.py"
    try:
        text = config_path.read_text()
        match = re.search(r'^APP_VERSION\s*=\s*["\'](.+?)["\']', text, re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    return "0.0.0"


__version__ = _read_app_version()
