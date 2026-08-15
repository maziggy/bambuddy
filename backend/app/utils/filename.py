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


def clean_display_name(name: str | None) -> str | None:
    """Tidy a free-text display name on the way into the database (#2832).

    A display name is allowed its punctuation: "Planter Pot with Drip Tray,
    12 cm / 5 inches" is a perfectly good title and refusing the slash would
    reject the very name this issue was reported about. What has no business
    in one is a control character or a NUL -- neither renders, both can
    truncate a string somewhere further down.

    Path safety is *not* enforced here, deliberately. It belongs at each point
    where a name becomes a path, because that is where the budget and the
    fallback differ; see ``safe_path_component``. This is tidying, not a
    boundary.

    Returns None unchanged, and None for a name that was only whitespace.

    Anything that is not a string is handed back untouched, so the schema this
    runs in front of still applies its own type check. Iterating it here instead
    would turn ``["a"]`` into the name ``"a"`` and a non-iterable into a 500,
    where the field is meant to answer with a 422.
    """
    if not isinstance(name, str):
        return name
    cleaned = "".join(ch for ch in name if ord(ch) >= 0x20 and ch != "\x7f").strip()
    return cleaned or None


def safe_path_component(name: str, *, fallback: str, max_bytes: int = MAX_FILENAME_BYTES) -> str:
    """Reduce a display name to something usable as one path component (#2832).

    A print's display name is not a filename. It comes from the ``print_name``
    embedded in the 3MF -- MakerWorld titles like "Planter Pot with Drip Tray,
    12 cm / 5 inches" arrive verbatim -- and several places build a directory or
    a file out of it. A ``/`` in such a name is a path separator: the join
    silently gains a level, ``mkdir(parents=True)`` creates it, and the write
    that follows fails on a parent that was never made. Worse, the name is
    user-controlled, so ``..`` segments in one steer the write out of the
    directory it was meant for.

    Every character the SD-card rules already reject is replaced rather than
    dropped, so the result still reads like the original: that set is exactly
    the separators plus the Windows-reserved punctuation, which a Windows
    install needs for the same reason Linux needs the separators. Leading and
    trailing dots and spaces go too -- ``..`` reduces to nothing rather than to
    a relative path -- and the result is capped to what one component may hold.

    Returns *fallback* when nothing usable survives, so a name made entirely of
    separators cannot produce an empty path component.

    *max_bytes* is the budget for this component alone. Callers that wrap the
    result in a prefix or an extension must subtract those, or the composed
    name can still exceed what the filesystem accepts.
    """
    cleaned = "".join("-" if (ch in INVALID_FILENAME_CHARS or ord(ch) < 0x20 or ch == "\x7f") else ch for ch in name)
    cleaned = cleaned.strip(" .")

    if len(cleaned.encode("utf-8")) > max_bytes:
        # Cut on the byte limit, then drop any partial character the cut left.
        cleaned = cleaned.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").strip(" .")

    return cleaned or fallback


def derive_remote_filename(filename: str) -> str:
    """Compute the SD-card filename used when uploading a sliced print file.

    Strips repeated trailing ``.gcode.3mf`` / ``.3mf`` suffixes until the
    bare stem remains, then appends a single ``.3mf``; spaces are
    replaced with underscores because the firmware parses
    ``ftp://{filename}`` as a URL.

    Canonical for both the dispatch uploader and the post-print SD
    cleanup — when the two drift apart the cleanup misses, and a
    library row whose stored filename ended up with a doubled
    ``.gcode.3mf`` (#1542) leaves the real file on the SD card. On A1
    firmware that lingering file becomes a ghost print on the next
    power-on (same family as the P1S behaviour in #374).

    Raises ``TypeError`` on non-string input rather than entering the
    strip loop, because a duck-typed object that returns truthy
    sentinels from ``endswith`` would never escape and the resulting
    unbounded allocation has cgroup-OOM'd the test runner under mocks.
    """
    if not isinstance(filename, str):
        raise TypeError(f"derive_remote_filename requires str, got {type(filename).__name__}")
    stem = filename
    while True:
        if stem.endswith(".gcode.3mf"):
            stem = stem[:-10]
        elif stem.endswith(".3mf"):
            stem = stem[:-4]
        else:
            break
    return f"{stem}.3mf".replace(" ", "_")
