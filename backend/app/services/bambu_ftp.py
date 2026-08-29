import asyncio
import ftplib  # nosec B402
import logging
import os
import shutil
import socket
import ssl
import threading
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from ftplib import FTP, FTP_TLS  # nosec B402
from io import BytesIO
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Every FTP call below is blocking ftplib work handed to a thread. They used to
# run on asyncio's *default* executor, which is sized min(32, cpu_count + 4) —
# six threads on a 2-core NAS — and is shared with every other ``to_thread`` /
# ``run_in_executor`` caller in the app. That was survivable only because the
# scheduler uploaded to exactly one printer at a time. Dispatching to several
# printers at once (#2555) would park one thread per in-flight upload for
# minutes at a stretch (a 41 MB 3MF at the ~150 KB/s a Bambu printer sustains
# takes ~4 min), starving the default pool and stalling unrelated work.
#
# A dedicated pool keeps that blast radius inside the FTP layer: the scheduler's
# own concurrency cap is what limits parallel uploads, and it can never exhaust
# the executor everything else depends on. Threads are created lazily, so an
# idle pool costs nothing.
#
# Sized well above `queue_max_concurrent_uploads` (max 16), because uploads are
# not the only traffic here: SD browsing, timelapse/recording listing, cover
# downloads, deletes and storage checks all run through this pool too, and on a
# farm they fan out across every printer at once. The pool's work queue is
# unbounded, so exceeding it does not fail — it queues. But `asyncio.wait_for`
# starts its clock at submission, not at thread start, so a task that sits in the
# queue can burn its whole timeout without ever running, and `list_files_async`
# reports a timeout as an empty listing — a silent "this printer has no files".
# Keep the headroom.
_FTP_MAX_WORKERS = 48
_ftp_executor = ThreadPoolExecutor(max_workers=_FTP_MAX_WORKERS, thread_name_prefix="bambu-ftp")

# Overall upload deadline (#2529). A flat wall-clock cap punishes big files on
# slow links rather than catching broken ones: a 96 MB 3MF at the ~75 KB/s an A1
# sustains over WiFi legitimately needs ~20 minutes, and the old flat 600 s
# declared it dead at ~70 MB. The deadline is therefore derived from the file
# size against a deliberately pessimistic floor rate. This is a backstop, not the
# failure detector — a link that has actually died is caught within
# ``socket_timeout`` by the blocking ``sendall``, long before this fires.
_UPLOAD_FLOOR_BYTES_PER_SEC = 25 * 1024
_UPLOAD_MIN_TIMEOUT = 600.0

# The same idea for the other direction (#2957). ``ftp_timeout`` is handed to
# every download as BOTH the socket inactivity timeout and the whole-transfer
# deadline, so its 30 s default is a cap on how big a file the printer is
# allowed to serve. A reporter measured the same 5.4 MB 3MF at 45 s off a worn
# P1S SD card and 25 s off a new one, and a 15.15 MB 3MF at 105 s; a 7.8 MB
# archive in his older logs survived only because it finished inside the retry
# grace. None of those transfers were unhealthy -- they were slow, which is what
# the inactivity timeout is for and what a total deadline cannot tell apart.
#
# So the total deadline follows the file instead, at the same pessimistic floor
# rate the upload path uses. The extension is granted only once the printer has
# answered SIZE, which it can only do from a running worker: the queue wait that
# #2572's cap exists to bound is not lengthened by any of this.
_DOWNLOAD_FLOOR_BYTES_PER_SEC = 25 * 1024

# ...but not without a ceiling, and for a reason that has nothing to do with FTP:
# ``on_print_start`` runs its whole 3MF hunt inside one ``async_session``, so
# every second a download is allowed is a second a pooled DB connection is held
# (the same coupling behind #2572's cap). 300 s is ~7.5 MB at the floor rate and
# covers the reporter's 15.15 MB / 105 s measurement three times over, because
# the floor is pessimistic by design and a real link is not that slow.
_DOWNLOAD_MAX_TIMEOUT = 300.0


def _download_extension(size: int | None, base_timeout: float) -> float:
    """Extra seconds to allow a download the printer says is this big.

    Zero when the size is unknown -- no SIZE reply means no transfer got under
    way, so the base deadline stands and a printer that is not answering still
    fails on schedule. Zero, too, once the base deadline is already the more
    generous of the two: this only ever lengthens a deadline.
    """
    if not size or size <= 0:
        return 0.0
    return max(0.0, min(size / _DOWNLOAD_FLOOR_BYTES_PER_SEC, _DOWNLOAD_MAX_TIMEOUT) - base_timeout)


# How long a download will wait for another one on the same printer to finish
# before going ahead alongside it (#2957). A P1S at print start is already
# serving the print off the same SD card and talking MQTT to the slicer, and a
# reporter watched Bambu Studio itself lose its connection while Bambuddy pulled
# a 12 MB 3MF -- with a second Bambuddy transfer for the same file running at
# the same time. 30 s covers the transfer sizes that actually overlap at print
# start -- the reporter's 5.4 MB 3MF took 25 s off a healthy SD card -- and the
# wait is deliberately no longer, because the gate is contention relief and not
# a correctness control. Whoever cannot have it goes anyway, exactly as every
# download did before this existed: a print must never lose its 3MF to queueing,
# and the caller's own deadline stays untouched either way.
_DOWNLOAD_GATE_WAIT_SECONDS = 30.0

# How long to wait for a cancelled path-walk worker to unwind before releasing
# the printer to the next download. It checks the flag once per 8 KiB chunk, so
# this is one chunk on a link slow enough to have blown the deadline.
_DOWNLOAD_UNWIND_SECONDS = 30.0


def _discard_worker_outcome(worker: asyncio.Future) -> None:
    """Read a shielded worker's result so asyncio does not complain about it.

    ``asyncio.wait_for`` cancels the shield, not the executor thread behind it,
    so the worker future outlives the call and nobody is left to look at what it
    raised. An unretrieved exception surfaces later as a loop-level
    ``Future exception was never retrieved`` ERROR with a traceback, logged
    after the caller has already reported the real failure -- the same class of
    noise as #2968, and measurably reproducible with a transport error that
    lands just after the cap. The value is genuinely unwanted here; only the
    fact that something read it matters.
    """

    def _read(fut: asyncio.Future) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            logger.debug("FTP path-walk worker failed after its caller gave up: %s", exc)

    if worker.done():
        _read(worker)
    else:
        worker.add_done_callback(_read)


# One heavy download at a time per printer. Keyed per event loop for the same
# reason ``_upload_locks`` is: an asyncio.Lock binds to the loop that first
# awaits it, and the test suite runs each case on a fresh loop.
_download_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _download_lock(loop: asyncio.AbstractEventLoop, ip_address: str) -> asyncio.Lock:
    per_loop = _download_locks.setdefault(loop, {})
    lock = per_loop.get(ip_address)
    if lock is None:
        lock = asyncio.Lock()
        per_loop[ip_address] = lock
    return lock


@asynccontextmanager
async def _serialized_download(ip_address: str, what: str, *, enabled: bool = True) -> AsyncIterator[bool]:
    """Hold this printer's download gate for the block, or go without it.

    Yields whether the gate was actually held, which is what the tests assert on
    -- from the outside a serialized download and a concurrent one differ only
    in timing. The wait is its own budget rather than a slice of the caller's
    transfer deadline: a print-start download that queued behind a thumbnail
    would otherwise fail on a timer and leave the print with no archive, which
    is a worse outcome than the contention this is here to relieve.

    ``enabled=False`` skips the gate entirely, for the callers that documented
    themselves as lock-free before this existed -- see ``serialize`` on
    :func:`download_file_async`.
    """
    if not enabled:
        yield False
        return
    loop = asyncio.get_event_loop()
    lock = _download_lock(loop, ip_address)
    started = loop.time()
    held = False
    try:
        await asyncio.wait_for(lock.acquire(), timeout=_DOWNLOAD_GATE_WAIT_SECONDS)
        held = True
        waited = loop.time() - started
        if waited > 1.0:
            logger.info("Waited %.1fs for printer %s to finish its other download before %s", waited, ip_address, what)
    except TimeoutError:
        logger.warning(
            "Printer %s is still busy with another download after %ss — starting %s alongside it",
            ip_address,
            _DOWNLOAD_GATE_WAIT_SECONDS,
            what,
        )
    try:
        yield held
    finally:
        if held:
            lock.release()


# How long to give the worker thread to notice the cancel flag, unwind, and
# delete its partial file. It checks the flag once per CHUNK_SIZE, so on a link
# slow enough to have hit the deadline this is one chunk plus the delete.
_UPLOAD_CANCEL_GRACE = 60.0


class UploadCancelled(Exception):
    """Raised inside the upload worker to abort an in-flight transfer.

    ``upload_file`` treats any exception from its progress callback as "stop
    now": it breaks out of the send loop, deletes the partial file from the
    printer, and re-raises. That is the only way to stop a transfer — an
    executor thread cannot be cancelled from the event loop, so a bare
    ``asyncio.wait_for`` leaves it streaming (see ``upload_file_async``).
    """


class DownloadCancelled(Exception):
    """Raised in an FTP callback to stop a disk-backed download cooperatively."""


class DownloadLimitExceeded(Exception):
    """Raised before an FTP callback writes beyond its caller-supplied limit."""


class DownloadInsufficientSpace(Exception):
    """Raised before an FTP callback consumes the application's disk reserve."""


class DownloadDeadlineExceeded(Exception):
    """A transfer overran the deadline derived from the size the printer reported.

    Never retried, for the reason ``UploadCancelled`` is not (#2529): the
    deadline was already stretched to fit the file at a floor rate no working
    link falls below, so another attempt would spend another full deadline
    reaching the same conclusion -- and ``on_print_start`` spends it holding a
    pooled database connection. Raised instead of returning False so
    ``with_ftp_retry`` can tell this apart from an ordinary failed attempt
    (#2957); callers that do not retry see it through their existing handlers,
    which is why the archive flow advances to its next candidate path.
    """


@dataclass(frozen=True)
class FileListResult:
    """A directory listing that distinguishes empty from unreachable."""

    files: list[dict]
    available: bool


class DeleteResult(Enum):
    """Outcome of an FTP delete attempt.

    Distinguishes "file isn't on the printer" (550, recovery impossible by
    retrying) from "delete failed for some other reason" (network, auth,
    transient FTP error — worth retrying). The post-print SD-card cleanup in
    main.py used to flatten both into ``False`` and log a "may linger" WARNING
    on every successful print where the printer self-cleaned its SD card
    before our cleanup ran (#1721 reporter's A1).
    """

    DELETED = "deleted"
    NOT_FOUND = "not_found"
    FAILED = "failed"


# How long to stop opening FTPS connections to a printer after its TLS
# handshake failed (#2780).
#
# ``WRONG_VERSION_NUMBER`` on port 990 means the printer answered with
# something that is not a TLS record at all, so no path, retry or SSL option
# gets further. Two support bundles show that state lasting for days: one X2D
# served clean FTPS for five days, flipped on 2026-07-19, and then failed every
# single handshake for the next eight (zero successes, 3511 failures).
#
# What it is NOT is a wedged file service, which is what this comment used to
# claim. #2780's reporter power-cycled both affected printers and the state
# survived it, and ``openssl s_client`` against the same port completes a clean
# handshake and returns a valid certificate while Bambuddy is failing. The
# leading theory is now a connection-count refusal — vsFTPd answers one in
# cleartext, which is exactly this error to an implicit-TLS client, and answers
# the global limit by accepting and never speaking, which is the handshake
# timeout we also see. Unproven: confirming it needs a capture taken while a
# printer is in the failing state.
#
# Without a gate every candidate path re-runs the same doomed handshake: the
# 3MF lookup alone walks 6 filename variants x 5 directories x 4 retries, and
# the cover and timelapse scans run their own sweeps on top. That is where
# those thousands of failures come from — one wedged printer, hammered.
#
# Five minutes is short enough that a power-cycled printer is picked up on the
# next print (and any successful connect clears the gate immediately), long
# enough that a wedged one is contacted twice an hour instead of hundreds of
# times a minute.
_HANDSHAKE_COOLOFF_SECONDS = 300.0


# How long to wait for a printer to say something in cleartext on the TLS port.
# The failing case answers immediately -- the banner is the first thing a
# vsFTPd refusal sends -- so this only ever elapses in full when the service has
# gone back to speaking TLS and is waiting for a ClientHello that will not come.
_CLEARTEXT_PROBE_TIMEOUT = 2.0


def _read_cleartext_reply(ip_address: str, port: int) -> str | None:
    """Read what a printer answers the TLS port with, when it is not TLS.

    ``WRONG_VERSION_NUMBER`` means the peer's first bytes were not a TLS
    record -- measured, not inferred: a cleartext ``421`` banner reproduces
    that exact error and message, while a genuine version mismatch produces
    ``TLSV1_ALERT_PROTOCOL_VERSION`` instead (#2780).

    What it does not say is *which* cleartext message, and that is the part
    that would identify the fault. OpenSSL has already consumed those bytes by
    the time the error surfaces, so this opens one plain connection and reads
    them directly. Answering it from the reporter's own printers beats waiting
    on a packet capture from the one farm that can take one.

    Returns the reply, or None when the printer said nothing readable -- which
    is itself informative: a healthy implicit-FTPS service sends nothing until
    it has a ClientHello, so silence means the fault had already passed.
    """
    sock = None
    # One budget for connect *and* read. Given a timeout each, a printer that
    # is slow to accept would then get the full read window on top of it, and
    # the wait this adds to a failed connect would be double what it says.
    deadline = time.monotonic() + _CLEARTEXT_PROBE_TIMEOUT
    try:
        sock = socket.create_connection((ip_address, port), _CLEARTEXT_PROBE_TIMEOUT)
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        # One read. A refusal is a single short line; anything longer is not
        # the thing being looked for, and this must not become a transfer.
        raw = sock.recv(256)
    except OSError as e:
        # Refused or reset is a different fact from "answered in cleartext",
        # and worth having in the log rather than flattened into silence.
        logger.debug("Cleartext probe of %s:%s could not connect: %s", ip_address, port, e)
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    if not raw:
        return None
    # latin-1 cannot fail, and an FTP reply line is ASCII in practice. Control
    # characters are stripped so a stray byte cannot mangle the log line.
    text = raw.decode("latin-1").strip()
    return "".join(c for c in text if c.isprintable()) or None


def _ftp_reply_code(error: BaseException) -> str | None:
    """The three-digit reply code an ftplib error carries, if it carries one.

    ``ftplib`` puts the server's whole reply line in the exception message, so
    the code is the first token: "553 Could not create file." Anything that is
    not three digits (an ``OSError``, a library-side message) has no code, and
    saying so beats inventing one.
    """
    head = str(error)[:3]
    return head if head.isdigit() else None


class FtpFailureKind(Enum):
    """Why an FTP operation failed, at the granularity the client can tell.

    ``connect`` and ``upload_file`` already separate every one of these -- each
    has its own log line, and 553 even gets a spelled-out list of storage
    causes -- and then both returned a bare ``False``. So the dispatch that
    reports the failure to the operator had nothing to go on, and used one
    string for all of them: "check if SD card is inserted and properly
    formatted". #2899's reporter acted on that after a TLS handshake failure
    and restarted the printer, which could not have helped: the handshake never
    got near the printer's filesystem.
    """

    COOLOFF = "cooloff"  # skipped without contacting the printer (#2780)
    HANDSHAKE = "handshake"  # port 990 answered with something that is not TLS
    AUTH = "auth"  # permanent refusal, typically a rejected access code
    TIMEOUT = "timeout"
    STORAGE = "storage"  # 553/552 -- the case the SD-card advice was written for
    NOT_FOUND = "not_found"  # 550
    NETWORK = "network"  # socket dropped, or an FTP error with no clearer reading
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FtpFailure:
    """What went wrong, kept next to the log line that already said it."""

    kind: FtpFailureKind
    detail: str
    code: str | None = None  # FTP reply code where the server gave one


@dataclass
class FtpFailureReport:
    """A slot the *caller* owns for the reason its upload failed.

    Deliberately not a per-IP dict on the client, the way ``_mode_cache`` and
    ``_handshake_blocked_until`` are. Those describe a printer, and are
    correct to share. This describes one operation, and a background timelapse
    fetch running beside a dispatch would overwrite the dispatch's reason with
    its own -- reporting the wrong cause with total confidence, which is the
    bug being fixed rather than a new way to hit it (#2899).
    """

    failure: FtpFailure | None = None


class FileNotOnPrinterError(Exception):
    """Raised when a remote FTP path returns 550 (file not found).

    550 means the file does not exist at that path — retrying the same path
    will never succeed. Callers use this sentinel with with_ftp_retry's
    non_retry_exceptions to immediately move on to the next candidate path
    instead of burning the full retry budget (up to 11 × 30s per path) on
    a lookup that cannot recover.
    """


class ImplicitFTP_TLS(FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (port 990) with model-specific SSL handling.

    X1C/P1S printers (vsFTPd) require SSL with session reuse on the data channel.
    A1/A1 Mini printers have issues with SSL on the data channel entirely and
    timeout waiting for transfer completion. Set skip_session_reuse=True for A1
    printers to skip SSL on the data channel (control channel remains encrypted).

    Optionally caps the SSL context's maximum TLS version to v1.2 (P2S firmware
    01.02.00.00 needs this — see :mod:`ftp_profiles` and #1401).
    """

    def __init__(self, *args, skip_session_reuse: bool = False, cap_tls_v1_2: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None
        self.skip_session_reuse = skip_session_reuse
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        # ``create_default_context()`` does NOT guarantee a protocol floor: it
        # leaves ``minimum_version`` at ``MINIMUM_SUPPORTED``, and what that
        # resolves to is a property of the OpenSSL build, not of this code.
        # Measured on identical OpenSSL 3.5.6: python:3.13-slim-trixie (our
        # Docker base) reports TLSv1_2, a bare-metal venv reports
        # MINIMUM_SUPPORTED. Docker users have therefore always been floored at
        # 1.2 — every Bambu model is reachable under that floor — while
        # bare-metal and appliance installs could silently negotiate TLS 1.0.
        # State the floor rather than inheriting it.
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        if cap_tls_v1_2:
            # With the floor above this pins the connection to exactly TLS 1.2.
            self.ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2

    def connect(self, host="", port=990, timeout=-999, source_address=None):
        """Connect to host, wrapping socket in TLS immediately (implicit FTPS)."""
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address:
            self.source_address = source_address

        # Create and wrap socket immediately (implicit TLS)
        self.sock = socket.create_connection((self.host, self.port), self.timeout, source_address=self.source_address)
        self.sock = self.ssl_context.wrap_socket(self.sock, server_hostname=self.host)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        """Override to wrap data connection in SSL for X1C/P1S only.

        X1C/P1S printers (vsFTPd) require SSL session reuse on the data channel.
        A1/A1 Mini printers have issues with SSL on the data channel entirely -
        they timeout waiting for the transfer completion response. For A1, we
        skip SSL wrapping on the data channel (control channel remains encrypted).
        """
        conn, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p and not self.skip_session_reuse:
            # X1C/P1S: Wrap data channel with SSL session reuse (required by vsFTPd)
            conn = self.ssl_context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,
            )
        # A1/A1 Mini (skip_session_reuse=True): Don't wrap data channel in SSL
        # The control channel remains encrypted via implicit FTPS
        return conn, size


class BambuFTPClient:
    """FTP client for retrieving files from Bambu Lab printers."""

    FTP_PORT = 990
    # Default timeout in seconds (increased for A1 printers)
    DEFAULT_TIMEOUT = 30
    # Models that may need SSL mode fallback (try prot_p first, fall back to prot_c)
    # These models have varying FTP SSL behavior depending on firmware version
    A1_MODELS = ("A1", "A1 Mini")
    # Chunk size for manual upload transfer (64KB)
    # Smaller chunks provide smoother progress reporting — at typical printer FTP
    # speeds (~50-100KB/s) this gives a progress update roughly every second.
    CHUNK_SIZE = 64 * 1024

    # Cache for working FTP modes per printer IP
    # Maps IP -> "prot_p" or "prot_c"
    _mode_cache: dict[str, str] = {}

    # Printers whose FTPS handshake just failed, mapped to the monotonic time
    # their cool-off expires. See ``_HANDSHAKE_COOLOFF_SECONDS``.
    _handshake_blocked_until: dict[str, float] = {}

    # Which cool-off deadline each printer's "not attempted" warning was last
    # logged for, so the warning is said once per cool-off. See ``connect``.
    _handshake_skip_logged: dict[str, float] = {}

    def __init__(
        self,
        ip_address: str,
        access_code: str,
        timeout: float | None = None,
        printer_model: str | None = None,
        force_prot_c: bool = False,
        respect_handshake_cooloff: bool = True,
    ):
        """Set ``respect_handshake_cooloff=False`` for bounded, user-initiated work.

        The cool-off exists to stop an unbounded sweep re-running one doomed
        handshake a hundred times over (#2780). Dispatching a print is not
        that: it is one delete plus at most four upload attempts, with someone
        waiting on the result. Sharing the sweep's gate cost those attempts
        their whole retry budget, and failed every further job queued for that
        printer for the rest of the 300s window (#2898).

        Leave it at the default everywhere else. Opting out is only defensible
        because the caller's own connection count is bounded and small.
        """
        self.ip_address = ip_address
        self.access_code = access_code
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self.printer_model = printer_model
        self.force_prot_c = force_prot_c
        self.respect_handshake_cooloff = respect_handshake_cooloff
        # Why the last connect/upload on this client failed, for a caller that
        # only gets a bool back (#2899). Per instance, so it describes one
        # operation and cannot be overwritten by work against another printer.
        self.last_failure: FtpFailure | None = None
        self._ftp: ImplicitFTP_TLS | None = None

    def _is_a1_model(self) -> bool:
        """Check if this is an A1 series printer."""
        if not self.printer_model:
            return False
        return self.printer_model in self.A1_MODELS

    def _get_cached_mode(self) -> str | None:
        """Get cached FTP mode for this printer."""
        return self._mode_cache.get(self.ip_address)

    @classmethod
    def cache_mode(cls, ip_address: str, mode: str):
        """Cache the working FTP mode for a printer."""
        cls._mode_cache[ip_address] = mode
        logger.info("FTP mode cached for %s: %s", ip_address, mode)

    def _should_use_prot_c(self) -> bool:
        """Determine if we should use prot_c (clear) mode."""
        # If explicitly forced, use prot_c
        if self.force_prot_c:
            return True
        # Check cache first
        cached = self._get_cached_mode()
        if cached:
            return cached == "prot_c"
        # Default: try prot_p first (will fall back if needed)
        return False

    @classmethod
    def handshake_blocked(cls, ip_address: str) -> bool:
        """True while *ip_address* is inside its post-handshake-failure cool-off.

        Public so a caller sweeping many candidate paths can stop after the
        first one rather than walking the rest against a printer that cannot
        complete a TLS handshake (#2780).
        """
        deadline = cls._handshake_blocked_until.get(ip_address)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            # Drop it on the way past rather than leaving an entry per printer
            # this process has ever failed against.
            del cls._handshake_blocked_until[ip_address]
            cls._handshake_skip_logged.pop(ip_address, None)
            return False
        return True

    def connect(self) -> bool:
        """Connect to the printer FTP server (implicit FTPS on port 990).

        Returns False without touching the network while the printer is inside
        the cool-off a previous TLS handshake failure opened (#2780) -- unless
        this client was built with ``respect_handshake_cooloff=False``.
        """
        self.last_failure = None
        if self.respect_handshake_cooloff and self.handshake_blocked(self.ip_address):
            # WARNING, not DEBUG. This is the one connect() failure path that
            # reported without its cause, so at default log level four
            # reason-free "FTP connection failed" lines two seconds apart gave
            # no hint that nothing had been sent (#2898). Every caller reaching
            # here is already gated by handshake_blocked() at its own sweep
            # boundary, so this costs about one line per print, not a flood.
            deadline = self._handshake_blocked_until.get(self.ip_address)
            remaining = max(0.0, deadline - time.monotonic()) if deadline is not None else 0.0
            if deadline is not None and self._handshake_skip_logged.get(self.ip_address) != deadline:
                self._handshake_skip_logged[self.ip_address] = deadline
                logger.warning(
                    "FTP connect to %s not attempted: its FTPS handshake failed recently and it is "
                    "cooling off for another %.0fs. Nothing was sent to the printer.",
                    self.ip_address,
                    remaining,
                )
            else:
                # Said once already for this cool-off. Repeating it per candidate
                # path is the log flood #2780 set out to stop -- a download-zip
                # of 200 files would print the same sentence 200 times.
                logger.debug(
                    "FTP connect to %s skipped: still cooling off for another %.0fs",
                    self.ip_address,
                    remaining,
                )
            self.last_failure = FtpFailure(
                FtpFailureKind.COOLOFF,
                f"cooling off for another {remaining:.0f}s after a recent FTPS handshake failure",
            )
            return False
        try:
            use_prot_c = self._should_use_prot_c()
            from backend.app.services.ftp_profiles import get_ftp_profile

            profile = get_ftp_profile(self.printer_model)
            logger.debug(
                f"FTP connecting to {self.ip_address}:{self.FTP_PORT} "
                f"(timeout={self.timeout}s, model={self.printer_model}, prot_c={use_prot_c}, "
                f"cap_tls_v1_2={profile.cap_tls_v1_2})"
            )
            self._ftp = ImplicitFTP_TLS(
                skip_session_reuse=use_prot_c,
                cap_tls_v1_2=profile.cap_tls_v1_2,
            )
            self._ftp.connect(self.ip_address, self.FTP_PORT, timeout=self.timeout)
            logger.debug("FTP connected, logging in as bblp")
            self._ftp.login("bblp", self.access_code)
            if use_prot_c:
                # Use clear (unencrypted) data channel
                logger.debug("FTP logged in, setting prot_c (clear) and passive mode")
                self._ftp.prot_c()
            else:
                # Use protected (encrypted) data channel with session reuse
                logger.debug("FTP logged in, setting prot_p (protected) and passive mode")
                self._ftp.prot_p()
            self._ftp.set_pasv(True)
            # Log welcome message for debugging
            if hasattr(self._ftp, "welcome") and self._ftp.welcome:
                logger.debug("FTP server welcome: %s", self._ftp.welcome)
            logger.info(
                f"FTP connected successfully to {self.ip_address} (model={self.printer_model}, prot_c={use_prot_c})"
            )
            return True
        except ftplib.error_perm as e:
            logger.warning("FTP connection permission error to %s: %s", self.ip_address, e)
            self.last_failure = FtpFailure(FtpFailureKind.AUTH, str(e), _ftp_reply_code(e))
            self._abandon_connection()
            return False
        except TimeoutError as e:
            logger.warning("FTP connection timed out to %s: %s", self.ip_address, e)
            self.last_failure = FtpFailure(FtpFailureKind.TIMEOUT, str(e))
            self._abandon_connection()
            return False
        except ssl.SSLError as e:
            # Not a transient failure and not something another path or another
            # retry can route around: the printer's file service answered port
            # 990 with something that isn't TLS. Say so once and stop knocking
            # for a while (#2780).
            #
            # Deliberately no advice about what to do. This message used to
            # tell the operator to restart the printer; #2780's reporter did
            # that twice, to no effect, and a single manual connect to the
            # same printer completes a clean handshake. We do not yet know the
            # trigger, so stating the observation and stopping there beats
            # sending people to do the one thing already known not to work.
            logger.warning(
                "FTP SSL error connecting to %s: %s — the printer answered port %s with something "
                "that is not TLS, so print files, covers and timelapses cannot be fetched from it. "
                "Pausing FTP to this printer for %.0fs.",
                self.ip_address,
                e,
                self.FTP_PORT,
                _HANDSHAKE_COOLOFF_SECONDS,
            )
            # Close the dead socket before asking this printer for anything
            # else. The probe below opens a second connection, and the leading
            # theory for this failure is a printer out of connection slots --
            # holding a failed handshake open across that is the exact thing
            # #2780's cleanup was added to stop. Idempotent, so the call that
            # used to sit at the end of this branch simply moved up.
            self._abandon_connection()

            # Ask the printer what it actually said, once per cool-off window.
            # Checked before the deadline below is written, so a live entry here
            # means an earlier failure already opened this window and already
            # asked -- which keeps a dispatch that ignores the cool-off from
            # probing on each of its four attempts.
            detail = str(e)
            if getattr(e, "reason", None) == "WRONG_VERSION_NUMBER" and not self.handshake_blocked(self.ip_address):
                reply = _read_cleartext_reply(self.ip_address, self.FTP_PORT)
                if reply:
                    logger.warning(
                        "Printer %s answered port %s in cleartext with: %s — that is what the TLS "
                        "handshake read as a malformed record. Please include this line if you report it.",
                        self.ip_address,
                        self.FTP_PORT,
                        reply,
                    )
                    detail = f"{e} (printer answered in cleartext: {reply})"
                else:
                    logger.warning(
                        "Printer %s sent nothing readable in cleartext on port %s, so its file service "
                        "was speaking TLS again by the time we asked — the refusal was momentary.",
                        self.ip_address,
                        self.FTP_PORT,
                    )
            self._handshake_blocked_until[self.ip_address] = time.monotonic() + _HANDSHAKE_COOLOFF_SECONDS
            self.last_failure = FtpFailure(FtpFailureKind.HANDSHAKE, detail)
            return False
        except (OSError, ftplib.Error) as e:
            logger.warning("FTP connection failed to %s: %s (type: %s)", self.ip_address, e, type(e).__name__)
            self.last_failure = FtpFailure(FtpFailureKind.NETWORK, str(e), _ftp_reply_code(e))
            self._abandon_connection()
            return False

    def _abandon_connection(self) -> None:
        """Drop a connection that never became usable, closing its socket.

        Every failure path in :meth:`connect` used to clear ``self._ftp`` and
        nothing else, leaving a connected socket for the garbage collector.
        That is survivable once; it is not survivable at this volume. A single
        print used to walk ~110 candidate paths, so a printer refusing FTPS
        got ~110 sockets opened and abandoned in a couple of minutes, and one
        support bundle recorded 1813 of them in a day (#2780). If the refusal
        is the printer running out of connection slots -- which fits the
        evidence better than a wedged service, since a single manual connect
        to the same printer succeeds -- then abandoning sockets is not just
        untidy, it is what keeps the printer refusing.

        Uses ``close()`` rather than ``quit()``: QUIT is a command, and there
        is no working control channel to send it on.
        """
        ftp = self._ftp
        self._ftp = None
        if ftp is None:
            return
        try:
            ftp.close()
        except (OSError, ftplib.Error, EOFError):
            pass  # Best-effort; the socket may already be gone

    def disconnect(self):
        """Disconnect from the FTP server."""
        if self._ftp:
            try:
                self._ftp.quit()
            except (OSError, ftplib.Error, EOFError):
                # ``quit()`` sends QUIT and only then closes; when the send
                # raises, ftplib never reaches its own close and the socket
                # stays open. Close it here rather than leaving it to the GC.
                self._abandon_connection()
            self._ftp = None

    def list_files(self, path: str = "/", *, raise_on_error: bool = False) -> list[dict]:
        """List files in a directory."""
        if not self._ftp:
            return []

        files = []
        try:
            self._ftp.cwd(path)
            items = []
            self._ftp.retrlines("LIST", items.append)

            for item in items:
                parts = item.split()
                if len(parts) >= 9:
                    name = " ".join(parts[8:])
                    is_dir = item.startswith("d")
                    size = int(parts[4]) if not is_dir else 0

                    # Parse modification time from FTP listing
                    # Format: "Nov 30 10:15" or "Nov 30  2024"
                    mtime = None
                    try:
                        from datetime import datetime

                        month = parts[5]
                        day = parts[6]
                        time_or_year = parts[7]

                        # Determine if it's time (HH:MM) or year
                        if ":" in time_or_year:
                            # Recent file: "Nov 30 10:15" - assume current year
                            year = datetime.now().year
                            time_str = f"{month} {day} {year} {time_or_year}"
                            mtime = datetime.strptime(time_str, "%b %d %Y %H:%M")
                            # If parsed date is in the future, use last year
                            if mtime > datetime.now():
                                mtime = mtime.replace(year=year - 1)
                        else:
                            # Older file: "Nov 30 2024" - no time, just date
                            time_str = f"{month} {day} {time_or_year}"
                            mtime = datetime.strptime(time_str, "%b %d %Y")
                    except (ValueError, IndexError):
                        pass  # Non-critical: mtime parsing is best-effort; file entry works without it

                    file_entry = {
                        "name": name,
                        "is_directory": is_dir,
                        "size": size,
                        "path": f"{path.rstrip('/')}/{name}",
                    }
                    if mtime:
                        file_entry["mtime"] = mtime
                    files.append(file_entry)
            logger.debug("Listed %s files in %s", len(files), path)
        except (OSError, ftplib.Error) as e:
            logger.info("FTP list_files failed for %s: %s", path, e)
            if raise_on_error:
                raise

        return files

    def download_file(self, remote_path: str, expected_size: int | None = None) -> bytes | None:
        """Download a file from the printer.

        ``expected_size`` is the byte count the directory listing reported for
        this file. Pass it whenever a short read must not be mistaken for a
        successful download: an FTPS data connection that closes early does
        not always raise, so ``retrbinary`` can hand back a partial buffer that
        looks like a perfectly good file to everything downstream. That is
        tolerable when the printer keeps its copy, and not tolerable when the
        caller goes on to delete the source (#2704).

        A zero-byte result is always treated as a failure, matching
        :meth:`download_to_file` — no caller has a use for an empty file.
        """
        if not self._ftp:
            return None

        try:
            buffer = BytesIO()
            self._ftp.retrbinary(f"RETR {remote_path}", buffer.write)
            data = buffer.getvalue()
        except (OSError, ftplib.Error):
            return None

        if not data:
            logger.warning("FTP download returned 0 bytes for %s", remote_path)
            return None
        if expected_size is not None and len(data) != expected_size:
            logger.warning(
                "FTP download of %s is short: got %s bytes, listing reported %s — treating as failed",
                remote_path,
                len(data),
                expected_size,
            )
            return None
        return data

    def download_to_file(
        self,
        remote_path: str,
        local_path: Path,
        *,
        expected_size: int | None = None,
        max_bytes: int | None = None,
        cancel_event: threading.Event | None = None,
        min_free_bytes: int | None = None,
        size_callback: Callable[[int], None] | None = None,
    ) -> bool:
        """Download a file with cooperative cancellation and byte bounds.

        ``size_callback`` is handed the size the printer reported for this file,
        once, before the transfer starts. The async wrappers use it to grow a
        whole-transfer deadline that was set before anyone knew how big the file
        was (#2957); it must not raise.
        """
        if not self._ftp:
            logger.warning("download_to_file called but FTP not connected")
            return False

        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            # SIZE is the printer's own current view of the file and is more
            # trustworthy than a browser round-tripped listing hint. Some
            # firmware does not implement SIZE, so retain expected_size as a
            # compatibility fallback when the command is unavailable.
            try:
                server_size = self._ftp.size(remote_path)
            except (OSError, ftplib.Error):
                server_size = None
            authoritative_size = server_size if server_size is not None and server_size >= 0 else expected_size
            if max_bytes is not None and authoritative_size is not None and authoritative_size > max_bytes:
                raise DownloadLimitExceeded(remote_path)
            if min_free_bytes is not None and authoritative_size is not None:
                if shutil.disk_usage(local_path.parent).free < min_free_bytes + authoritative_size:
                    raise DownloadInsufficientSpace(remote_path)
            if size_callback is not None and authoritative_size is not None and authoritative_size > 0:
                size_callback(authoritative_size)
            with open(local_path, "wb") as f:
                written = 0
                # retrbinary hands over 8 KiB at a time, so checking the volume
                # on every callback is ~30k statvfs calls per 250 MB chunk for a
                # reserve measured in hundreds of megabytes. Sampling every few
                # MB cannot overshoot it by more than one interval.
                free_check_interval = 8 * 1024 * 1024
                next_free_check = 0

                def _write(chunk: bytes) -> None:
                    nonlocal written, next_free_check
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled(remote_path)
                    if max_bytes is not None and written + len(chunk) > max_bytes:
                        raise DownloadLimitExceeded(remote_path)
                    if min_free_bytes is not None and written >= next_free_check:
                        next_free_check = written + free_check_interval
                        if shutil.disk_usage(local_path.parent).free < min_free_bytes + free_check_interval:
                            raise DownloadInsufficientSpace(remote_path)
                    f.write(chunk)
                    written += len(chunk)

                self._ftp.retrbinary(f"RETR {remote_path}", _write)
                f.flush()
                os.fsync(f.fileno())
            file_size = local_path.stat().st_size if local_path.exists() else 0
            if file_size == 0:
                logger.warning("FTP download returned 0 bytes for %s", remote_path)
                if local_path.exists():
                    local_path.unlink()
                return False
            if authoritative_size is not None and file_size != authoritative_size:
                logger.warning(
                    "FTP download of %s is short: got %s bytes, listing reported %s — treating as failed",
                    remote_path,
                    file_size,
                    authoritative_size,
                )
                local_path.unlink(missing_ok=True)
                return False
            logger.info("Successfully downloaded %s to %s (%s bytes)", remote_path, local_path, file_size)
            return True
        except (OSError, ftplib.Error, DownloadCancelled, DownloadLimitExceeded, DownloadInsufficientSpace) as e:
            # Clean up partial file if it exists
            if local_path.exists():
                try:
                    local_path.unlink()
                except OSError:
                    pass  # Best-effort partial file cleanup; not critical if removal fails
            # 550 means the file is not at this path. Surface as a sentinel so
            # with_ftp_retry can abandon this path immediately and the caller
            # can advance to the next candidate instead of retrying 11× at
            # 30s intervals (the pattern that cost #972's reporter ~48min).
            if isinstance(e, (DownloadCancelled, DownloadLimitExceeded, DownloadInsufficientSpace)):
                raise
            if isinstance(e, ftplib.error_perm) and str(e).startswith("550"):
                logger.info("FTP download failed for %s: %s (not on printer)", remote_path, e)
                raise FileNotOnPrinterError(f"{remote_path}: {e}") from e
            # Log at INFO level so we can see failures in normal logs
            logger.info("FTP download failed for %s: %s", remote_path, e)
            return False

    def diagnose_storage(self) -> dict:
        """Run storage diagnostics and return results. For debugging upload issues."""
        results = {
            "connected": self._ftp is not None,
            "can_list_root": False,
            "root_files": [],
            "can_list_cache": False,
            "storage_info": None,
            "pwd": None,
            "errors": [],
        }

        if not self._ftp:
            results["errors"].append("FTP not connected")
            return results

        # Try to get current directory
        try:
            results["pwd"] = self._ftp.pwd()
            logger.debug("FTP current directory: %s", results["pwd"])
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"PWD failed: {e}")
            logger.debug("FTP PWD failed: %s", e)

        # Try to list root directory
        try:
            self._ftp.cwd("/")
            items = []
            self._ftp.retrlines("LIST", items.append)
            results["can_list_root"] = True
            results["root_files"] = items[:10]  # First 10 entries
            logger.debug("FTP root listing (%s items): %s", len(items), items[:5])
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"LIST / failed: {e}")
            logger.debug("FTP LIST / failed: %s", e)

        # Try to list /cache (should exist on all printers)
        try:
            self._ftp.cwd("/cache")
            items = []
            self._ftp.retrlines("LIST", items.append)
            results["can_list_cache"] = True
            logger.debug("FTP /cache listing: %s items", len(items))
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"LIST /cache failed: {e}")
            logger.debug("FTP LIST /cache failed: %s", e)

        # Try to get storage info
        try:
            results["storage_info"] = self.get_storage_info()
            logger.debug("FTP storage info: %s", results["storage_info"])
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"Storage info failed: {e}")

        return results

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Upload a file to the printer with optional progress callback."""
        self.last_failure = None
        if not self._ftp:
            logger.warning("upload_file: FTP not connected")
            self.last_failure = FtpFailure(FtpFailureKind.UNKNOWN, "no FTP connection")
            return False

        try:
            file_size = local_path.stat().st_size if local_path.exists() else 0
            logger.info("FTP uploading %s (%s bytes) to %s", local_path, file_size, remote_path)

            uploaded = 0
            callback_exception: Exception | None = None

            # Use manual transfer instead of storbinary() for A1 compatibility
            # A1 printers have issues with storbinary's voidresp() hanging after transfer
            with open(local_path, "rb") as f:
                logger.debug("FTP STOR command starting for %s", remote_path)
                t0 = time.monotonic()
                conn = self._ftp.transfercmd(f"STOR {remote_path}")
                logger.info(
                    "FTP data channel ready in %.1fs (PASV + TLS handshake)",
                    time.monotonic() - t0,
                )

                # Set explicit socket options for reliable transfer
                conn.setblocking(True)
                conn.settimeout(self.timeout)

                try:
                    while True:
                        chunk = f.read(self.CHUNK_SIZE)
                        if not chunk:
                            logger.debug("FTP upload: final chunk reached")
                            break

                        conn.sendall(chunk)
                        uploaded += len(chunk)
                        logger.debug("FTP upload progress: %s/%s bytes", uploaded, file_size)

                        if progress_callback:
                            try:
                                progress_callback(uploaded, file_size)
                            except Exception as e:
                                callback_exception = e
                                logger.info(
                                    "FTP upload callback requested stop for %s at %s/%s bytes: %s",
                                    remote_path,
                                    uploaded,
                                    file_size,
                                    e,
                                )
                                break

                except OSError as e:
                    logger.error("FTP connection lost during upload: %s", e)
                    raise
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

            # Wait for the server's 226 "Transfer complete" response to confirm
            # the file has been flushed to the SD card. Without this, the printer
            # may try to read an incomplete file when the print command is sent,
            # causing 0500-C010 "MicroSD Card read/write exception" errors.
            # See: https://bugs.python.org/issue25458 (ftplib response desync)
            try:
                old_timeout = self._ftp.sock.gettimeout()
                # Use a generous timeout — H2D printers can take 30+ seconds
                # to send the 226 after the data channel closes.
                self._ftp.sock.settimeout(max(self.timeout, 60))
                try:
                    resp = self._ftp.voidresp()
                    logger.info("FTP STOR confirmed for %s: %s", remote_path, resp.strip())
                finally:
                    self._ftp.sock.settimeout(old_timeout)
            except ftplib.Error as e:
                # Some P2S firmware revisions return ftplib.Error (e.g. 426
                # "Failure reading network stream") on voidresp() even when
                # the file landed fully on the SD card — the TLS data
                # channel close races the 226 confirmation (#1417 follow-up).
                # Verify via SIZE: if the server-side file size matches what
                # we just uploaded, the file is intact and we proceed with
                # a warning. If not — or SIZE itself fails — the transfer
                # was genuinely truncated and we must fail so the print
                # command doesn't go out for a partial 3MF (the original
                # reason this catch was tightened in the previous round).
                try:
                    server_size = self._ftp.size(remote_path)
                except (OSError, ftplib.Error) as size_err:
                    logger.debug("Post-error SIZE check failed: %s", size_err)
                    server_size = None
                if server_size is not None and server_size == file_size:
                    # INFO, not WARNING: a 426 whose bytes verify is the normal
                    # way Bambu FTPS ends a transfer, not a fault. It fired 54
                    # times in one support bundle and every one was followed by
                    # a completed upload, which buried the 26 handshake failures
                    # in the same log that actually cost the user two prints
                    # (#2987). The unverified branch below is still an error.
                    logger.info(
                        "FTP STOR returned %s for %s but file is intact on the "
                        "printer (%s bytes match) — proceeding: %s",
                        type(e).__name__,
                        remote_path,
                        file_size,
                        e,
                    )
                else:
                    logger.error(
                        "FTP STOR rejected by printer for %s: %s (%s); server size=%s expected=%s",
                        remote_path,
                        e,
                        type(e).__name__,
                        server_size,
                        file_size,
                    )
                    raise
            except Exception as e:
                # Timeout or socket-level error reading 226 — the data was sent
                # on our side and the printer may still have written the file.
                # H2D can take 30+ seconds to send 226 after the data channel
                # closes, so we proceed with a warning rather than failing here.
                logger.warning(
                    "FTP STOR confirmation not received for %s (proceeding): %s (%s)",
                    remote_path,
                    e,
                    type(e).__name__,
                )

            if callback_exception is not None:
                cleanup_result: DeleteResult = DeleteResult.FAILED
                try:
                    cleanup_result = self.delete_file(remote_path)
                except Exception as cleanup_error:
                    logger.warning("FTP cancel cleanup failed for %s: %s", remote_path, cleanup_error)

                # NOT_FOUND is success here — the partial file is gone (printer
                # may have already swept on cancel), which is the goal.
                if cleanup_result in (DeleteResult.DELETED, DeleteResult.NOT_FOUND):
                    logger.info("FTP cancel cleanup succeeded for %s (%s)", remote_path, cleanup_result.value)
                    raise callback_exception

                raise RuntimeError(
                    f"Upload cancelled but failed to remove partial file {remote_path} from printer"
                ) from callback_exception

            elapsed = time.monotonic() - t0
            speed_kbs = (file_size / 1024) / elapsed if elapsed > 0 else 0
            logger.info(
                "FTP upload complete: %s (%s bytes in %.1fs, %.0f KB/s)",
                remote_path,
                file_size,
                elapsed,
                speed_kbs,
            )
            return True
        except ftplib.error_perm as e:
            # Permanent FTP error (4xx/5xx response)
            error_code = str(e)[:3] if str(e) else "unknown"
            logger.error("FTP upload failed for %s: %s (error code: %s)", remote_path, e, error_code)
            # 553 and 552 are the printer telling us about its own storage --
            # the one case where advice about the card is worth giving, and
            # the case the dispatch's blanket SD-card message was written for
            # before it was applied to every failure alike (#2899).
            if error_code == "553":
                logger.error(
                    "FTP 553 error - Could not create file. Possible causes: "
                    "1) No SD card inserted, 2) SD card full, 3) SD card not formatted correctly (needs FAT32/exFAT), "
                    "4) Printer busy/not ready, 5) File path issue"
                )
                kind = FtpFailureKind.STORAGE
            elif error_code == "550":
                logger.error("FTP 550 error - File/directory not found or permission denied")
                kind = FtpFailureKind.NOT_FOUND
            elif error_code == "552":
                logger.error("FTP 552 error - Storage quota exceeded (SD card full?)")
                kind = FtpFailureKind.STORAGE
            else:
                kind = FtpFailureKind.UNKNOWN
            self.last_failure = FtpFailure(kind, str(e), _ftp_reply_code(e))
            return False
        except (OSError, ftplib.Error) as e:
            logger.error("FTP upload failed for %s: %s (type: %s)", remote_path, e, type(e).__name__)
            self.last_failure = FtpFailure(FtpFailureKind.NETWORK, str(e), _ftp_reply_code(e))
            return False

    def upload_bytes(self, data: bytes, remote_path: str) -> bool:
        """Upload bytes to the printer."""
        if not self._ftp:
            return False

        try:
            # Use manual transfer instead of storbinary() for A1 compatibility
            conn = self._ftp.transfercmd(f"STOR {remote_path}")
            conn.setblocking(True)
            conn.settimeout(self.timeout)

            try:
                # Send data in chunks
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + self.CHUNK_SIZE]
                    conn.sendall(chunk)
                    offset += len(chunk)
            except OSError as e:
                logger.error("FTP connection lost during upload_bytes: %s", e)
                raise
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            # Wait for 226 confirmation (see upload_file for rationale).
            # ftplib.Error subclasses (e.g. 426 error_temp) mean the server
            # rejected the transfer and the file is partial — fail. Other
            # exceptions (timeout, socket-level) are tolerated as in upload_file.
            try:
                old_timeout = self._ftp.sock.gettimeout()
                self._ftp.sock.settimeout(max(self.timeout, 60))
                try:
                    self._ftp.voidresp()
                finally:
                    self._ftp.sock.settimeout(old_timeout)
            except ftplib.Error as e:
                # Same SIZE-verify path as upload_file (#1417 follow-up):
                # tolerate a transient 426 if the bytes are actually on the
                # printer, fail loudly if they aren't.
                try:
                    server_size = self._ftp.size(remote_path)
                except (OSError, ftplib.Error) as size_err:
                    logger.debug("Post-error SIZE check failed: %s", size_err)
                    server_size = None
                if server_size is not None and server_size == len(data):
                    # INFO for the same reason as upload_file above (#2987).
                    logger.info(
                        "FTP STOR returned %s for %s but file is intact on the "
                        "printer (%s bytes match) — proceeding: %s",
                        type(e).__name__,
                        remote_path,
                        len(data),
                        e,
                    )
                else:
                    logger.error(
                        "FTP STOR rejected by printer for %s: %s (%s); server size=%s expected=%s",
                        remote_path,
                        e,
                        type(e).__name__,
                        server_size,
                        len(data),
                    )
                    return False
            except Exception:
                pass  # Timeout / socket-level — proceed, data was sent.
            return True
        except (OSError, ftplib.Error):
            return False

    def delete_file(self, remote_path: str) -> DeleteResult:
        """Delete a file from the printer.

        Returns :class:`DeleteResult` distinguishing the file-not-found case
        (550) from network / auth / transient FTP failure. Callers that just
        want "did it work" should check ``result == DeleteResult.DELETED``.
        """
        if not self._ftp:
            return DeleteResult.FAILED

        try:
            self._ftp.delete(remote_path)
            return DeleteResult.DELETED
        except ftplib.error_perm as e:
            if str(e).startswith("550"):
                logger.debug("FTP delete: %s not on printer (550)", remote_path)
                return DeleteResult.NOT_FOUND
            logger.warning("Failed to delete %s: %s", remote_path, e)
            return DeleteResult.FAILED
        except (OSError, ftplib.Error) as e:
            logger.warning("Failed to delete %s: %s", remote_path, e)
            return DeleteResult.FAILED

    def get_file_size(self, remote_path: str) -> int | None:
        """Get the size of a file."""
        if not self._ftp:
            return None

        try:
            return self._ftp.size(remote_path)
        except (OSError, ftplib.Error):
            return None

    def get_storage_info(self) -> dict | None:
        """Get storage information from the printer."""
        if not self._ftp:
            return None

        result = {}

        # Try AVBL command (available space) - some FTP servers support this
        try:
            response = self._ftp.sendcmd("AVBL")
            logger.debug("AVBL response: %s", response)
            # Response format: "213 <bytes available>"
            if response.startswith("213"):
                parts = response.split()
                if len(parts) >= 2:
                    result["free_bytes"] = int(parts[1])
        except (OSError, ftplib.Error) as e:
            logger.debug("AVBL command not supported: %s", e)
            # Try STAT command as fallback
            try:
                response = self._ftp.sendcmd("STAT")
                logger.debug("STAT response: %s", response)
            except (OSError, ftplib.Error):
                pass  # Both AVBL and STAT unsupported; storage info will rely on directory scan

        # Calculate used space by listing root directories
        try:
            total_used = 0
            dirs_to_scan = ["/cache", "/timelapse", "/model", "/data", "/data/Metadata", "/"]

            for dir_path in dirs_to_scan:
                try:
                    self._ftp.cwd(dir_path)
                    items = []
                    self._ftp.retrlines("LIST", items.append)

                    for item in items:
                        parts = item.split()
                        if len(parts) >= 5 and not item.startswith("d"):
                            try:
                                total_used += int(parts[4])
                            except ValueError:
                                pass  # Skip entries with non-numeric size fields
                except (OSError, ftplib.Error):
                    pass  # Directory may not exist on this printer model; skip it

            result["used_bytes"] = total_used
        except (OSError, ftplib.Error):
            pass  # Storage scan failed; return whatever info was collected above

        return result if result else None


def describe_upload_failure(failure: FtpFailure | None) -> str:
    """One sentence for the operator, chosen from what actually went wrong.

    Every upload failure used to get the same one: "Failed to upload file to
    printer. Check if SD card is inserted and properly formatted
    (FAT32/exFAT)." #2899's reporter got that after a TLS handshake failure and
    restarted the printer, which could not have helped -- the handshake never
    reached the printer's filesystem, and the state that produced it lives in
    Bambuddy's own memory. #2780 had already removed advice from this failure's
    *log* line for the same reason; it survived in the string people read.

    So the card is named only where the printer itself raised storage, and
    where nothing here can say more, this says so and points at the log rather
    than picking a plausible cause. A wrong instruction costs more than a
    vague one: it sends someone to work on hardware that is fine.
    """
    if failure is None:
        return (
            "Could not upload the file to the printer. See the server log for the reason — "
            "it records what the printer's file service said."
        )

    if failure.kind is FtpFailureKind.STORAGE:
        return (
            f"The printer refused to store the file ({failure.code or 'storage error'}). Check that its SD card "
            "is inserted, has space free, and is formatted FAT32 or exFAT."
        )
    if failure.kind is FtpFailureKind.HANDSHAKE:
        return (
            "The printer's file service answered, but not with TLS, so no file could be sent to it. "
            "Its SD card is not involved. This usually clears by itself; if it does not, power-cycling "
            "the printer has not been found to help either, so please report it."
        )
    if failure.kind is FtpFailureKind.COOLOFF:
        return (
            "Bambuddy is holding off from this printer's file service after a recent failed TLS handshake, "
            "so the file was not sent. This clears on its own within a few minutes."
        )
    if failure.kind is FtpFailureKind.AUTH:
        return (
            "The printer refused the file transfer connection. If the printer's access code changed, "
            "update it on Bambuddy's Printers page."
        )
    if failure.kind is FtpFailureKind.TIMEOUT:
        return (
            "The printer's file service did not respond in time, so the file was not sent. "
            "Check that the printer is on the network and reachable."
        )
    if failure.kind is FtpFailureKind.NOT_FOUND:
        return (
            "The printer rejected the upload path (550). See the server log — this is a Bambuddy-side "
            "problem, not something to fix on the printer."
        )
    return (
        "Could not upload the file to the printer. See the server log for the reason — "
        "it records what the printer's file service said."
    )


def ftps_handshake_blocked(ip_address: str) -> bool:
    """True while this printer's FTPS handshake cool-off is still running.

    Callers that walk a list of candidate paths use this to give up on the
    remaining candidates: the failure is at the transport, below any path, so
    every one of them would fail identically (#2780).
    """
    return BambuFTPClient.handshake_blocked(ip_address)


# Shared 3MF download cache (#972).
#
# Both the cover thumbnail endpoint (api/routes/printers.py) and the archive
# metadata flow (main.py) fetch the same 3MF file over FTP during a print.
# On slow / contended links (A1 Wi-Fi, large files) the duplicate transfers
# compete for the printer's single FTP socket and trigger 425 "can't open
# data channel" errors, feeding back into cause-2's retry storm.
#
# This cache stores the local path of a successfully-downloaded 3MF keyed
# by (printer_id, normalized_name). Whichever flow downloads first populates
# the cache; the other flow reuses the file read-only. Evicted on print
# completion so a later print with the same name re-downloads fresh bytes.
_threemf_path_cache: dict[tuple[int, str], Path] = {}


def normalize_3mf_name(name: str) -> str:
    """Collapse various 3MF filename variants to a cache key.

    Bambu tooling produces names as bare subtask ("Part"), with .3mf, with
    .gcode.3mf, or (Studio-normalized) with spaces → underscores. All of
    these refer to the same print job on the same printer, so they must
    hash to the same cache key.
    """
    # Lowercase first so .3MF / .GCODE.3MF variants strip cleanly — a
    # real-world case since Windows-side tooling sometimes uppercases
    # extensions.
    cleaned = name.strip().lower().replace(".gcode.3mf", "").replace(".gcode", "").replace(".3mf", "")
    return cleaned.replace(" ", "_")


def cache_3mf_download(printer_id: int, name: str, local_path: Path) -> None:
    """Record a successfully-downloaded 3MF so a sibling flow can reuse it."""
    _threemf_path_cache[(printer_id, normalize_3mf_name(name))] = local_path


def get_cached_3mf(printer_id: int, name: str) -> Path | None:
    """Return a cached 3MF path for this printer/name if the file still exists."""
    key = (printer_id, normalize_3mf_name(name))
    cached = _threemf_path_cache.get(key)
    if cached and cached.exists() and cached.stat().st_size > 0:
        return cached
    # Evict dead entry — the file was cleaned up (temp dir clean, manual
    # deletion, restart) so the cache value is no longer usable.
    if cached:
        _threemf_path_cache.pop(key, None)
    return None


def clear_3mf_cache(printer_id: int | None = None, delete_files: bool = True) -> None:
    """Drop cache entries for one printer (or all with None).

    When ``delete_files`` is True (default) the on-disk 3MF is removed as well
    — called from on_print_complete so temp files don't accumulate across
    prints. Tests that want to inspect the cache contents disable this.

    Only paths inside ``archive_dir/temp`` are unlinked. The dispatch sites
    added in #1166 also cache the live archive copy and library file bytes
    so /cover can skip FTP — those are *user data*, never the cache's to
    delete. Pre-fix this branch silently removed archive 3mfs on every print
    completion (#1212 + private reports of "file disappeared overnight").
    """
    from backend.app.core.config import settings as _config_settings

    temp_root = _config_settings.archive_dir / "temp"

    def _is_temp_path(path: Path) -> bool:
        try:
            return path.is_relative_to(temp_root)
        except (OSError, ValueError):
            return False

    def _maybe_unlink(path: Path) -> None:
        if not delete_files or not path.exists():
            return
        if not _is_temp_path(path):
            return
        try:
            path.unlink()
        except OSError as exc:
            logger.debug("3MF cache cleanup skipped %s: %s", path, exc)

    if printer_id is None:
        for path in list(_threemf_path_cache.values()):
            _maybe_unlink(path)
        _threemf_path_cache.clear()
        return
    for key in [k for k in _threemf_path_cache if k[0] == printer_id]:
        _maybe_unlink(_threemf_path_cache[key])
        _threemf_path_cache.pop(key, None)


async def download_file_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
    local_path: Path,
    timeout: float = 60.0,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
    expected_size: int | None = None,
    max_bytes: int | None = None,
    cancel_event: threading.Event | None = None,
    min_free_bytes: int | None = None,
    serialize: bool = True,
) -> bool:
    """Async wrapper for downloading a file with timeout.

    For A1/A1 Mini printers, automatically tries prot_p first, then falls back
    to prot_c if the download fails. The working mode is cached for future operations.

    ``timeout`` bounds the wait for a *result*, not the call: when it expires
    this waits for the FTP worker thread to unwind before returning, because
    the thread owns ``local_path`` until it does and a caller that came back
    early would delete a file still being written. That wait is bounded by the
    socket timeout, so pass ``socket_timeout`` on any path that must not block
    indefinitely -- every caller here does.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        remote_path: Remote file path on printer
        local_path: Local path to save file
        timeout: Overall operation timeout (asyncio)
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
        serialize: take this printer's download gate for the transfer (#2957).
            Pass False from a path that must neither queue behind another
            download nor make one queue behind it -- the printer file browser
            is both, and says so: a preview must not wait out somebody else's
            ten-gigabyte selection, and that selection must not hold the printer
            for the twenty minutes it legitimately takes.
    """
    loop = asyncio.get_event_loop()
    is_a1 = printer_model in BambuFTPClient.A1_MODELS if printer_model else False

    # Per-attempt completion state: asyncio.wait_for cannot cancel
    # run_in_executor threads, so on timeout the executor may still complete
    # the download after we stop waiting. The thread flips `success` to True
    # ONLY after the file is fully written — a post-timeout check lets us
    # salvage the download without mistaking an in-progress partial write
    # for a completed one. Each attempt gets its own dict and event so a
    # zombie from an earlier attempt can't flip the flag for a later one.
    # The event is set in `_download`'s finally block so the post-timeout
    # path can wait for genuine thread completion instead of a fixed sleep.

    class _CombinedCancelEvent:
        def __init__(self, attempt_event: threading.Event):
            self._attempt_event = attempt_event

        def is_set(self) -> bool:
            return self._attempt_event.is_set() or (cancel_event is not None and cancel_event.is_set())

    def _download(
        force_prot_c: bool,
        completion: dict,
        done: threading.Event,
        attempt_cancel: threading.Event,
    ) -> bool:
        mode_str = "prot_c" if force_prot_c else "prot_p"
        try:
            combined_cancel = _CombinedCancelEvent(attempt_cancel)
            if combined_cancel.is_set():
                raise DownloadCancelled(remote_path)
            client = BambuFTPClient(
                ip_address,
                access_code,
                timeout=socket_timeout,
                printer_model=printer_model,
                force_prot_c=force_prot_c,
            )
            if client.connect():
                try:
                    result = client.download_to_file(
                        remote_path,
                        local_path,
                        expected_size=expected_size,
                        max_bytes=max_bytes,
                        cancel_event=combined_cancel,
                        min_free_bytes=min_free_bytes,
                        size_callback=lambda n: completion.__setitem__("size", n),
                    )
                    if result:
                        BambuFTPClient.cache_mode(ip_address, mode_str)
                        completion["success"] = True
                    return result
                finally:
                    client.disconnect()
            return False
        finally:
            done.set()

    async def _run(force_prot_c: bool) -> bool:
        completion = {"success": False}
        done = threading.Event()
        attempt_cancel = threading.Event()
        worker = loop.run_in_executor(_ftp_executor, _download, force_prot_c, completion, done, attempt_cancel)
        # What this attempt was actually allowed, for the log lines below: the
        # size-derived extension moves it after the fact.
        allowed = timeout
        extended = False
        try:
            try:
                return await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
            except TimeoutError:
                # The deadline was set before anyone knew the file's size. Now
                # the printer has told us, so give a transfer that is genuinely
                # under way the time that size needs (#2957). Re-raises into the
                # handler below when the size is unknown or already covered.
                extension = _download_extension(completion.get("size"), timeout)
                if extension <= 0:
                    raise
                logger.info(
                    "FTP download of %s passed its %ss deadline but the printer reports %s bytes — "
                    "allowing %.0fs more rather than declaring a slow transfer dead (#2957)",
                    remote_path,
                    timeout,
                    completion.get("size"),
                    extension,
                )
                allowed = timeout + extension
                extended = True
                return await asyncio.wait_for(asyncio.shield(worker), timeout=extension)
        except asyncio.CancelledError:
            # Cancelling an asyncio Future cannot stop its executor thread. Set
            # the callback-visible flag and do not let the caller unlink the
            # staging file until the worker has genuinely unwound.
            attempt_cancel.set()
            try:
                await asyncio.shield(worker)
            except (DownloadCancelled, OSError, ftplib.Error):
                pass
            raise
        except TimeoutError:
            # Slow WiFi links commonly overshoot ftp_timeout by 10–30 s without
            # actually being stuck, so starting attempt 2 now would just contend
            # with the still-progressing RETR on attempt 1 and produce the
            # zombie-write race reported in #1014 (file landed on disk minutes
            # after the retry loop had already given up). Wait for the worker
            # thread to genuinely finish — capped at 30 s so a truly stuck
            # connection can't stall a whole attempt indefinitely, with a 0.5 s
            # floor so artificially small test timeouts still give zombies a
            # realistic window to finish.
            grace = max(min(timeout, 30.0), 0.5)
            # Deliberately the DEFAULT executor, not `_ftp_executor`: this thread
            # blocks waiting on `_download`, which is itself an `_ftp_executor`
            # worker. Parking waiters in the same bounded pool as the workers they
            # wait for is how you build a deadlock — with enough concurrent
            # timeouts the waiters would occupy every slot and the downloads they
            # are waiting for could never be scheduled.
            attempt_cancel.set()
            await loop.run_in_executor(None, done.wait, grace)
            # Wait for the thread either way. If the grace period was enough it
            # returns at once; if it was not, the blocking socket still has to
            # reach its own timeout, and returning before it does would let the
            # caller unlink a file the executor is still writing.
            try:
                await asyncio.shield(worker)
            except (DownloadCancelled, OSError, ftplib.Error):
                pass
            if completion["success"] and local_path.exists() and local_path.stat().st_size > 0:
                logger.info(
                    "FTP download wait_for timed out after %ss for %s, but thread completed within %ss grace (%s bytes) — salvaging",
                    allowed,
                    remote_path,
                    grace,
                    local_path.stat().st_size,
                )
                return True
            if extended:
                # The transfer had already been given the time its own reported
                # size needs. Retrying spends that again to learn the same
                # thing, so say so rather than reporting an ordinary miss.
                logger.warning(
                    "FTP download of %s did not finish inside the %ss its size bought it (plus %ss grace)",
                    remote_path,
                    allowed,
                    grace,
                )
                raise DownloadDeadlineExceeded(remote_path)
            logger.warning(
                "FTP download timed out after %ss (plus %ss grace) for %s",
                allowed,
                grace,
                remote_path,
            )
            return False

    # Check if we have a cached mode for this printer
    cached_mode = BambuFTPClient._mode_cache.get(ip_address)

    # The gate spans the prot_c fallback too: those are two attempts at one
    # transfer, and letting go between them would hand the printer to a waiter
    # mid-download (#2957).
    async with _serialized_download(ip_address, f"a download of {remote_path}", enabled=serialize):
        if cached_mode:
            force_prot_c = cached_mode == "prot_c"
            return await _run(force_prot_c)

        # No cached mode - try prot_p first
        if await _run(False):
            return True

        # Download failed - for A1 models, try prot_c fallback
        if is_a1:
            logger.info("FTP download failed with prot_p for A1 model, trying prot_c fallback...")
            return await _run(True)

        return False


async def download_file_try_paths_async(
    ip_address: str,
    access_code: str,
    remote_paths: list[str],
    local_path: Path,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
    timeout: float = 90.0,
) -> str | None:
    """Try downloading a file from multiple paths using a single connection.

    Returns the path that served the file, or ``None``. The path rather than a
    bare flag because the caller usually cannot tell afterwards which candidate
    hit, and on a printer that keeps uploads around for weeks that is the
    difference between a diagnosable stale-copy match and an invisible one
    (#1820). Callers testing it for truth are unaffected: a served path is
    always a non-empty string.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
        timeout: overall async cap. The per-socket timeout only bounds an
            in-flight worker; it does NOT bound how long this coroutine waits
            for a free slot in the fixed-size ``_ftp_executor``. On a large
            farm where offline printers keep every worker busy on dead
            connects, that queue wait is otherwise unbounded — and any caller
            holding a DB connection while awaiting this would pin it until the
            pool is exhausted (#2572). The cap converts that into a bounded
            wait; the worker is then cancelled and waited out rather than
            orphaned (#2957), so a DB-holding caller's worst case is the gate
            wait plus this cap plus one unwind -- still bounded, and the
            orphaned worker no longer keeps the printer's socket after it.
    """
    loop = asyncio.get_event_loop()
    # An executor thread cannot be cancelled, so the cap alone used to leave a
    # worker walking the remaining paths -- still holding the printer's FTP
    # socket -- long after this coroutine had given up on it. A reporter's log
    # shows one of those still going as the archive flow's own download landed,
    # two Bambuddy transfers deep into a P1S that was mid-print (#2957). The
    # flag stops it at the next chunk instead.
    cancel = threading.Event()
    done = threading.Event()

    def _download():
        try:
            client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
            if not client.connect():
                return None

            try:
                # FileNotOnPrinterError signals "try the next path", not "give up" —
                # this function's whole purpose is to walk a list of candidates
                # over one connection. Only a real transport error should bubble.
                for remote_path in remote_paths:
                    if cancel.is_set():
                        return None
                    try:
                        if client.download_to_file(remote_path, local_path, cancel_event=cancel):
                            return remote_path
                    except FileNotOnPrinterError:
                        continue
                    except DownloadCancelled:
                        return None
                return None
            finally:
                client.disconnect()
        finally:
            done.set()

    async with _serialized_download(ip_address, f"a {len(remote_paths)}-path lookup"):
        worker = loop.run_in_executor(_ftp_executor, _download)
        try:
            return await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
        except asyncio.CancelledError:
            # The caller is going away and should not be made to wait, but the
            # worker must not keep the printer to itself either.
            cancel.set()
            _discard_worker_outcome(worker)
            raise
        except TimeoutError:
            logger.warning("FTP download_try_paths exceeded its %ss cap for %s (#2572)", timeout, ip_address)
            cancel.set()
            # Do not hand the printer to the next download while this worker is
            # still on its socket. The DEFAULT executor, never ``_ftp_executor``:
            # parking a waiter in the same bounded pool as the worker it waits
            # for is how a deadlock gets built.
            await loop.run_in_executor(None, done.wait, _DOWNLOAD_UNWIND_SECONDS)
            _discard_worker_outcome(worker)
            return None


def _upload_deadline(local_path: Path) -> float:
    """Derive an upload deadline from the file size (#2529).

    See ``_UPLOAD_FLOOR_BYTES_PER_SEC``. An unstat-able file falls back to the
    floor timeout — ``upload_file`` will fail on the open() anyway.
    """
    try:
        size = local_path.stat().st_size
    except OSError:
        return _UPLOAD_MIN_TIMEOUT
    return max(_UPLOAD_MIN_TIMEOUT, size / _UPLOAD_FLOOR_BYTES_PER_SEC)


# One upload at a time per printer. Two concurrent STOR commands for the same
# remote path leave a corrupt file on the SD card, and the printer reads as
# flaky rather than busy (#2529). Held for the duration of a transfer, so a
# second dispatch to the same printer queues behind the first instead of racing
# it. Keyed per event loop: an asyncio.Lock binds to the loop that first awaits
# it, and the test suite runs each case on a fresh loop.
_upload_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _upload_lock(loop: asyncio.AbstractEventLoop, ip_address: str) -> asyncio.Lock:
    per_loop = _upload_locks.setdefault(loop, {})
    lock = per_loop.get(ip_address)
    if lock is None:
        lock = asyncio.Lock()
        per_loop[ip_address] = lock
    return lock


async def upload_file_async(
    ip_address: str,
    access_code: str,
    local_path: Path,
    remote_path: str,
    timeout: float | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
    respect_handshake_cooloff: bool = True,
    failure: FtpFailureReport | None = None,
) -> bool:
    """Async wrapper for uploading a file with timeout and progress callback.

    For A1/A1 Mini printers, automatically tries prot_p first, then falls back
    to prot_c if the upload fails. The working mode is cached for future uploads.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        local_path: Local file path to upload
        remote_path: Remote path on printer
        timeout: Overall deadline. ``None`` (the default) derives it from the
            file size — see ``_upload_deadline``. A caller that passes a number
            gets exactly that, which is what the tests rely on.
        progress_callback: Optional callback for progress updates
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
        respect_handshake_cooloff: see ``BambuFTPClient.__init__``. False for a
            user-initiated upload, whose attempts are bounded and were being
            spent against a cool-off that outlives them (#2898).
        failure: caller-owned slot filled in with why the upload failed, so the
            caller can say something true about it instead of guessing (#2899).
            Passed through ``with_ftp_retry`` unchanged, so it ends up holding
            the last attempt's reason -- which is the one that decided the
            outcome.
    """
    loop = asyncio.get_event_loop()
    is_a1 = printer_model in BambuFTPClient.A1_MODELS if printer_model else False
    deadline = _upload_deadline(local_path) if timeout is None else timeout

    # Set when the deadline expires. The worker checks it once per chunk.
    cancel = threading.Event()

    def _guarded_progress(uploaded: int, total: int) -> None:
        if cancel.is_set():
            raise UploadCancelled(f"upload of {remote_path} exceeded its {deadline:.0f}s deadline")
        if progress_callback:
            progress_callback(uploaded, total)

    def _upload(force_prot_c: bool = False) -> bool:
        mode_str = "prot_c" if force_prot_c else "prot_p"
        logger.info(
            f"FTP connecting to {ip_address} for upload (model={printer_model}, "
            f"mode={mode_str}, socket_timeout={socket_timeout}s, deadline={deadline:.0f}s)..."
        )
        client = BambuFTPClient(
            ip_address,
            access_code,
            timeout=socket_timeout,
            printer_model=printer_model,
            force_prot_c=force_prot_c,
            respect_handshake_cooloff=respect_handshake_cooloff,
        )
        try:
            if client.connect():
                logger.info("FTP connected to %s", ip_address)
                try:
                    result = client.upload_file(local_path, remote_path, _guarded_progress)
                    if result:
                        # Cache the working mode
                        BambuFTPClient.cache_mode(ip_address, mode_str)
                    return result
                finally:
                    client.disconnect()
            logger.warning("FTP connection failed to %s", ip_address)
            return False
        finally:
            # In a finally so a transfer that leaves by raising -- a cancelled
            # upload, a re-raised STOR rejection -- still reports what the
            # client recorded on its way out.
            if failure is not None and client.last_failure is not None:
                failure.failure = client.last_failure

    async def _attempt(force_prot_c: bool) -> bool:
        """Run one upload attempt, and make a timeout actually stop the transfer.

        ``asyncio.wait_for`` cancels the *future*, never the executor thread
        behind it. Before #2529 a slow-but-healthy upload that overran the
        deadline left that thread streaming: it kept pushing bytes, kept firing
        the progress callback, and the retry above put a *second* STOR of the
        same file onto the same printer. The reporter's 96 MB job ran four
        concurrent transfers and never landed. So on timeout we signal the
        worker (it raises ``UploadCancelled`` from the progress callback, which
        breaks the send loop and deletes the partial file) and wait for it to
        actually go.
        """
        fut = loop.run_in_executor(_ftp_executor, lambda: _upload(force_prot_c))
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=deadline)
        except TimeoutError:
            cancel.set()
            logger.warning(
                "FTP upload of %s exceeded its %.0fs deadline — cancelling the transfer",
                remote_path,
                deadline,
            )
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout=_UPLOAD_CANCEL_GRACE)
            except UploadCancelled:
                logger.info("FTP upload of %s cancelled; partial file removed from the printer", remote_path)
            except TimeoutError:
                # The thread is wedged somewhere that never reaches the callback
                # (a blocked sendall, say). Nothing more we can do from here —
                # but consume the eventual result so asyncio doesn't log the
                # future's exception as unretrieved when it is garbage-collected.
                logger.error(
                    "FTP upload thread for %s did not stop within %.0fs of the cancel signal",
                    remote_path,
                    _UPLOAD_CANCEL_GRACE,
                )
                fut.add_done_callback(_swallow_future_result)
            except Exception as e:
                logger.warning("FTP upload of %s errored while cancelling: %s", remote_path, e)
            # Raise rather than return False: a deadline expiry means the link
            # sustained less than the floor rate for the whole transfer, and a
            # retry would only spend another full deadline finding that out
            # again — with check_queue serialized, four of those block the
            # entire print queue for hours. ``with_ftp_retry`` never retries it.
            raise UploadCancelled(
                f"Upload of {remote_path} to {ip_address} exceeded its {deadline:.0f}s deadline "
                f"(link sustained less than {_UPLOAD_FLOOR_BYTES_PER_SEC // 1024} KB/s)"
            ) from None

    async with _upload_lock(loop, ip_address):
        # Check if we have a cached mode for this printer
        cached_mode = BambuFTPClient._mode_cache.get(ip_address)

        if cached_mode:
            # Use cached mode
            return await _attempt(cached_mode == "prot_c")

        # No cached mode - try prot_p first
        if await _attempt(False):
            return True

        # Upload failed - for A1 models, try prot_c fallback
        if is_a1:
            logger.info("FTP upload failed with prot_p for A1 model, trying prot_c fallback...")
            return await _attempt(True)

        return False


def _swallow_future_result(fut: asyncio.Future) -> None:
    """Retrieve a future's exception so asyncio doesn't log it as unhandled."""
    if not fut.cancelled():
        fut.exception()


async def list_files_async(
    ip_address: str,
    access_code: str,
    path: str = "/",
    timeout: float = 30.0,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> list[dict]:
    """Async wrapper for listing files with timeout.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _list():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.list_files(path)
            finally:
                client.disconnect()
        return []

    try:
        return await asyncio.wait_for(loop.run_in_executor(_ftp_executor, _list), timeout=timeout)
    except TimeoutError:
        logger.warning("FTP list_files timed out after %ss for %s", timeout, path)
        return []


async def list_files_result_async(
    ip_address: str,
    access_code: str,
    path: str = "/",
    timeout: float = 30.0,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> FileListResult:
    """List a directory without collapsing transport failure into empty."""

    loop = asyncio.get_event_loop()

    def _list() -> FileListResult:
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if not client.connect():
            return FileListResult(files=[], available=False)
        try:
            return FileListResult(files=client.list_files(path, raise_on_error=True), available=True)
        except (OSError, ftplib.Error):
            return FileListResult(files=[], available=False)
        finally:
            client.disconnect()

    try:
        return await asyncio.wait_for(loop.run_in_executor(_ftp_executor, _list), timeout=timeout)
    except TimeoutError:
        logger.warning("FTP list_files timed out after %ss for %s", timeout, path)
        return FileListResult(files=[], available=False)


async def find_remote_file_async(
    ip_address: str,
    access_code: str,
    remote_paths: list[str],
    timeout: float = 30.0,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> str | None:
    """First of *remote_paths* the printer actually has, or None.

    Answers "is this file there?" without fetching it, over a single
    connection: one listing per distinct directory, reused across the
    candidates that share it, and stops at the first hit. Written for the
    connection diagnostic (#2856), which needs the answer for a file that can
    be tens of megabytes and has no use for its contents.

    Listing rather than ``SIZE``: LIST is what every Bambu firmware here is
    known to answer, and a ``SIZE`` the server simply does not implement would
    read as "the file is missing".
    """
    loop = asyncio.get_event_loop()

    def _find() -> str | None:
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if not client.connect():
            return None
        try:
            listed: dict[str, set[str]] = {}
            for remote_path in remote_paths:
                directory, _, name = remote_path.rpartition("/")
                directory = directory or "/"
                if directory not in listed:
                    listed[directory] = {
                        entry.get("name") for entry in client.list_files(directory) if not entry.get("is_directory")
                    }
                if name in listed[directory]:
                    return remote_path
            return None
        finally:
            client.disconnect()

    try:
        return await asyncio.wait_for(loop.run_in_executor(_ftp_executor, _find), timeout=timeout)
    except TimeoutError:
        logger.warning("FTP find_remote_file timed out after %ss on %s", timeout, ip_address)
        return None


async def delete_file_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
    timeout: float = 60.0,
    respect_handshake_cooloff: bool = True,
) -> DeleteResult:
    """Async wrapper for deleting a file.

    Returns :class:`DeleteResult` so callers can distinguish ``NOT_FOUND``
    (550 — file isn't on the printer, no retry value) from ``FAILED``
    (network / auth / transient — worth retrying or surfacing).

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
        timeout: overall async cap so a saturated ``_ftp_executor`` can't pin
            the caller (and any DB connection it holds) indefinitely (#2572).
        respect_handshake_cooloff: see ``BambuFTPClient.__init__``. The delete
            that clears the way for a dispatch shares the upload's exemption --
            it is one connection, and in #2898's trace it is the one that armed
            the cool-off the upload then spent all four attempts against.
    """
    loop = asyncio.get_event_loop()

    def _delete() -> DeleteResult:
        client = BambuFTPClient(
            ip_address,
            access_code,
            timeout=socket_timeout,
            printer_model=printer_model,
            respect_handshake_cooloff=respect_handshake_cooloff,
        )
        if client.connect():
            try:
                return client.delete_file(remote_path)
            finally:
                client.disconnect()
        return DeleteResult.FAILED

    try:
        return await asyncio.wait_for(loop.run_in_executor(_ftp_executor, _delete), timeout=timeout)
    except TimeoutError:
        logger.warning("FTP delete_file exceeded its %ss cap for %s (#2572)", timeout, ip_address)
        return DeleteResult.FAILED


async def download_file_bytes_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
    timeout: float = 300.0,
    expected_size: int | None = None,
) -> bytes | None:
    """Async wrapper for downloading file as bytes.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
        timeout: overall async cap so a saturated ``_ftp_executor`` can't pin
            the caller (and any DB connection it holds) indefinitely (#2572).
            Generous by default because this pulls whole files (timelapse
            video, gcode) which can legitimately take minutes over slow Wi-Fi —
            the cap only guards against a permanently-starved pool, not a
            slow-but-progressing transfer.
        expected_size: size from the directory listing; a mismatch fails the
            download instead of returning a truncated file. See
            :meth:`BambuFTPClient.download_file`.
    """
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.download_file(remote_path, expected_size=expected_size)
            finally:
                client.disconnect()
        return None

    try:
        return await asyncio.wait_for(loop.run_in_executor(_ftp_executor, _download), timeout=timeout)
    except TimeoutError:
        logger.warning("FTP download_bytes exceeded its %ss cap for %s (#2572)", timeout, ip_address)
        return None


async def remote_file_settled(
    ip_address: str,
    access_code: str,
    remote_path: str,
    downloaded_bytes: int,
    *,
    printer_model: str | None = None,
) -> bool:
    """Confirm the printer has finished writing the file we just downloaded.

    Matching the download against the size from the directory listing proves we
    received what the listing *said*, not that the file was *finished*. The
    timelapse scan's first look happens seconds after the print ends, which is
    exactly when the printer is writing the video — so a file still growing can
    be listed at a partial size, served at that size, and pass the length check
    as a complete video (#2704).

    That was survivable while the printer kept its copy. It isn't now that a
    successful attach deletes the source, so re-list afterwards: if the file has
    grown, what we hold is a prefix and the caller should discard it and try
    again on the next round.

    Returns True when the remote file can no longer differ from what we hold —
    the size still matches, or the file is gone from the listing entirely and
    so cannot grow any further. Returns False when it has changed size, and on
    a listing failure, because "we could not check" must not read as "safe to
    delete".
    """
    directory, _, name = remote_path.rpartition("/")
    files = await list_files_async(ip_address, access_code, directory or "/", printer_model=printer_model)
    if not files:
        logger.warning("[TIMELAPSE] Could not re-list %s to confirm %s is complete", directory or "/", name)
        return False

    for f in files:
        if f.get("name") == name:
            size = f.get("size")
            if size == downloaded_bytes:
                return True
            logger.info(
                "[TIMELAPSE] %s is still being written (%s bytes now, %s when downloaded) — will retry",
                name,
                size,
                downloaded_bytes,
            )
            return False

    # Vanished between the download and now. Nothing left that could grow, and
    # nothing left to delete either.
    logger.debug("[TIMELAPSE] %s is no longer on the printer after download", name)
    return True


async def delete_archived_timelapse(
    ip_address: str,
    access_code: str,
    remote_path: str,
    *,
    verified: bool,
    printer_model: str | None = None,
    printer_name: str = "",
) -> bool:
    """Remove a timelapse from the printer once it is safely in the archive.

    Call this only after the attach succeeded (#2704). Keeping ``/timelapse``
    down to just the unclaimed videos is what makes the snapshot diff
    unambiguous rather than merely usually-right, and it stops P1S cards
    filling with AVIs.

    ``verified`` must say whether the downloaded byte count was checked against
    the size the directory listing reported. It is required rather than
    defaulted because this is the one irreversible step in the flow: an FTPS
    data connection that closes early does not always raise, so an unverified
    transfer can be a partial file that looks complete, and deleting the source
    would then destroy the only good copy. The check lives here rather than at
    each call site so no future caller can omit it.

    Best-effort otherwise: a printer that refuses the delete keeps its copy, the
    diff still excludes that filename next time because it is attached to an
    archive, and nothing else in the flow cares. Returns True only on an actual
    delete or a 550 (already gone).
    """
    if not verified:
        logger.warning(
            "[TIMELAPSE] Not deleting %s from printer %s: the download was never size-checked",
            remote_path,
            printer_name,
        )
        return False

    for attempt in range(1, 4):
        try:
            result = await delete_file_async(ip_address, access_code, remote_path, printer_model=printer_model)
        except Exception as e:
            result = DeleteResult.FAILED
            logger.warning("[TIMELAPSE] Delete attempt %d/3 raised for %s: %s", attempt, remote_path, e)

        if result == DeleteResult.DELETED:
            logger.info("[TIMELAPSE] Deleted %s from printer %s after archiving", remote_path, printer_name)
            return True
        if result == DeleteResult.NOT_FOUND:
            # 550 never recovers by waiting — the printer already cleaned up.
            logger.debug("[TIMELAPSE] %s already gone from printer %s", remote_path, printer_name)
            return True
        if attempt < 3:
            await asyncio.sleep(2)

    logger.warning(
        "[TIMELAPSE] Could not delete %s from printer %s (it stays on the card; the archive copy is unaffected)",
        remote_path,
        printer_name,
    )
    return False


async def get_storage_info_async(
    ip_address: str,
    access_code: str,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
    timeout: float = 60.0,
) -> dict | None:
    """Async wrapper for getting storage info.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
        timeout: overall async cap so a saturated ``_ftp_executor`` can't pin
            the caller (and any DB connection it holds) indefinitely (#2572).
    """
    loop = asyncio.get_event_loop()

    def _get_storage():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.get_storage_info()
            finally:
                client.disconnect()
        return None

    try:
        return await asyncio.wait_for(loop.run_in_executor(_ftp_executor, _get_storage), timeout=timeout)
    except TimeoutError:
        logger.warning("FTP get_storage_info exceeded its %ss cap for %s (#2572)", timeout, ip_address)
        return None


async def get_ftp_retry_settings() -> tuple[bool, int, float, float]:
    """Get FTP retry settings from database.

    Returns:
        Tuple of (retry_enabled, retry_count, retry_delay, timeout)
    """
    from backend.app.api.routes.settings import get_setting
    from backend.app.core.database import async_session

    async with async_session() as db:
        enabled = (await get_setting(db, "ftp_retry_enabled") or "true") == "true"
        count = int(await get_setting(db, "ftp_retry_count") or "3")
        delay = float(await get_setting(db, "ftp_retry_delay") or "2")
        timeout = float(await get_setting(db, "ftp_timeout") or "30")
    return enabled, count, delay, timeout


async def with_ftp_retry(
    operation: Callable[..., Awaitable[T]],
    *args,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    operation_name: str = "FTP operation",
    non_retry_exceptions: tuple[type[BaseException], ...] = (),
    cooloff_ip: str | None = None,
    **kwargs,
) -> T | None:
    """Execute FTP operation with retry logic.

    Args:
        operation: Async function to execute
        *args: Positional arguments for the operation
        max_retries: Number of retry attempts (default: 3)
        retry_delay: Seconds to wait between retries (default: 2.0)
        operation_name: Name for logging purposes
        non_retry_exceptions: Exception types that should immediately abort retries
        cooloff_ip: printer IP whose FTPS handshake cool-off should end the loop
            early. Pass it from any caller that respects the cool-off; leave it
            unset for one that opted out, or the loop would stop on a gate its
            own attempts are ignoring (#2898).
        **kwargs: Keyword arguments for the operation

    Returns:
        Result of the operation, or None if all attempts fail

    ``UploadCancelled`` is never retried, whatever the caller passes: it means
    the transfer overran its size-derived deadline, so a retry would spend
    another full deadline reaching the same conclusion (#2529).
    ``DownloadDeadlineExceeded`` is the same thing in the other direction
    (#2957) and is treated the same way.
    """
    last_error = None
    attempts_made = 0

    for attempt in range(max_retries + 1):
        attempts_made = attempt + 1
        try:
            result = await operation(*args, **kwargs)
            # Check for "falsy" success indicators
            if result not in (False, None, []):
                if attempt > 0:
                    logger.info("%s succeeded on attempt %s/%s", operation_name, attempt + 1, max_retries + 1)
                return result
            # Operation returned failure indicator
            if attempt > 0:
                logger.info("%s attempt %s/%s returned failure", operation_name, attempt + 1, max_retries + 1)
        except (UploadCancelled, DownloadDeadlineExceeded):
            raise
        except Exception as e:
            if non_retry_exceptions and isinstance(e, non_retry_exceptions):
                raise
            last_error = e
            logger.warning("%s attempt %s/%s failed: %s", operation_name, attempt + 1, max_retries + 1, e)

        # Don't wait after the last attempt
        if attempt < max_retries:
            # A cool-off outlasts this loop by two orders of magnitude, so once
            # it is armed every remaining attempt returns False without opening
            # a socket. Spending them anyway bought nothing and cost the caller
            # `max_retries * retry_delay` seconds of sleeping, then reported the
            # failure with the wrong reason (#2898).
            if cooloff_ip and ftps_handshake_blocked(cooloff_ip):
                logger.warning(
                    "%s: stopping after attempt %s/%s — %s is inside its FTPS handshake cool-off, "
                    "so the remaining attempts would not reach it",
                    operation_name,
                    attempt + 1,
                    max_retries + 1,
                    cooloff_ip,
                )
                break
            logger.info("%s will retry in %ss...", operation_name, retry_delay)
            await asyncio.sleep(retry_delay)

    # attempts_made, not max_retries + 1: the loop can stop early on a cool-off,
    # and reporting attempts that were never made is how #2898 read as a network
    # problem when nothing had gone near the network.
    logger.error("%s failed after %s attempts", operation_name, attempts_made)
    if last_error:
        logger.debug("Last error: %s", last_error)
    return None
