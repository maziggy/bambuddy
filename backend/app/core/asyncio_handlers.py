"""Asyncio event-loop exception handlers used at app startup.

Currently houses a single Windows-specific filter for the noisy
``_ProactorBasePipeTransport._call_connection_lost`` ``WinError 10054``
that fires every time a printer / MQTT broker / camera RSTs a TCP socket
instead of closing it cleanly. See ``install_proactor_reset_filter`` for
the why and the failure mode it suppresses.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _is_proactor_connection_reset(context: dict[str, Any]) -> bool:
    """True if `context` describes the Windows Proactor cleanup-RST noise.

    asyncio's default exception handler is invoked in two distinct cases
    we care about — generic uncaught task exceptions, and the specific
    `_call_connection_lost` cleanup path — and we only want to suppress
    the latter. Match on three signals together so a real
    `ConnectionResetError` raised inside an application task still
    surfaces normally:

      1. The exception is `ConnectionResetError` (or a subclass).
      2. asyncio's own message string mentions `_call_connection_lost`
         (the Proactor-cleanup callback is the only place Python emits
         this exact phrase).
      3. We're actually on Windows, where the Proactor is in use.
    """
    if sys.platform != "win32":
        return False
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    message = context.get("message", "")
    return "_call_connection_lost" in message


def _proactor_reset_filter(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """Custom event-loop exception handler.

    Handles the Proactor-cleanup `ConnectionResetError` by logging it at
    DEBUG instead of ERROR, and delegates everything else to asyncio's
    default handler so unrelated bugs are still visible.
    """
    if _is_proactor_connection_reset(context):
        logger.debug(
            "asyncio Proactor: peer reset socket during cleanup (WinError 10054); "
            "ignored — application-layer reconnect handles the disconnect"
        )
        return
    loop.default_exception_handler(context)


def install_proactor_reset_filter(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Install the filter on `loop` (or the running loop if omitted).

    Returns True when the filter was installed (Windows only), False on
    every other platform — so callers can branch on the return value if
    they want to log the install / skip.
    """
    if sys.platform != "win32":
        return False
    if loop is None:
        loop = asyncio.get_running_loop()
    loop.set_exception_handler(_proactor_reset_filter)
    return True
