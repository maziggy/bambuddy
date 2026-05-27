"""Print-file filename validation matching Bambu Studio's save-dialog rules.

The Bambu printer SD card is FAT32/exFAT. Names containing the Windows /
DOS-reserved set (``< > : " / \\ | ? *``), ASCII control characters
(0x00-0x1F), or trailing dots / spaces cannot be created on it — FTP fails
with ``553 Could not create file`` (#1540). Bambu Studio refuses to save
such names client-side; Bambuddy now does the same at the rename, upload,
and dispatch boundaries so the failure surfaces with a clear message
instead of an obscure FTP error after the user has already hit Print.
"""

INVALID_FILENAME_CHARS = '<>:"/\\|?*'

# FAT/exFAT cap on a single path component; UTF-8 byte length, not codepoints,
# because that is what the on-disk encoding limit actually is.
MAX_FILENAME_BYTES = 255


class InvalidFilenameError(ValueError):
    """Filename contains characters or shape the printer SD card rejects.

    ``char`` is the first offending character when the failure is a
    character-set violation, or ``None`` for structural failures (empty,
    bare ``.``, trailing space, too long, etc.). The frontend echoes it
    back to the user in the Bambu Studio-style error message.
    """

    def __init__(self, message: str, char: str | None = None):
        super().__init__(message)
        self.char = char


def validate_print_filename(name: str) -> None:
    """Raise ``InvalidFilenameError`` if ``name`` would fail on the SD card.

    Matches Bambu Studio's save-dialog rejection set. Callers are expected
    to translate the exception into an HTTP 400 (or a clean dispatch
    rejection); the message is intentionally short and ASCII so it fits
    a translation template.
    """
    if not name or not name.strip():
        raise InvalidFilenameError("Filename cannot be empty")

    if name in (".", ".."):
        raise InvalidFilenameError("Filename cannot be '.' or '..'")

    for ch in name:
        if ch in INVALID_FILENAME_CHARS:
            raise InvalidFilenameError(f"Filename contains invalid character: {ch}", char=ch)
        if ord(ch) < 0x20:
            raise InvalidFilenameError("Filename contains a control character", char=ch)

    if name.endswith(" ") or name.endswith("."):
        raise InvalidFilenameError("Filename cannot end with a space or dot")

    if len(name.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise InvalidFilenameError(f"Filename exceeds {MAX_FILENAME_BYTES} bytes")
