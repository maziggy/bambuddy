"""Logging filters and redaction helpers for the Bambuddy log pipeline.

Holds two filters: ``WriteRequestsOnlyFilter`` keeps the file-side
uvicorn access log focused on state-changing HTTP methods, and
``CancelledPoolNoiseFilter`` drops SQLAlchemy connection-pool log noise
caused by Starlette's ``BaseHTTPMiddleware`` cancellation propagation
(see the filter's docstring for details). Both live here so tests can
import them without pulling in ``backend.app.main``'s startup graph.

Also holds :data:`URL_CREDENTIALS_PATTERN` and
:func:`redact_url_credentials`, the single place where the shape of a
credentialed URL is defined for the whole backend.
"""

from __future__ import annotations

import asyncio
import logging
import re

# ``scheme://user:secret@host`` — the only URL shape that carries a secret.
# Both userinfo parts exclude ``/`` so the match can never run past the
# authority into the path, and exclude whitespace so a wrapped log line can't
# glue two URLs together. ``secret`` is otherwise unrestricted and greedy so
# it reaches the *last* ``@`` before the path, which is where RFC 3986 ends
# the userinfo — that keeps an unescaped ``@`` inside a password (legal in an
# external camera URL) from leaving its tail in the log. Named groups let
# callers choose how much to mask: the log pipeline keeps the username, the
# support-bundle sanitizer drops it (see ``log_reader.sanitize_log_content``).
#
# The scheme's repetition is bounded deliberately. As an unbounded ``*`` the
# match was quadratic in the length of the subject (CodeQL py/polynomial-redos):
# on a long run of scheme-legal characters the engine restarts at every offset
# and consumes to the end each time before failing to find ``://``. Measured at
# 557ms for a 32KB line, quadrupling per doubling. ffmpeg echoes the operator's
# camera URL back in its stderr, and that whole string reaches this pattern
# before any truncation, so the subject length is attacker-influenced. A cap
# makes the work per offset constant. 63 is far above any real scheme (the
# longest registered one is under 20 characters), and a longer pseudo-scheme
# still gets its secret masked — the match simply starts from a later offset.
URL_CREDENTIALS_PATTERN = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]{0,63}://)(?P<user>[^/:@\s]+):(?P<secret>[^/\s]+)@"
)


def redact_url_credentials(text: str | None) -> str | None:
    """Mask the password in every ``scheme://user:secret@host`` URL in *text*.

    Subprocesses echo their input URL back at us — ffmpeg prints the RTSP
    input in its ``Input #0`` line, so logging its stderr verbatim publishes
    the printer access code (or an external camera's password) into
    ``bambuddy.log``, which users routinely attach to public issues.

    The username, host, port and path survive so the line stays useful for
    diagnosis; only the secret is replaced. Returns *text* unchanged when
    there is nothing to mask, including ``None``/``""``.
    """
    if not text or "://" not in text or "@" not in text:
        return text
    return URL_CREDENTIALS_PATTERN.sub(r"\g<scheme>\g<user>:[REDACTED]@", text)


class WriteRequestsOnlyFilter(logging.Filter):
    """Keep uvicorn access log records for state-changing HTTP methods only.

    Uvicorn's access logger emits one record per HTTP request, formatted as

        ``<client_addr> - "<METHOD> <path> HTTP/<ver>" <status>``

    On a typical Bambuddy install the bulk of that traffic is GETs — the
    frontend status-polling loop, the camera stream, snapshots, websocket
    upgrades. None of those can change server state on their own, so for
    incident triage ("who hit ``/print/stop`` at 09:23?") they're noise that
    just rotates the log file faster.

    This filter accepts only POST / PUT / PATCH / DELETE — the verbs that
    actually mutate state — and drops everything else. Match anchors on the
    surrounding ``" `` and trailing space so an unrelated literal substring
    in a URL (e.g. ``GET /api/posts/POST``) cannot false-match.

    Attach to ``logging.getLogger("uvicorn.access")`` (and only there — the
    pattern is uvicorn's specific format string and would silently drop
    everything if applied to a generic logger).
    """

    _WRITE_VERB_TOKENS: tuple[str, ...] = (
        ' "POST ',
        ' "PUT ',
        ' "PATCH ',
        ' "DELETE ',
    )

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API name
        message = record.getMessage()
        return any(token in message for token in self._WRITE_VERB_TOKENS)


class CancelledPoolNoiseFilter(logging.Filter):
    """Drop SQLAlchemy connection-pool log records driven by request cancellation.

    Starlette's ``BaseHTTPMiddleware`` (used under the hood by FastAPI's
    ``@app.middleware("http")`` decorator) cancels the inner task scope when a
    client disconnects mid-request. The cancellation propagates into
    SQLAlchemy's connection-pool cleanup and surfaces as two distinct ERROR
    records — both expected on disconnect, neither actionable for the user:

    1. ``Exception terminating connection ... CancelledError`` — fires every
       time ``do_terminate`` is interrupted by the same cancel scope that's
       unwinding the request. The ``CancelledError`` traceback always
       attributes the cancel to ``BaseHTTPMiddleware.call_next``.

    2. ``The garbage collector is trying to clean up non-checked-in
       connection`` — fires later when the GC reclaims the session that
       couldn't return its connection to the pool because of (1). It's
       symptomatic of the cancellation, not a separate bug.

    These pile up under heavy upload load (long multipart uploads where the
    client times out before the server's response). Real connection-pool
    issues — pool exhaustion, broken connections from network hiccups, etc.
    — surface through DIFFERENT messages and a non-cancellation
    ``exc_info`` chain, so they keep flowing through this filter unchanged.

    Attach to ``logging.getLogger("sqlalchemy.pool")`` (and only there).
    """

    _GC_CLEANUP_PREFIX = "The garbage collector is trying to clean up non-checked-in connection"
    _TERMINATE_PREFIX = "Exception terminating connection"

    @staticmethod
    def _has_cancelled_in_chain(exc: BaseException | None) -> bool:
        """True if `exc` is `CancelledError` or has one in its cause chain."""
        seen: set[int] = set()
        cur: BaseException | None = exc
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, asyncio.CancelledError):
                return True
            cur = cur.__cause__ or cur.__context__
        return False

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API name
        message = record.getMessage()
        # GC-cleanup records have no exc_info — match by prefix only. Always
        # symptomatic of the cancellation cascade, never independently useful.
        if message.startswith(self._GC_CLEANUP_PREFIX):
            return False
        # Terminate-connection records carry a traceback; only drop those
        # that are cancellation-driven. A real terminate failure (broken
        # connection, network hiccup) keeps a non-CancelledError exc_info
        # chain and surfaces normally.
        if message.startswith(self._TERMINATE_PREFIX) and record.exc_info:
            exc = record.exc_info[1]
            if self._has_cancelled_in_chain(exc):
                return False
        return True
