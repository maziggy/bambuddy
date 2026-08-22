"""HTTP response helpers."""

from pathlib import Path
from urllib.parse import quote

from starlette.responses import PlainTextResponse


def download_error_response(status_code: int, message: str) -> PlainTextResponse:
    """Answer a browser-native download with a file that says what went wrong.

    These URLs are reached by an ``<a download>`` click, and a browser saves
    whatever comes back under the name it was going to use. A JSON error body
    therefore lands on the user's disk as a .zip that will not open, with
    nothing on screen to explain it -- the download simply appears to have
    produced a broken file. A short text file, named for the failure rather
    than for the download, is at least legible when opened.
    """

    return PlainTextResponse(
        f"{message}\n",
        status_code=status_code,
        headers={"Content-Disposition": build_content_disposition("download-failed.txt")},
    )


def safe_download_filename(filename: str, fallback: str = "download", max_chars: int = 200) -> str:
    """Return a basename safe for a bounded download response header."""

    basename = Path(filename.replace("\\", "/")).name
    cleaned = "".join("_" if ord(char) < 32 or ord(char) == 127 else char for char in basename).strip(" .")
    if not cleaned:
        return fallback
    if len(cleaned) <= max_chars:
        return cleaned
    suffixes = "".join(Path(cleaned).suffixes)
    suffix = suffixes if len(suffixes) <= 32 else ""
    stem_chars = max(1, max_chars - len(suffix))
    return f"{cleaned[:stem_chars]}{suffix}"


def build_content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Build an RFC 6266-compliant Content-Disposition header value.

    Starlette/uvicorn encodes response headers as latin-1, so any non-ASCII
    character in a raw `filename="..."` parameter raises UnicodeEncodeError.
    The fix is RFC 5987's `filename*=UTF-8''<percent-encoded>` form alongside
    a stripped ASCII fallback in the legacy `filename="..."` parameter — every
    modern browser prefers the `*` form when present.
    """
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii").strip(" ._-") or "download"
    ascii_fallback = ascii_fallback.replace('"', "").replace("\\", "")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
