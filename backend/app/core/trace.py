"""Per-request trace ID plumbing.

Each HTTP request gets a short hex ID set in a ``ContextVar``; downstream
log records (application *and* uvicorn access) read the same context and
include the ID in their output. The result is that one ``grep <trace_id>``
on ``bambuddy.log`` returns the access line + every line emitted on the
server side while that request was being handled — closing the loop
opened by piping uvicorn access into the file: the access line tells you
*who* called the endpoint, the trace ID tells you *what else happened*
on the server because of it.

Why a ContextVar instead of e.g. ``request.state``:

* asyncio copies the current context into every ``asyncio.create_task``,
  so background work spawned from within a request inherits the same
  trace ID without having to be passed it explicitly. ``request.state``
  doesn't survive that hop.
* The logging filter has no access to the FastAPI request object — it
  runs synchronously inside the stdlib logging machinery — and the
  ContextVar is the only mechanism that bridges async request scope to
  sync log emission.

Why no fancy structured-logging schema: this is a small project. The
existing log format is a single line per record; we add a single
bracketed token for the trace ID and stop there. If structured logging
is wanted later, it can layer on top — the ContextVar carries an opaque
string regardless of what consumes it downstream.
"""

from __future__ import annotations

import logging
import re
import secrets
from contextvars import ContextVar

# Default ``"-"`` (instead of None or empty string) so the format string
# always produces a stable visual width; a bare empty bracket pair would
# read as "no trace ID at all" which is hard to grep for. ``-`` reads as
# "no value in this column" the way it does in HTTP access logs already.
TRACE_ID_PLACEHOLDER = "-"

trace_id_var: ContextVar[str] = ContextVar("trace_id", default=TRACE_ID_PLACEHOLDER)

# Length of a freshly minted trace ID in hex chars. 8 chars = 32 bits of
# entropy = ~4 billion possibilities; collisions are astronomically
# unlikely within a single rotation window of bambuddy.log and grep stays
# easy at this length. Increase later if it proves too short for a busy
# install — the filter and format don't care about width.
_GENERATED_LENGTH = 8

# Bound on how long an *inbound* trace ID can be when echoed from the
# X-Trace-Id request header. Without a cap a malicious / buggy client
# could push 1 MB of garbage into every log line for a request. 64 chars
# comfortably accommodates UUIDs (32 hex), Datadog-style 64-bit IDs,
# OpenTelemetry's 32-hex spans — anything longer is almost certainly
# wrong and we'd rather mint our own than honour it.
_MAX_INBOUND_LENGTH = 64

# Whitelist of characters allowed in an inbound trace ID. Restricted to
# the alphanumerics + a small set of separators that real-world
# correlation IDs use, so newlines / quotes / control chars cannot be
# smuggled into log lines via the X-Trace-Id header. A request with an
# unacceptable header just gets a freshly minted server-side ID — we
# never reject the request for it.
_VALID_INBOUND = re.compile(r"^[A-Za-z0-9_\-]+$")


def get_trace_id() -> str:
    """Return the current trace ID, or the placeholder if none is set."""
    return trace_id_var.get()


def generate_trace_id() -> str:
    """Mint a fresh server-side trace ID."""
    return secrets.token_hex(_GENERATED_LENGTH // 2)


def normalise_inbound_trace_id(raw: str | None) -> str | None:
    """Validate and return a caller-supplied trace ID, or ``None`` to mint fresh.

    Accepts only short alphanumeric + ``_-`` strings so a hostile or buggy
    client can't smuggle log-injection payloads through the X-Trace-Id
    header. Returns ``None`` for any input that fails the gate, signalling
    to the middleware that it should generate one instead.
    """
    if raw is None:
        return None
    if not raw or len(raw) > _MAX_INBOUND_LENGTH:
        return None
    if not _VALID_INBOUND.match(raw):
        return None
    return raw


class TraceIDFilter(logging.Filter):
    """Inject the current ``trace_id_var`` value into every LogRecord.

    Attach to the file handler (or any handler whose format string
    references ``%(trace_id)s``) so that every line written through that
    handler carries the request scope it was generated under. The filter
    always returns ``True`` — it never drops records, only annotates
    them.

    Records emitted outside any HTTP request (startup, MQTT callbacks,
    scheduled tasks not chained from a request) get the placeholder
    string, so the format column stays aligned and absent values are
    obviously visible as ``[-]`` rather than blanks.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API name
        # Use direct attribute set (not setdefault-style) so the value is
        # always taken from the *current* context — a record formatted on
        # a different task than where it was created (rare but possible
        # via QueueHandler or async-throttled handlers) still picks up
        # the right ID.
        record.trace_id = trace_id_var.get()
        return True
