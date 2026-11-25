"""A refused header's value must not reach the live logs.

An HTTP client that refuses a header quotes its value in the error, and that
value is commonly a token (Home Assistant's ``Authorization: Bearer ...`` with
a stray space or newline). Services log such errors with ``%s`` or with their
traceback, so the text went to the console, ``docker logs`` and bambuddy.log;
only the support download masked it. ``RefusedHeaderRedactFilter`` masks it
before any handler writes the line.
"""

from __future__ import annotations

import io
import logging
import socket

import httpx
import pytest

from backend.app.core.logging_filters import RefusedHeaderRedactFilter

# Made up. Never paste a value from a real log here.
SECRET = "hdr-live-value-1"


@pytest.fixture
def logged():
    """A logger writing through a handler that carries the filter, as main.py's do."""
    logger = logging.getLogger("test.refused_header_redaction")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RefusedHeaderRedactFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield logger, stream
    finally:
        logger.removeHandler(handler)


def _refused_header_error() -> Exception:
    """The error httpx really raises for a header value with a trailing space."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        url = f"http://127.0.0.1:{server.getsockname()[1]}/api/states"
        try:
            with httpx.Client(timeout=2) as client:
                client.get(url, headers={"Authorization": f"Bearer {SECRET} "})
        except Exception as e:  # noqa: BLE001 -- whatever httpx raises is the point
            return e
    raise AssertionError("httpx accepted the header")


def test_the_real_httpx_error_quotes_the_value():
    """Guards the premise: if httpx stops quoting the value, this filter is moot."""
    assert SECRET in str(_refused_header_error())


def test_an_error_logged_as_an_argument_is_masked(logged):
    logger, stream = logged

    logger.warning("Failed to get HA entity state for %s: %s", "switch.x", _refused_header_error())

    text = stream.getvalue()
    assert SECRET not in text
    assert "Failed to get HA entity state for switch.x: Illegal header value [REDACTED]" in text


def test_an_error_logged_with_its_traceback_is_masked(logged):
    logger, stream = logged

    try:
        raise _refused_header_error()
    except Exception:
        logger.exception("Failed to list HA entities")

    text = stream.getvalue()
    assert SECRET not in text
    assert "Failed to list HA entities" in text
    assert "Traceback" in text
    assert "Illegal header value [REDACTED]" in text


def test_a_refused_header_name_is_masked(logged):
    logger, stream = logged

    logger.error("Send failed: %s", f"Illegal header name b'X-{SECRET}'")

    assert SECRET not in stream.getvalue()
    assert "Send failed: Illegal header name [REDACTED]" in stream.getvalue()


def test_other_records_are_left_as_they_are():
    record = logging.LogRecord("x", logging.INFO, "", 0, "%d items in %s", (3, "queue"), None)

    assert RefusedHeaderRedactFilter().filter(record) is True
    assert record.msg == "%d items in %s" and record.args == (3, "queue")


def test_main_attaches_the_filter_where_lines_are_written():
    """Root's handlers (console, bambuddy.log) and uvicorn's loggers, which do
    not hand records to root."""
    import backend.app.main as main

    handlers = [main.console_handler]
    if getattr(main, "file_handler", None) is not None:
        handlers.append(main.file_handler)
    for handler in handlers:
        assert any(isinstance(f, RefusedHeaderRedactFilter) for f in handler.filters), handler
    for name in ("uvicorn.access", "uvicorn.error"):
        assert any(isinstance(f, RefusedHeaderRedactFilter) for f in logging.getLogger(name).filters), name


def test_a_record_whose_arguments_do_not_fit_does_not_raise_from_the_filter():
    """A bad format string is reported by the handler as before; the filter
    must not turn it into an exception at the caller's log call."""
    record = logging.LogRecord("x", logging.INFO, "", 0, "%d items", ("three",), None)

    assert RefusedHeaderRedactFilter().filter(record) is True


def test_uvicorns_access_line_keeps_its_argument_tuple():
    """uvicorn's console AccessFormatter unpacks record.args into five fields,
    and a request path is the client's to choose."""
    from uvicorn.logging import AccessFormatter

    logger = logging.getLogger("test.refused_header_redaction.access")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    logger.addHandler(handler)
    logger.addFilter(RefusedHeaderRedactFilter())
    logger.propagate = False
    try:
        logger.warning('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "POST", f"/Illegal header value b'{SECRET}'", "1.1", 404)
    finally:
        logger.removeHandler(handler)

    text = stream.getvalue()
    assert SECRET not in text
    assert text.startswith('1.2.3.4:5 - "POST /Illegal header value [REDACTED]')
    assert "404" in text


def test_a_quote_split_between_format_string_and_argument_is_masked(logged):
    logger, stream = logged

    logger.warning("Illegal header value %s", f"b'Bearer {SECRET} '")

    assert SECRET not in stream.getvalue()
    assert "Illegal header value [REDACTED]" in stream.getvalue()
    assert "Logging error" not in stream.getvalue()
