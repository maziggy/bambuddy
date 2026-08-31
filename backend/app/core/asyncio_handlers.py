"""Event-loop concerns handled at app startup.

Two of them, both about which loop implementation Bambuddy finds itself on.
``install_proactor_reset_filter`` silences the noisy Windows Proactor
cleanup-RST that fires whenever a printer / MQTT broker / camera RSTs a socket
instead of closing it; ``warn_if_running_on_uvloop`` says so out loud when the
loop is uvloop, which Bambuddy is not launched on and does not want.
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


# Every launch path Bambuddy ships pins ``--loop asyncio``: the Dockerfile,
# install/install.sh, deploy/bambuddy.service, the Windows service and the
# SpoolBuddy installer. That flag was added for #1896 and is load-bearing --
# see the warning text below for what it holds up.
_LOOP_FLAG = "--loop asyncio"


def running_on_uvloop(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Is `loop` (or the running loop) a uvloop loop?

    Asks the loop what it is rather than whether uvloop imports: uvloop is a
    hard dependency here -- ``requirements.txt`` pins ``uvicorn[standard]``,
    which installs it on Linux -- so its mere presence says nothing. Matching
    on the module name rather than ``isinstance(loop, uvloop.Loop)`` keeps this
    from importing uvloop just to ask the question, which on a host without it
    would be an ImportError in the middle of startup.
    """
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
    return type(loop).__module__.split(".")[0] == "uvloop"


def warn_if_running_on_uvloop(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Log a loud warning when the process is running on uvloop.

    Bambuddy is developed, tested and shipped on asyncio's own loop, and two
    faults have already been traced to uvloop's differences from it:

      * #1896 -- uvloop's SSL layer can drop buffered data when a client closes
        without a TLS close_notify, so a Virtual Printer FTP upload can be
        truncated, acked ``226``, archived and forwarded to a printer as a
        corrupt ``.gcode.3mf``. There is a second guard for that one (the ZIP
        is validated before the ack), but it is a backstop, not a licence to
        run the loop that needs it.
      * #3001 -- ``uvloop.loop.Server`` rejects attribute assignment, which
        took out every RTSP camera in 1.2.5.4. Fixed, and the fix is loop
        agnostic; it is named here because it is how we learned that installs
        on uvloop exist at all.

    Nothing is blocked and no loop is swapped: a running server that answers
    requests is worth more than a purist one that refuses to boot, and by the
    time this runs uvicorn has long since chosen. The point is that the two
    populations this reaches -- the Proxmox VE Helper-Scripts LXC, which writes
    its own unit with no loop pinned, and native installs predating the #1896
    fix, which never gained the flag because ``update.sh`` does not rewrite
    unit files -- have no other way to find out. The camera outage was visible;
    a truncated upload is not.

    Returns True when the warning was emitted.
    """
    if not running_on_uvloop(loop):
        return False
    logger.warning(
        "Running on uvloop, which Bambuddy is not tested or shipped on. Virtual Printer FTP "
        "uploads can be silently truncated on this loop (#1896). Add '%s' to the uvicorn "
        "command in your service file and restart. Every installer Bambuddy ships already "
        "does this; a unit written by a third-party script, or one created before 2026-07-05, "
        "will not, and updating does not add it.",
        _LOOP_FLAG,
    )
    return True
