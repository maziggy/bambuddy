"""Brother QL raster protocol over TCP 9100 (#3612), QL-800/810W/820NWB.

Written against Brother's "Raster Command Reference QL-800/810W/820NWB" and
checked byte for byte against brother_ql's output for the same image (reference
only, not a dependency). Everything before ``send`` is pure.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageOps

from backend.app.services.label_printers.base import (
    LabelPrinterConfig,
    PreparedJob,
    PrinterBusy,
    PrinterDidNotFinish,
    PrinterReportedError,
    PrinterStatus,
    PrinterUnreachable,
    WrongMedia,
)
from backend.app.services.label_printers.brother_ql_media import (
    BYTES_PER_LINE,
    FEED_MARGIN_DOTS,
    HEAD_PINS,
    MEDIA_TYPE_CONTINUOUS,
    MODELS_WITHOUT_COMPRESSION,
    TapeSpec,
    tape_spec,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

# Clears the command buffer of the QL-800 family (the reference: 400 bytes).
INVALIDATE = b"\x00" * 400
INITIALIZE = b"\x1b@"  # ESC @
STATUS_REQUEST = b"\x1biS"  # ESC i S
STATUS_SIZE = 32
# Everything that precedes the first page: clear the buffer, initialise, raster mode.
JOB_PREAMBLE = INVALIDATE + INITIALIZE + b"\x1bia\x01"

# Time limits (#3612). The printer is slow to take a big job and slower to print
# it, so the limits scale with the job; they only bite when the printer stops
# answering. After the connect and the status request, ONE deadline covers
# sending the job and printing it: send budget + finish budget, measured from
# the start of the send (a printer that reads slowly eats into the same
# allowance it would need to print).
CONNECT_TIMEOUT_S = 5.0
STATUS_TIMEOUT_S = 2.0
# Sending: a base plus the job at this (pessimistic) rate.
SEND_BASE_S = 10.0
SEND_MIN_BYTES_PER_S = 20_000
# Printing, from the end of the send: per label (feed, cut) plus per raster line.
FINISH_PAGE_S = 5.0
FINISH_LINE_S = 0.005
# A printer that answers the status request but then says nothing for this long
# after the job is taken to have no status channel; its job counts as printed.
SILENT_GRACE_S = 5.0
# "Cooling" notification: the printer pauses. Each one extends the deadline,
# and only when it happens: the normal bound does not include any of it.
COOLING_EXTENSION_S = 120.0
MAX_COOLING_EXTENSIONS = 3
# After the job: half-close and read until the printer closes, so the close is
# a FIN and not a reset that could drop the tail of the job.
CLOSE_DRAIN_S = 1.0
# How long a job waits for the printer's lock (a job whose caller gave up may
# still hold it for a moment), and how often blocking calls look at ``abandon``.
LOCK_TIMEOUT_S = 30.0
ABANDON_POLL_S = 0.5
SEND_CHUNK_BYTES = 65536
# Grey levels up to this print black.
BLACK_THRESHOLD = 128
# Rendering at 300 dpi may land a pixel or two off the exact dot width.
FIT_TOLERANCE_DOTS = 2

# Error information 1 (byte 8) and 2 (byte 9) of the status, bit -> reason.
_ERRORS_1 = {
    0: "no tape loaded",
    1: "end of tape",
    2: "cutter jam",
    4: "printer in use",
    5: "printer turned off",
    7: "fan motor error",
}
_ERRORS_2 = {
    0: "wrong tape loaded",
    1: "printer buffer full",
    2: "communication error",
    4: "cover open",
    6: "tape cannot be fed",
    7: "system error",
}
_MEDIA_TYPES = {0x00: "none", 0x0A: "continuous", 0x0B: "die-cut"}
_STATUS_TYPES = {
    0x00: "reply",
    0x01: "completed",
    0x02: "error",
    0x04: "turned off",
    0x05: "notification",
    0x06: "phase",
}
_PHASES = {0x00: "waiting", 0x01: "printing"}


# ── Raster ───────────────────────────────────────────────────────────────────


def packbits(data: bytes) -> bytes:
    """TIFF PackBits: a run of 3 or more equal bytes becomes (1 - n, byte); the rest
    is copied in literal blocks of at most 128 bytes."""
    out = bytearray()
    literal = bytearray()

    def flush() -> None:
        for i in range(0, len(literal), 128):
            chunk = literal[i : i + 128]
            out.append(len(chunk) - 1)
            out.extend(chunk)
        literal.clear()

    i, n = 0, len(data)
    while i < n:
        run = 1
        while i + run < n and run < 128 and data[i + run] == data[i]:
            run += 1
        if run >= 3 or (run == 2 and not literal):
            flush()
            out.append(257 - run)
            out.append(data[i])
        else:
            literal += data[i : i + run]
        i += run
    flush()
    return bytes(out)


def fit_to_tape(image: Image.Image, tape: str) -> Image.Image:
    """Pad or crop a pixel or two of width so the image is exactly the tape's
    printable width; anything further off is a caller bug."""
    spec = tape_spec(tape)
    diff = spec.printable_dots - image.width
    if diff == 0:
        return image
    if abs(diff) > FIT_TOLERANCE_DOTS:
        raise ValueError(f"Image is {image.width} dots wide, the {tape} mm tape prints {spec.printable_dots}")
    canvas = Image.new("L", (spec.printable_dots, image.height), 255)
    canvas.paste(image.convert("L"), (0, 0))
    return canvas


def rasterize(image: Image.Image, tape: str) -> list[bytes]:
    """The 90-byte raster lines of ``image``, whose width is the tape's printable dots.

    Black is a set bit. The head is addressed right to left, so each line is
    the image row mirrored, placed ``left_margin_pins`` into the 720 pins.
    """
    spec = tape_spec(tape)
    if image.width != spec.printable_dots:
        raise ValueError(f"Image is {image.width} dots wide, the {tape} mm tape prints {spec.printable_dots}")
    # Black pixels become 255, then the row is mirrored and set into the 720 pins.
    black = image.convert("L").point([255 if v < BLACK_THRESHOLD else 0 for v in range(256)])
    canvas = Image.new("L", (HEAD_PINS, black.height), 0)
    canvas.paste(ImageOps.mirror(black), (spec.left_margin_pins, 0))
    raw = canvas.convert("1", dither=Image.Dither.NONE).tobytes()
    return [raw[i : i + BYTES_PER_LINE] for i in range(0, len(raw), BYTES_PER_LINE)]


def _page_header(spec: TapeSpec, line_count: int, *, cut: bool, compress: bool) -> bytes:
    header = bytearray()
    # ESC i z: valid flags (kind, width, length, quality, recover), media type,
    # width in mm, length 0 (continuous), raster lines, page byte 0. brother_ql
    # sends 0 on every page and ends every page with 0x1A, so each page is a
    # complete job of its own (no form feed).
    header += b"\x1biz" + bytes([0xCE, MEDIA_TYPE_CONTINUOUS, spec.media_width_byte, 0])
    header += struct.pack("<L", line_count) + b"\x00\x00"
    header += b"\x1biM" + bytes([0x40 if cut else 0x00])  # auto cut
    if cut:
        header += b"\x1biA\x01"  # cut every label
    header += b"\x1biK" + bytes([0x08 if cut else 0x00])  # expanded mode: cut at end
    header += b"\x1bid" + struct.pack("<H", FEED_MARGIN_DOTS)
    if compress:
        header += b"M\x02"  # TIFF (PackBits) compression
    return bytes(header)


def build_page(image: Image.Image, tape: str, *, cut: bool = True, compress: bool = True) -> tuple[bytes, int]:
    """One label as the bytes brother_ql sends per image, and its raster line count."""
    spec = tape_spec(tape)
    lines = rasterize(image, tape)
    page = bytearray(_page_header(spec, len(lines), cut=cut, compress=compress))
    for line in lines:
        data = packbits(line) if compress else line
        page += b"g\x00" + bytes([len(data)]) + data
    page += b"\x1a"  # print with feed
    return bytes(page), len(lines)


def build_job(images: Iterable[Image.Image], tape: str, *, cut: bool = True, compress: bool = True) -> bytes:
    """The whole print job: one page per image, each of the tape's printable width."""
    return prepare_job(images, tape, cut=cut, compress=compress).data


def prepare_job(images: Iterable[Image.Image], tape: str, *, cut: bool = True, compress: bool = True) -> PreparedJob:
    """Rasterise the images one at a time: only the current page's image and the
    compact bytes of the finished ones are alive, whatever the number of labels."""
    job = bytearray(JOB_PREAMBLE)
    pages = lines = 0
    iterator = iter(images)
    while (image := next(iterator, None)) is not None:
        page, line_count = build_page(fit_to_tape(image, tape), tape, cut=cut, compress=compress)
        del image  # the loop variable would otherwise hold it until the next page exists
        job += page
        pages += 1
        lines += line_count
    if not pages:
        raise ValueError("Nothing to print")
    return PreparedJob(bytes(job), pages, lines)


# ── Status ───────────────────────────────────────────────────────────────────


def parse_status(data: bytes) -> PrinterStatus:
    """Decode the 32-byte status block (starts 80 20 42)."""
    if len(data) < STATUS_SIZE or data[:3] != b"\x80\x20\x42":
        raise ValueError("Not a Brother QL status block")
    errors = [text for bit, text in _ERRORS_1.items() if data[8] & (1 << bit)]
    errors += [text for bit, text in _ERRORS_2.items() if data[9] & (1 << bit)]
    status_type = data[18]
    return PrinterStatus(
        media_width_mm=data[10],
        media_type=_MEDIA_TYPES.get(data[11], f"unknown ({data[11]:#04x})"),
        errors=errors,
        status_type=_STATUS_TYPES.get(status_type, f"unknown ({status_type:#04x})"),
        phase=_PHASES.get(data[19], f"unknown ({data[19]:#04x})"),
        # Notification 0x03: cooling started. The printer waits, nothing failed.
        cooling=status_type == 0x05 and data[22] == 0x03,
    )


# ── Network ──────────────────────────────────────────────────────────────────

_STATUS_MAGIC = b"\x80\x20\x42"
# One print at a time per printer: the QL takes a single connection and a second
# job would interleave with the first. Keyed by the host string as configured,
# not by the resolved address: resolving would block, and one printer is
# configured by one name.
_PRINTER_LOCKS: dict[tuple[str, int], threading.Lock] = {}
_PRINTER_LOCKS_GUARD = threading.Lock()


def _printer_lock(host: str, port: int) -> threading.Lock:
    with _PRINTER_LOCKS_GUARD:
        return _PRINTER_LOCKS.setdefault((host, port), threading.Lock())


def _acquire(lock: threading.Lock, timeout: float, abandon: threading.Event | None) -> bool:
    """``lock.acquire`` that gives up after ``timeout`` or once ``abandon`` is set."""
    if timeout <= 0:
        return lock.acquire(blocking=False)  # no waiting: free now or busy (#3612)
    deadline = time.monotonic() + timeout
    while True:
        if abandon is not None and abandon.is_set():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if lock.acquire(timeout=min(remaining, ABANDON_POLL_S)):
            return True


def send_timeout_s(job_size: int) -> float:
    return SEND_BASE_S + job_size / SEND_MIN_BYTES_PER_S


def finish_timeout_s(pages: int, lines: int) -> float:
    return FINISH_PAGE_S * pages + FINISH_LINE_S * lines


def job_timeout_s(job: PreparedJob) -> float:
    """The time ``send`` needs at most for ``job`` when the printer never pauses:
    connect, status, the one send-and-print deadline, and the close."""
    return (
        CONNECT_TIMEOUT_S
        + STATUS_TIMEOUT_S
        + send_timeout_s(len(job.data))
        + finish_timeout_s(job.pages, job.lines)
        + CLOSE_DRAIN_S
    )


def max_job_timeout_s(job: PreparedJob) -> float:
    """The hard bound: ``job_timeout_s`` plus every cooling pause the printer may
    announce, and the wait for the lock."""
    return job_timeout_s(job) + COOLING_EXTENSION_S * MAX_COOLING_EXTENSIONS + LOCK_TIMEOUT_S


def _check_status(status: PrinterStatus, spec: TapeSpec) -> None:
    if status.errors:
        raise PrinterReportedError(status.errors)
    if status.status_type in ("error", "turned off"):
        raise PrinterReportedError(_unnamed_error(status))
    if status.media_type == "none" or status.media_width_mm == 0:
        raise PrinterReportedError(["no tape loaded"])
    if status.media_type != "continuous":
        raise WrongMedia(f"{spec.tape_mm} mm", f"{status.media_width_mm} mm {status.media_type}")
    if status.media_width_mm != spec.tape_mm:
        raise WrongMedia(f"{spec.tape_mm} mm", f"{status.media_width_mm} mm")


def _unnamed_error(status: PrinterStatus) -> list[str]:
    """The reasons of an error report, with a fallback when no error bit is set."""
    if status.errors:
        return status.errors
    return ["printer turned off"] if status.status_type == "turned off" else ["printer reported an error"]


def _plausible_status(block: bytes) -> bool:
    """The fixed bytes of a status block: series code 0x34, bytes 5 and 6 '0' (0x30)
    or 0, byte 7 zero. A match of the 3-byte magic inside other data fails this."""
    return block[3] == 0x34 and block[5] in (0x00, 0x30) and block[6] in (0x00, 0x30) and block[7] == 0x00


class _StatusReader:
    """Reads 32-byte status blocks from the printer, keeping partial ones between
    reads and skipping bytes that are not a status."""

    def __init__(self, sock: socket.socket, abandon: threading.Event | None = None) -> None:
        self.sock = sock
        self.abandon = abandon
        self.buf = bytearray()
        self.talked = False  # any status block seen on this connection

    def _pop(self) -> PrinterStatus | None:
        while True:
            at = self.buf.find(_STATUS_MAGIC)
            if at < 0:
                del self.buf[: max(len(self.buf) - 2, 0)]  # keep a possible start of the magic
                return None
            del self.buf[:at]
            if len(self.buf) < STATUS_SIZE:
                return None
            block = bytes(self.buf[:STATUS_SIZE])
            if not _plausible_status(block):
                del self.buf[:1]  # not a real block: resync one byte on
                continue
            del self.buf[:STATUS_SIZE]
            self.talked = True
            return parse_status(block)

    def next(self, deadline: float) -> PrinterStatus | None:
        """The next status block, or ``None`` when ``deadline`` passes (or the job
        is abandoned) first. Raises ``EOFError`` when the printer closes or resets
        the connection."""
        while True:
            status = self._pop()
            if status is not None:
                return status
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (self.abandon is not None and self.abandon.is_set()):
                return None
            self.sock.settimeout(min(remaining, ABANDON_POLL_S))
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                continue
            except OSError as exc:  # reset by the printer
                raise EOFError from exc
            if not chunk:
                raise EOFError
            self.buf += chunk

    def pending_error(self) -> list[str] | None:
        """The reasons of an error status that already arrived, if any (does not wait)."""
        try:
            while (status := self.next(time.monotonic() + 0.05)) is not None:
                if status.errors or status.status_type in ("error", "turned off"):
                    return _unnamed_error(status)
        except EOFError:
            pass
        return None


def _close(sock: socket.socket, *, graceful: bool) -> None:
    """Close after a job that went through: half-close, then read until the printer
    closes (or a moment passes), as closing with unread data sends a reset that can
    discard the end of the job. After an error the close is abortive (SO_LINGER 0,
    a reset), so a printer stuck mid-job is released and nothing lingers."""
    if not graceful:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        sock.close()
        return
    try:
        sock.shutdown(socket.SHUT_WR)
        deadline = time.monotonic() + CLOSE_DRAIN_S
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            if not sock.recv(4096):
                break
    except OSError:
        pass
    finally:
        sock.close()


class _SendError(Exception):
    """The job could not be sent completely; ``sent`` bytes did go out."""

    def __init__(self, sent: int, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.sent = sent


def _send_all(sock: socket.socket, data: bytes, deadline: float, abandon: threading.Event | None) -> None:
    """``sendall`` against one deadline that reports how much went out and watches ``abandon``."""
    view = memoryview(data)
    sent = 0
    while sent < len(view):
        if abandon is not None and abandon.is_set():
            raise _SendError(sent, TimeoutError("abandoned"))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _SendError(sent, TimeoutError("send deadline passed"))
        sock.settimeout(min(remaining, ABANDON_POLL_S))
        try:
            sent += sock.send(view[sent : sent + SEND_CHUNK_BYTES])
        except TimeoutError:
            continue
        except OSError as exc:
            raise _SendError(sent, exc) from exc


def send(
    config: LabelPrinterConfig,
    job: bytes,
    *,
    pages: int = 1,
    lines: int = 0,
    connect_timeout: float = CONNECT_TIMEOUT_S,
    status_timeout: float = STATUS_TIMEOUT_S,
    send_timeout: float | None = None,
    finish_timeout: float | None = None,
    silent_grace: float = SILENT_GRACE_S,
    lock_timeout: float = LOCK_TIMEOUT_S,
    abandon: threading.Event | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> PrinterStatus | None:
    """Send ``job`` (``pages`` labels, ``lines`` raster lines); returns the printer's
    status block if it answered.

    Asks for the status first, to refuse a printer with an error or the wrong tape
    before anything is sent; a printer that does not answer is sent the job anyway.
    Then reads the printer's reports until every label is confirmed (one "completed"
    each). A printer that reports nothing at all after the job counts as done once
    the job is sent; one that talks but does not confirm all labels in time, or
    that hangs up, raises ``PrinterDidNotFinish`` (some labels may be printed).
    Sending and printing share one deadline (``send_timeout`` + ``finish_timeout``
    from the start of the send), extended only by a cooling pause the printer
    announces. One print at a time per printer: a job that cannot get the lock in
    ``lock_timeout`` raises ``PrinterBusy``. ``abandon`` (set by a caller that gave
    up waiting) stops every blocking step within about half a second. ``on_progress``
    is called with the number of labels the printer has confirmed so far. The socket
    is closed abortively on every error path and gracefully after success.
    """
    spec = tape_spec(config.tape)
    send_timeout = send_timeout_s(len(job)) if send_timeout is None else send_timeout
    finish_timeout = finish_timeout_s(pages, lines) if finish_timeout is None else finish_timeout
    lock = _printer_lock(config.host, config.port)
    if not _acquire(lock, lock_timeout, abandon):
        raise PrinterBusy("the printer is still busy with another job")
    try:
        try:
            sock = socket.create_connection((config.host, config.port), timeout=connect_timeout)
        except OSError as exc:
            raise PrinterUnreachable(f"{config.host}:{config.port}: {exc}") from exc
        done = False
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            result = _converse(
                sock,
                spec,
                job,
                pages,
                status_timeout,
                send_timeout + finish_timeout,
                silent_grace,
                abandon,
                on_progress,
            )
            done = True
            return result
        finally:
            _close(sock, graceful=done)
    finally:
        lock.release()


def _converse(
    sock: socket.socket,
    spec: TapeSpec,
    job: bytes,
    pages: int,
    status_timeout: float,
    budget: float,
    silent_grace: float,
    abandon: threading.Event | None,
    on_progress: Callable[[int], None] | None = None,
) -> PrinterStatus | None:
    reader = _StatusReader(sock, abandon)
    status: PrinterStatus | None = None
    try:
        sock.settimeout(status_timeout + 5.0)
        sock.sendall(INVALIDATE + INITIALIZE + STATUS_REQUEST)
    except OSError as exc:
        raise PrinterUnreachable(f"connection lost before the job: {exc}") from exc
    try:
        # The reply to the request; anything the printer pushed before it is checked too.
        wait_until = time.monotonic() + status_timeout
        while (block := reader.next(wait_until)) is not None:
            if block.status_type == "reply":
                _check_status(block, spec)
                status = block
                break
            if block.errors or block.status_type in ("error", "turned off"):
                raise PrinterReportedError(_unnamed_error(block))
    except EOFError:
        pass  # no status channel; the send below tells whether the printer is there

    # One deadline for sending and printing, from here.
    sent_at = time.monotonic()
    deadline = sent_at + budget
    try:
        _send_all(sock, job, deadline, abandon)
    except _SendError as exc:
        if exc.sent == 0:
            raise PrinterBusy(f"the printer took no data: {exc}") from exc
        # A printer that stopped reading usually says why.
        reasons = reader.pending_error()
        if reasons:
            raise PrinterReportedError(reasons) from exc
        raise PrinterDidNotFinish(f"the job was not fully sent: {exc}") from exc

    silent_until = time.monotonic() + silent_grace
    cooling_extensions = 0
    completed = 0
    spoke = False  # a status after the job
    while completed < pages:
        try:
            report = reader.next(deadline if spoke else min(deadline, silent_until))
        except EOFError:
            if reader.talked:
                raise PrinterDidNotFinish("the printer closed the connection before confirming every label") from None
            break
        if report is None:
            if abandon is not None and abandon.is_set():
                raise PrinterDidNotFinish(f"abandoned after {completed} of {pages} labels")
            if spoke:
                raise PrinterDidNotFinish(f"{completed} of {pages} labels confirmed before the time limit")
            logger.debug("Label printer sent no status after the job; taking it as printed")
            break
        spoke = True
        if report.errors or report.status_type in ("error", "turned off"):
            raise PrinterReportedError(_unnamed_error(report))
        if report.cooling and cooling_extensions < MAX_COOLING_EXTENSIONS:
            cooling_extensions += 1
            deadline = max(deadline, time.monotonic() + COOLING_EXTENSION_S)
        if report.status_type == "completed":
            completed += 1
            status = status or report
            if on_progress is not None:
                on_progress(completed)
    return status


# ── Driver ───────────────────────────────────────────────────────────────────

_FONT = Path(__file__).resolve().parent.parent.parent / "data" / "fonts" / "LiberationSans-Regular.ttf"
_FONT_BOLD = _FONT.with_name("LiberationSans-Bold.ttf")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """The bundled font, or Pillow's own when an install lacks it (#3612).

    A test label must never fail over a font: the fonts directory is not part
    of every build.
    """
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default(size)


def _test_image(config: LabelPrinterConfig) -> Image.Image:
    """A small landscape test label, rotated so its width is the tape's."""
    spec = tape_spec(config.tape)
    width, length = spec.printable_dots, 400
    image = Image.new("L", (length, width), 255)
    draw = ImageDraw.Draw(image)
    size = max(12, min(40, width // 5))
    title, body = _font(_FONT_BOLD, size), _font(_FONT, max(10, size * 2 // 3))
    draw.rectangle((2, 2, length - 3, width - 3), outline=0, width=3)
    draw.text((16, 10), "Bambuddy test label", font=title, fill=0)
    draw.text((16, 14 + size), f"{config.model}, {spec.tape_mm} mm tape", font=body, fill=0)
    draw.text((16, 18 + size + size * 2 // 3), date.today().isoformat(), font=body, fill=0)
    return image.transpose(Image.Transpose.ROTATE_90)


class BrotherQLDriver:
    def prepare(self, images: Iterable[Image.Image], config: LabelPrinterConfig, *, cut: bool = True) -> PreparedJob:
        compress = config.model not in MODELS_WITHOUT_COMPRESSION
        return prepare_job(images, config.tape, cut=cut, compress=compress)

    def test_job(self, config: LabelPrinterConfig) -> PreparedJob:
        return self.prepare([_test_image(config)], config)

    def deliver(
        self,
        job: PreparedJob,
        config: LabelPrinterConfig,
        abandon: threading.Event | None = None,
        on_progress: Callable[[int], None] | None = None,
        lock_timeout: float | None = None,
    ) -> PrinterStatus | None:
        return send(
            config,
            job.data,
            pages=job.pages,
            lines=job.lines,
            abandon=abandon,
            on_progress=on_progress,
            lock_timeout=LOCK_TIMEOUT_S if lock_timeout is None else lock_timeout,
        )

    def timeout_s(self, job: PreparedJob) -> float:
        return job_timeout_s(job)

    def max_timeout_s(self, job: PreparedJob) -> float:
        return max_job_timeout_s(job)
