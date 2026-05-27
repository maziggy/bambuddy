"""Logging filters for the Bambuddy log pipeline.

Holds two filters: ``WriteRequestsOnlyFilter`` keeps the file-side
uvicorn access log focused on state-changing HTTP methods, and
``CancelledPoolNoiseFilter`` drops SQLAlchemy connection-pool log noise
caused by Starlette's ``BaseHTTPMiddleware`` cancellation propagation
(see the filter's docstring for details). Both live here so tests can
import them without pulling in ``backend.app.main``'s startup graph.
"""

from __future__ import annotations

import asyncio
import logging


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
