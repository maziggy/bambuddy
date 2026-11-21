"""Driver interface of the label printers (#3612)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Iterable

    import PIL.Image

DEFAULT_PORT = 9100


class LabelPrinterError(Exception):
    """A label could not be printed."""


class PrinterUnreachable(LabelPrinterError):
    """The printer did not accept a connection."""


class WrongMedia(LabelPrinterError):
    """The loaded tape is not the one in the settings."""

    def __init__(self, expected: str, loaded: str) -> None:
        super().__init__(f"The printer has {loaded} tape loaded, the settings say {expected}")
        self.expected = expected
        self.loaded = loaded


class PrinterBusy(LabelPrinterError):
    """Nothing reached the printer: another job still holds it, or it takes no data."""


class PrinterDidNotFinish(LabelPrinterError):
    """The job was (partly) sent, but the printer did not confirm all labels.

    Some labels may have been printed, so the caller must not retry blindly.
    """


class PrinterReportedError(LabelPrinterError):
    """The printer's own status names a problem (cover open, no tape, ...)."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(", ".join(reasons))
        self.reasons = reasons


DID_NOT_FINISH_MESSAGE = "The label printer did not confirm all labels; check the printer before printing again"
BUSY_MESSAGE = "The label printer is busy or not taking data; try again in a moment"


def describe_error(exc: BaseException, config: LabelPrinterConfig, *, reveal_address: bool = False) -> tuple[int, str]:
    """(HTTP status, user-facing text) for a failed print. The printer's address is
    only named when ``reveal_address`` (the settings test, for those who may change
    it): the print routes need only ``inventory:read`` and must not map the LAN."""
    if isinstance(exc, PrinterUnreachable):
        where = f" at {config.host}:{config.port}" if reveal_address else ""
        return 502, f"Label printer not reachable{where}"
    if isinstance(exc, WrongMedia):
        return 409, f"The printer has {exc.loaded} tape loaded, the settings say {exc.expected}"
    if isinstance(exc, PrinterReportedError):
        return 502, f"Label printer reports: {', '.join(exc.reasons)}"
    if isinstance(exc, PrinterBusy):
        return 503, BUSY_MESSAGE
    if isinstance(exc, PrinterDidNotFinish):
        return 504, DID_NOT_FINISH_MESSAGE
    return 502, "Label printing failed"


@dataclass
class PrinterStatus:
    media_width_mm: int
    media_type: str
    errors: list[str]
    status_type: str = "reply"
    phase: str = "waiting"
    cooling: bool = False

    def to_dict(self) -> dict:
        return {
            "media_width_mm": self.media_width_mm,
            "media_type": self.media_type,
            "errors": list(self.errors),
            "status_type": self.status_type,
            "phase": self.phase,
        }


@dataclass
class PreparedJob:
    """A rasterised print job, ready to send: the protocol bytes, the number of
    labels (pages) and of raster lines (the printing time scales with these)."""

    data: bytes
    pages: int
    lines: int


@dataclass
class LabelPrinterConfig:
    type: str
    host: str
    port: int = DEFAULT_PORT
    model: str = "QL-820NWB"
    tape: str = "50"


class LabelPrinterDriver(Protocol):
    """Printing is two steps so the caller can render and rasterise outside the
    network time limit: ``prepare`` is CPU work, ``deliver`` is the (blocking,
    serialised per printer) conversation with the printer. Both are called
    through ``asyncio.to_thread``."""

    def prepare(
        self, images: Iterable[PIL.Image.Image], config: LabelPrinterConfig, *, cut: bool = True
    ) -> PreparedJob:
        """Rasterise one label per image, taking the images lazily (one at a time)."""
        ...

    def test_job(self, config: LabelPrinterConfig) -> PreparedJob: ...

    def deliver(
        self,
        job: PreparedJob,
        config: LabelPrinterConfig,
        abandon: threading.Event | None = None,
        on_progress: Callable[[int], None] | None = None,
        lock_timeout: float | None = None,
    ) -> PrinterStatus | None:
        """Send the job and wait for the printer to confirm it. Setting ``abandon``
        makes it give up promptly (the caller stopped waiting). ``on_progress`` is
        called (from the worker thread) with the labels confirmed so far.
        ``lock_timeout`` caps the wait for the printer's lock (0 = fail at once
        with ``PrinterBusy`` when another job holds it; ``None`` = driver default)."""
        ...

    def timeout_s(self, job: PreparedJob) -> float:
        """The time ``deliver`` normally needs at most, without any cooling pause."""
        ...

    def max_timeout_s(self, job: PreparedJob) -> float:
        """The hard upper bound for ``deliver``: ``timeout_s`` plus every allowed
        cooling pause and the wait for the printer's lock."""
        ...


def parse_printer_address(value: str) -> tuple[str, int]:
    """Split ``host`` or ``host:port`` (``[v6]:port`` for IPv6) and check the host.

    Raises ``ValueError`` naming the field, so the settings validator and the
    test endpoint report the same message.
    """
    from urllib.parse import urlsplit

    from backend.app.api.routes._url_safety import assert_safe_lan_service_url

    label = "Label printer address"
    candidate = value.strip()
    if not candidate or any(ch in candidate for ch in "/?#@ \t\r\n"):
        raise ValueError(f"{label} must be a host name or IP address, optionally with :port")
    # A bare IPv6 address has several colons and no port.
    if candidate.count(":") >= 2 and not candidate.startswith("["):
        candidate = f"[{candidate}]"
    try:
        assert_safe_lan_service_url(f"http://{candidate}", label=label)
    except ValueError as exc:
        # urlparse's own errors ("Invalid IPv6 URL") do not name the field.
        if label in str(exc):
            raise
        raise ValueError(f"{label} is not a valid host name or IP address") from None
    try:
        parts = urlsplit(f"http://{candidate}")
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError(f"{label} has an invalid port") from None
    if not host:
        raise ValueError(f"{label} must include a hostname")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label} has an invalid port")
    return host, port or DEFAULT_PORT


def get_driver(printer_type: str) -> LabelPrinterDriver:
    """The driver for a validated printer type (settings only accept ``PRINTER_TYPES``)."""
    from backend.app.services.label_printers.brother_ql import BrotherQLDriver

    drivers: dict[str, type[LabelPrinterDriver]] = {"brother_ql": BrotherQLDriver}
    return drivers[printer_type]()


# The printer types the settings accept.
PRINTER_TYPES: tuple[str, ...] = ("brother_ql",)
