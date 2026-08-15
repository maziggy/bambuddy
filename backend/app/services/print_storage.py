"""Can FTPS see the file this print is running from? (#2780)

Bambuddy reads a print's 3MF, cover and timelapse off the printer over implicit
FTPS on port 990. On every Bambu model that port serves **external storage only**
-- the SD card or USB stick. It is not a view of the printer's filesystem.

H2-series and P2S firmware default to keeping the sliced file on internal eMMC
instead, and BambuStudio uploads there over a separate service on port 6000
(the "BambuTunnelLocal" protocol -- see #2762, which tracks implementing it).
When that happens there is no file on FTPS to find, at any path, and no TLS
option, retry or directory guess changes that. The dispatch says so plainly:
the ``project_file`` command carries ``url``, which is ``ftp://<name>`` for
external storage and ``brtc://emmc/<name>`` for internal.

Before this module we ignored ``url`` and swept anyway: six filename variants
across five directories with up to four retries for the 3MF, then sixteen more
paths for the cover, then the timelapse scan -- roughly 110 FTPS connections per
print, every one of them certain to 550. The user-visible result was an archive
card with nothing on it and no stated reason, which read as a Bambuddy bug and
was reported as one four times (#1170, #2524, #2762, #2780).

The rule here is deliberately one-sided: **skip only on positive evidence**.
Silence is not evidence -- a printer that never publishes ``sdcard`` and never
had a ``project_file`` pass through the request topic (some brokers refuse the
subscription) must keep the old behaviour exactly, or this becomes a regression
for installs whose archives work fine today.
"""

from __future__ import annotations

from dataclasses import dataclass

# The one URL scheme that means "on external storage, reachable over FTPS".
# Anything else -- brtc://emmc today, whatever Bambu ships next -- is somewhere
# port 990 does not serve. Matching the reachable value rather than the
# unreachable one is what keeps a new scheme from silently reading as fine.
_EXTERNAL_STORAGE_SCHEME = "ftp"

# Reason slugs. These cross the API into the UI and into the connection
# diagnostic, so they are part of the contract: the frontend maps each to its
# own explanation and its own advice. Keep them stable.
REASON_INTERNAL_STORAGE = "internal_storage"
REASON_NO_EXTERNAL_STORAGE = "no_external_storage"


@dataclass(frozen=True)
class StorageVerdict:
    """Whether an FTPS sweep for this print's file is worth running.

    ``reachable`` False always carries a ``reason``; True never does.
    """

    reachable: bool
    reason: str | None = None


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
    scheme, separator, _ = project_url.partition("://")
    if not separator:
        # No scheme at all. Real dispatches always carry one, so rather than
        # guess at a bare path, decline to answer and let the caller fall
        # through to its existing behaviour.
        return None
    return scheme.lower() == _EXTERNAL_STORAGE_SCHEME


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


def _verdict(project_url: str | None, state: object | None) -> StorageVerdict:
    if state is None:
        return _REACHABLE

    # Strongest signal, and specific to the print in question: the dispatcher
    # named the destination.
    external = url_is_external_storage(project_url)
    if external is False:
        return StorageVerdict(reachable=False, reason=REASON_INTERNAL_STORAGE)
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
