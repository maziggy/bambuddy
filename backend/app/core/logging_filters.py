"""Logging filters and redaction helpers for the Bambuddy log pipeline.

Holds two filters: ``WriteRequestsOnlyFilter`` keeps the file-side
uvicorn access log focused on state-changing HTTP methods, and
``CancelledPoolNoiseFilter`` drops SQLAlchemy connection-pool log noise
caused by Starlette's ``BaseHTTPMiddleware`` cancellation propagation
(see the filter's docstring for details). Both live here so tests can
import them without pulling in ``backend.app.main``'s startup graph.

Also holds :data:`URL_CREDENTIALS_PATTERN` and
:func:`redact_url_credentials`, the single place where the shape of a
credentialed URL is defined for the whole backend, and
:data:`QUERY_TOKEN_PATTERN` / :class:`QueryTokenRedactFilter` for tokens
carried in a query string.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlsplit

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
URL_CREDENTIALS_PATTERN = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]{0,63}://)(?P<login>[^/\s]+)@")


def _mask_login(match: re.Match[str]) -> str:
    # The login runs to the last @ before the path. It may hold an unencoded ?
    # or #, which cannot be told from a query: masked rather than risk it. Its
    # user part (up to the first ':') survives; without a ':' it may be a
    # token, so it all goes.
    login = match.group("login")
    shown = f"{login.split(':', 1)[0]}:[REDACTED]" if ":" in login else "[REDACTED]"
    return f"{match.group('scheme')}{shown}@"


def redact_url_credentials(text: str | None) -> str | None:
    """Mask the login secret in every ``scheme://login@host`` URL in *text*.

    The login runs to the last ``@`` before the path (``?`` and ``#``
    included, when in doubt it is masked), so an empty user
    (``:secret@``), an unencoded ``@`` in the user and a bare token
    (``token@``, masked whole) are covered as well as ``user:secret@``.
    Also used for camera URLs shown to those who may not edit the printer.

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
    return URL_CREDENTIALS_PATTERN.sub(_mask_login, text)


# ``?token=<value>`` and its kin. The SPA passes its short-lived WebSocket and
# media tokens in the query string (a browser cannot set headers on a
# WebSocket upgrade, an <img> or a <video>), and uvicorn logs every request
# path with its query: the access line for each GET, and the "WebSocket ...
# [accepted]" line for each upgrade. Those reach the console, so `docker logs`,
# and from there anything that collects container logs -- the appliance's
# support bundle among them. The value ends at the next ``&``, whitespace or
# quote, which is where uvicorn's format closes the path.
QUERY_TOKEN_PATTERN = re.compile(r"(?P<key>[?&](?:token|access_token|api_key|url)=)[^&\s\"']+")


def redact_query_tokens(text: str | None) -> str | None:
    """Mask the value of every token-bearing query parameter in *text*."""
    if not text or "=" not in text:
        return text
    return QUERY_TOKEN_PATTERN.sub(r"\g<key>[REDACTED]", text)


# Loggers whose lines name a device by its whole URL, where the path or query
# can be the device's credential in a shape no pattern recognises (a webhook id,
# an ``auth_key``). Read back, their URLs are reduced to scheme and host.
HOST_ONLY_LOGGERS = ("backend.app.services.rest_smart_plug", "httpx")

# Runs to whitespace only: a quote or angle bracket may sit inside a stored
# URL, and ending there would leave the rest of it in the line.
_URL_IN_TEXT = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]{0,63}://\S+")


def url_host_only(url: str | None) -> str:
    """A URL reduced to scheme and host (with port) for a log line.

    The login, path, query and fragment go. An ``@`` after the part the parser
    took for the host means a login it could not find (an unencoded ``?``,
    ``/`` or ``#`` in the password): that part may then hold the login, so it
    goes as well.
    """
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return "<invalid URL>"
    if not parts.scheme or not parts.netloc:
        return "<invalid URL>"
    if "@" in (url or "")[len(parts.scheme) + 3 + len(parts.netloc) :]:
        return f"{parts.scheme}://<hidden>"
    return f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"


# Masked to the end of the line, in lines from those loggers: the REST plug
# service's messages carry raw input (a refused URL, unparsed headers) or an
# error's own text, which may quote a URL or a header's value. Covers every
# message it has logged.
_MASKED_TAILS = re.compile(r"(invalid URL: |headers JSON: |returned error: |REST smart plug at \S+?: ).*")


# An HTTP client refusing a header quotes the header's value, commonly a token,
# whichever service sent it: masked to the end of the line.
_REFUSED_HEADER = re.compile(r"(Illegal header (?:value|name) ).*")
_UNMASKED_REFUSED_HEADER = re.compile(r"Illegal header (?:value|name) (?!\[REDACTED\])")


def redact_refused_headers(text: str | None) -> str | None:
    """Mask the header an HTTP client quotes when it refuses one."""
    if not text or "Illegal header" not in text:
        return text
    return _REFUSED_HEADER.sub(r"\1[REDACTED]", text)


def _redact_refused_header_arg(value: object) -> object:
    """*value* as masked text when its text quotes a refused header (an
    exception, a string), else *value* itself."""
    text = value if isinstance(value, str) else str(value)
    return redact_refused_headers(text) if "Illegal header" in text else value


class RefusedHeaderRedactFilter(logging.Filter):
    """Mask a refused header's value in a record before a handler writes it.

    The value reaches a record as an argument (``"...: %s", e``), in the
    message itself, or in the traceback of ``logger.exception``, so the
    rendered message and traceback are checked, not the format string. A
    record with nothing to mask is left untouched.

    Attach to the handlers that write lines (a handler filter sees records
    from every logger that propagates to it) and to uvicorn's loggers, which
    do not propagate to root.
    """

    _formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API name
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a bad format string is the handler's error to report, not the caller's
            message = ""
        if "Illegal header" in message:
            # Masked in the arguments, keeping their shape: uvicorn's access
            # formatter unpacks them.
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_refused_header_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_refused_header_arg(v) for k, v in record.args.items()}
            try:
                masked = record.getMessage()
            except Exception:  # noqa: BLE001 — the masked format string no longer fits its arguments
                masked = message
            if _UNMASKED_REFUSED_HEADER.search(masked):
                # The quote is (also) in the format string: the rendered
                # line, masked, replaces both.
                record.msg = redact_refused_headers(message)
                record.args = None
        if record.exc_info and not record.exc_text:
            # Formatters reuse exc_text once set, so the masked text is what
            # every handler then writes.
            record.exc_text = self._formatter.formatException(record.exc_info)
        if record.exc_text and "Illegal header" in record.exc_text:
            record.exc_text = redact_refused_headers(record.exc_text)
        return True


def redact_device_urls(text: str | None) -> str | None:
    """Every URL in *text* reduced to :func:`url_host_only`, and the tails in
    ``_MASKED_TAILS`` masked."""
    if not text:
        return text
    text = _MASKED_TAILS.sub(r"\1[REDACTED]", text)
    if "://" not in text:
        return text
    return _URL_IN_TEXT.sub(lambda match: url_host_only(match.group(0)), text)


class QueryTokenRedactFilter(logging.Filter):
    """Mask query-string tokens in uvicorn's request and WebSocket log lines.

    Rewrites the record rather than dropping it: the line is still the record
    of a request, only its credential goes. Uvicorn passes the path as a
    ``%s`` argument (``'%s - "%s %s HTTP/%s" %d'`` for HTTP,
    ``'%s - "WebSocket %s" [accepted]'`` for an upgrade), so the arguments are
    rewritten as well as the message.

    Attach to ``logging.getLogger("uvicorn.access")`` and
    ``logging.getLogger("uvicorn.error")`` -- the latter carries the WebSocket
    lines. A logger-level filter runs whichever handler then writes the record,
    so the console is covered as well as ``bambuddy.log``.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API name
        if isinstance(record.msg, str):
            record.msg = redact_query_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_query_tokens(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact_query_tokens(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


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


class ShutdownCancelledRequestFilter(logging.Filter):
    """One INFO line for a request uvicorn cancels at the end of a shutdown.

    The service caps the graceful shutdown (``--timeout-graceful-shutdown``)
    because an MJPEG camera stream never ends by itself. When the cap is
    reached uvicorn logs "Cancel N running task(s)" and then, for each
    cancelled request, "Exception in ASGI application" with the full
    ``CancelledError`` traceback, both at ERROR. That happens on every restart
    with a camera view open and is not an error. Only that cancellation is
    rewritten, recognised by uvicorn's own cancel message; any other failure,
    and any other cancellation, keeps its level and traceback.

    Attach to ``logging.getLogger("uvicorn.error")`` before any filter that
    renders the traceback.
    """

    _CANCEL_MESSAGE = "timeout graceful shutdown exceeded"

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API name
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, asyncio.CancelledError) and self._CANCEL_MESSAGE in str(exc):
            record.msg = "Request closed at shutdown, still open after the grace period (a camera stream, for example)"
            record.args = None
            record.exc_info = None
            record.exc_text = None
            self._as_info(record)
        elif exc is None and isinstance(record.msg, str) and record.msg.endswith(self._CANCEL_MESSAGE):
            self._as_info(record)
        return True

    @staticmethod
    def _as_info(record: logging.LogRecord) -> None:
        record.levelno = logging.INFO
        record.levelname = logging.getLevelName(logging.INFO)


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
