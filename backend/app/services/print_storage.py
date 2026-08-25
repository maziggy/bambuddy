"""Can FTPS see the file this print is running from? (#2780)

Bambuddy reads a print's 3MF, cover and timelapse off the printer over implicit
FTPS on port 990. On every Bambu model that port serves **external storage only**
-- the SD card or USB stick. It is not a view of the printer's filesystem.

H2-series and P2S firmware default to keeping the sliced file on internal eMMC
instead, and BambuStudio uploads there over a separate service on port 6000
(the "BambuTunnelLocal" protocol -- see #2762, which tracks implementing it).
The dispatch says where it went: the ``project_file`` command carries ``url``,
which is ``ftp://<name>`` for external storage and ``brtc://emmc/<name>`` for
internal.

Before this module we ignored ``url`` and swept anyway: six filename variants
across five directories with up to four retries for the 3MF, then sixteen more
paths for the cover, then the timelapse scan -- roughly 110 FTPS connections per
print, every one of them certain to 550. The user-visible result was an archive
card with nothing on it and no stated reason, which read as a Bambuddy bug and
was reported as one four times (#1170, #2524, #2762, #2780).

But that URL is not the last word on reachability, and reading it as one was
itself a regression (#2856). It says where the printer *chose* to put the file,
not whether port 990 can serve it -- measured on an H2D (firmware 01.03.00.00,
card in the slot): every ``brtc://emmc/<name>`` print of that reporter's was
sitting under ``/cache/<name>`` and downloaded fine, 19 MB included, until this
module started skipping the lookup. On #2780's P2S and H2C the same URL really
did mean nothing was there. So an internal-storage URL earns a *bounded probe*
rather than a skip: it names the exact file, which turns the 110-connection
sweep into one connection walking five paths, and the answer comes from the
printer instead of from a guess about its model. Only when that probe misses
does the verdict's ``reason`` stand -- see :func:`probe_filename_from_url` and
:func:`ftp_probe_paths`, and the callers that run it.

The rule here is deliberately one-sided: **skip only on positive evidence**.
Silence is not evidence -- a printer that never publishes ``sdcard`` and never
had a ``project_file`` pass through the request topic (some brokers refuse the
subscription) must keep the old behaviour exactly, or this becomes a regression
for installs whose archives work fine today.
"""

from __future__ import annotations

from dataclasses import dataclass

# The scheme that means "uploaded to external storage, reachable over FTPS".
# An unknown scheme -- whatever Bambu ships next -- must not read as fine, so
# this matches the reachable value rather than the unreachable one.
_EXTERNAL_STORAGE_SCHEME = "ftp"

# ``file://`` means the file was already on the printer when the print started:
# a reprint from the touchscreen, from Handy, or a Studio send-to-storage
# followed by a print. The path says which storage, and only the printer's own
# internal roots are out of reach of port 990. Measured on an H2D, 2026-08-17:
# ``file:///media/usb0/foobar.gcode.3mf`` while that exact file was listable and
# downloadable over FTPS.
_LOCAL_FILE_SCHEME = "file"

# Internal roots seen in ``file://`` paths. ``/userdata`` is where the model
# cache lives (``/userdata/model/history/<name>``, confirmed via the printer's
# own file listing), and port 990 does not serve it.
_INTERNAL_FILE_PREFIXES = ("/userdata/",)

# Reason slugs. These cross the API into the UI and into the connection
# diagnostic, so they are part of the contract: the frontend maps each to its
# own explanation and its own advice. Keep them stable.
REASON_INTERNAL_STORAGE = "internal_storage"
REASON_NO_EXTERNAL_STORAGE = "no_external_storage"

# Same verdict as REASON_INTERNAL_STORAGE, different cause -- and the cause is
# the whole of the advice. `brtc://emmc/<name>` is a *dispatch* that chose
# internal storage: a slicer sent the file and the printer filed it where port
# 990 cannot serve it, which the operator can change by sending it elsewhere.
# `file:///userdata/...` is a print of a file that was already on the printer --
# a touchscreen re-print, a Handy start, a Studio send-to-storage printed later
# -- so there was no dispatch to aim anywhere, and telling that operator to pick
# "External" in Send describes a step they never took (#1820).
REASON_INTERNAL_HISTORY = "internal_history"

# Not a storage verdict — the file's location was never in question. The
# printer's FTPS service was inside its post-failed-handshake cool-off when the
# print started, so the sweep was skipped without a single connection. Stamped
# on the fallback archive by the print-start handler rather than returned by
# `_verdict`, and unlike the two above it is temporary: it is the one reason a
# retry is worth scheduling (#2957).
REASON_FTPS_COOLOFF = "ftps_cooloff"

# Where a sliced file has ever been found over FTPS, in the order the sweep in
# `main.py` tries them -- root first, which is where A1/P1-series uploads land
# (#972), then `/cache`, which is where the H2D keeps its copy of an eMMC job
# (#2856).
_PROBE_DIRECTORIES = ("/", "/cache/", "/model/", "/data/", "/data/Metadata/")

# Longest name worth probing for. Every filesystem the printer could be serving
# from caps a name at 255 bytes, so anything past this cannot be a file that is
# actually there -- and it would be written to a local temp path too.
_MAX_PROBE_FILENAME_LENGTH = 255


@dataclass(frozen=True)
class StorageVerdict:
    """Whether an FTPS sweep for this print's file is worth running.

    ``reachable`` False always carries a ``reason``; True never does.

    ``probe_filename`` is the exact name the dispatch gave, present only on an
    unreachable verdict and only when the URL named a ``.3mf``. It is the
    caller's chance to check the claim cheaply before acting on ``reason`` --
    see :func:`ftp_probe_paths`.
    """

    reachable: bool
    reason: str | None = None
    probe_filename: str | None = None


_REACHABLE = StorageVerdict(reachable=True)


def url_is_external_storage(project_url: str | None) -> bool | None:
    """Does *project_url* name a file on external storage?

    ``None`` when there is no URL to read, which is not the same answer as
    False and must not be collapsed into one by callers.
    """
    # Type-checked, not just truth-checked: this value arrives straight off the
    # wire, so it is whatever the sender put there. Anything that is not a
    # string is not an answer.
    if not isinstance(project_url, str) or not project_url:
        return None
    scheme, separator, path = project_url.partition("://")
    if not separator:
        # No scheme at all. Real dispatches always carry one, so rather than
        # guess at a bare path, decline to answer and let the caller fall
        # through to its existing behaviour.
        return None
    scheme = scheme.lower()
    if scheme == _EXTERNAL_STORAGE_SCHEME:
        return True
    if scheme == _LOCAL_FILE_SCHEME:
        # Only a known-internal path is positive evidence of somewhere FTPS
        # cannot reach. Anything else is unknown, which sweeps -- this module
        # skips only on positive evidence, and a path we do not recognise is
        # not that. Returning False here instead is what made a print of a file
        # sitting on the stick report as internal storage and archive with no
        # 3MF, when the sweep would have found it immediately.
        if path.startswith(_INTERNAL_FILE_PREFIXES):
            return False
        return None
    return False


def probe_filename_from_url(project_url: str | None) -> str | None:
    """The exact 3MF name *project_url* points at, for a bounded FTPS probe.

    ``brtc://emmc/Cube.gcode.3mf`` -> ``Cube.gcode.3mf``, and likewise for the
    internal ``file://`` paths. ``None`` when there is no name to probe with,
    which is the caller's signal to fall back to the sweep it would have run.

    Only ``.3mf`` names come back. A print running from a bare gcode has no 3MF
    to find at any path, so probing for one would spend connections to learn
    what the extension already said.

    The value arrives from the network -- whatever the slicer or the printer
    put in the dispatch -- and callers turn it into both a remote path and a
    local temp filename, so anything that could steer either is refused rather
    than sanitized: no separators, no traversal, no control characters.
    """
    if not isinstance(project_url, str):
        return None
    _scheme, separator, path = project_url.partition("://")
    if not separator:
        return None
    name = path.rpartition("/")[2].strip()
    if not name or len(name) > _MAX_PROBE_FILENAME_LENGTH:
        return None
    # A leading dot is either a traversal segment or a hidden file; neither is
    # a sliced upload, and both would put an odd path on the wire. A backslash
    # is a path separator on the host even though it is a legal character in
    # the printer's own filesystem, which is how a name could reach outside the
    # temp directory it is written to.
    if name.startswith(".") or "\\" in name:
        return None
    if any(character < " " or character == "\x7f" for character in name):
        return None
    if not name.lower().endswith(".3mf"):
        return None
    return name


def ftp_probe_paths(filename: str) -> list[str]:
    """Remote paths to try for *filename*, best first.

    One filename across the known directories, because the dispatch already
    told us the name and only the directory is in question (#2856). Callers
    walk the list over a single connection, against the sweep's ~110.
    """
    return [f"{directory}{filename}" for directory in _PROBE_DIRECTORIES]


def external_storage_present(state: object | None) -> bool:
    """Does the printer have external storage for FTPS to serve at all?

    Narrower than :func:`print_file_reachable_over_ftp` and deliberately so.
    The printer records its timelapse to the card itself, so *where the sliced
    file went* says nothing about whether a video exists -- an H2C that kept
    the 3MF on eMMC still writes ``/timelapse`` to an inserted card. Only the
    empty-slot case rules a scan out, and only when the printer said the slot
    is empty rather than never mentioning it.
    """
    if state is None:
        return True
    return not (getattr(state, "sdcard_reported", False) and not getattr(state, "sdcard", False))


def print_file_reachable_over_ftp(state: object | None) -> StorageVerdict:
    """Decide whether to run an FTPS sweep for the print *state* is running.

    *state* is a ``PrinterState`` (duck-typed so tests and callers can pass a
    stand-in). Reads ``current_project_url``, ``sdcard`` and ``sdcard_reported``.

    Deliberately the *per-print* URL, not the sticky one: a print Bambuddy saw
    no dispatch for must read as unknown and sweep, rather than inherit the
    previous job's destination. Roughly a fifth of the print starts in #2780's
    bundle had no dispatch on the request topic -- touchscreen reprints and
    restart recovery -- and inheriting a stale internal-storage answer there
    would skip a sweep that could have found the file.

    Returns :data:`_REACHABLE` unless something positively says otherwise.
    """
    return _verdict(getattr(state, "current_project_url", None), state)


def last_print_storage_verdict(state: object | None) -> StorageVerdict:
    """Same question, asked of the last dispatch seen whenever that was.

    For reporting only -- the connection diagnostic is normally run after the
    print that prompted it, by which point the per-print URL has been cleared.
    Never gate an FTPS sweep on this: it may describe a different print.
    """
    return _verdict(getattr(state, "last_project_url", None), state)


def _internal_reason(project_url: str | None) -> str:
    """Which flavour of "internal" *project_url* names.

    Only ever reached on a negative verdict, so the URL is one of the two
    shapes :func:`url_is_external_storage` answers False for.
    """
    if not isinstance(project_url, str):
        return REASON_INTERNAL_STORAGE
    scheme = project_url.partition("://")[0].lower()
    return REASON_INTERNAL_HISTORY if scheme == _LOCAL_FILE_SCHEME else REASON_INTERNAL_STORAGE


def _verdict(project_url: str | None, state: object | None) -> StorageVerdict:
    if state is None:
        return _REACHABLE

    # Strongest signal, and specific to the print in question: the dispatcher
    # named the destination.
    external = url_is_external_storage(project_url)
    if external is False:
        # Worth probing only if there is external storage for the probe to find
        # anything on. An empty slot answers the question the probe would ask,
        # and #2780's H2C sat that way for three weeks -- one connection per
        # print start is small, but it is not worth spending to be told what
        # the printer already said.
        return StorageVerdict(
            reachable=False,
            reason=_internal_reason(project_url),
            probe_filename=probe_filename_from_url(project_url) if external_storage_present(state) else None,
        )
    if external is True:
        # It said external storage, so sweep even if the card flags disagree.
        # Trusting the specific claim over the general one is what keeps a
        # printer that misreports `sdcard` from losing archives that work
        # today -- a false skip is a regression, a needless sweep is only slow.
        return _REACHABLE

    # Model-independent fallback for printers whose broker refuses the request
    # topic, so we never see a `project_file` at all. An empty slot means FTPS
    # has nothing to serve from any path -- but only when the printer actually
    # said so. `sdcard` defaults to False, and acting on that default would
    # skip the sweep for every printer that simply doesn't publish the field.
    if getattr(state, "sdcard_reported", False) and not getattr(state, "sdcard", False):
        return StorageVerdict(reachable=False, reason=REASON_NO_EXTERNAL_STORAGE)

    return _REACHABLE
