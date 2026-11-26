"""Shared by the label print endpoints and the settings test endpoint (#3612)."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.schemas.settings import validate_label_printer_value
from backend.app.services.label_printers.base import (
    LabelPrinterConfig,
    LabelPrinterDriver,
    LabelPrinterError,
    PreparedJob,
    PrinterDidNotFinish,
    PrinterStatus,
    describe_error,
    parse_printer_address,
)

logger = logging.getLogger(__name__)

_KEYS = ("label_printer_type", "label_printer_host", "label_printer_model", "label_printer_tape")
_DEFAULT_MODEL = "QL-820NWB"
_DEFAULT_TAPE = "50"


async def load_label_printer_config(
    db: AsyncSession, overrides: dict[str, str | None] | None = None
) -> LabelPrinterConfig | None:
    """The configured label printer, or ``None`` when none is set up. ``overrides``
    (type, host, model, tape; ``None`` = keep) replace saved values, for testing
    unsaved ones."""
    rows = (await db.execute(select(Settings.key, Settings.value).where(Settings.key.in_(_KEYS)))).all()
    saved: dict[str, str] = {row[0]: row[1] for row in rows}  # noqa: C416
    values = {key: (saved.get(key) or "").strip() for key in _KEYS}
    for key, value in (overrides or {}).items():
        if value is not None:
            values[f"label_printer_{key}"] = value.strip()
    if not values["label_printer_type"] or not values["label_printer_host"]:
        return None
    # Saved values were validated on save, but a restored backup or a hand-edited
    # row can hold anything: a clear 400, not a 500 from the driver.
    for key in _KEYS:
        try:
            validate_label_printer_value(key, values[key])
        except ValueError as exc:
            raise HTTPException(400, f"The saved label printer settings are not valid: {exc}") from exc
    try:
        host, port = parse_printer_address(values["label_printer_host"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return LabelPrinterConfig(
        type=values["label_printer_type"],
        host=host,
        port=port,
        model=values["label_printer_model"] or _DEFAULT_MODEL,
        tape=values["label_printer_tape"] or _DEFAULT_TAPE,
    )


def label_printer_http_error(
    exc: LabelPrinterError, config: LabelPrinterConfig, *, reveal_address: bool = False
) -> HTTPException:
    """The HTTP error for a failed print (see ``describe_error``)."""
    status, message = describe_error(exc, config, reveal_address=reveal_address)
    return HTTPException(status, message)


async def deliver_job(
    driver: LabelPrinterDriver,
    job: PreparedJob,
    config: LabelPrinterConfig,
    *,
    bound: float | None = None,
    on_progress: Callable[[int], None] | None = None,
    lock_timeout: float | None = None,
) -> PrinterStatus | None:
    """Run the driver's blocking ``deliver`` in a thread and wait at most ``bound``
    seconds (default: the driver's hard bound). When the wait ends, by the time
    limit or by cancellation, the thread is told to abandon the job, so it does
    not go on holding the printer. Raises ``LabelPrinterError``."""
    abandon = threading.Event()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(driver.deliver, job, config, abandon, on_progress, lock_timeout),
            driver.max_timeout_s(job) if bound is None else bound,
        )
    except TimeoutError:
        abandon.set()
        logger.warning("Label printing on %s:%s timed out", config.host, config.port)
        raise PrinterDidNotFinish("time limit passed") from None
    except BaseException:
        abandon.set()
        raise


async def deliver_job_http(
    driver: LabelPrinterDriver,
    job: PreparedJob,
    config: LabelPrinterConfig,
    *,
    bound: float | None = None,
    reveal_address: bool = False,
    lock_timeout: float | None = None,
) -> PrinterStatus | None:
    """``deliver_job`` for a request that waits for the printer: errors become HTTP errors."""
    try:
        return await deliver_job(driver, job, config, bound=bound, lock_timeout=lock_timeout)
    except LabelPrinterError as exc:
        logger.warning("Label printing on %s:%s failed: %s", config.host, config.port, exc)
        raise label_printer_http_error(exc, config, reveal_address=reveal_address) from exc
