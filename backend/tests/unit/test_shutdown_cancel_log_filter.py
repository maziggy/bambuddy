"""A stream still open at shutdown is closed with a line, not a traceback.

The service caps uvicorn's graceful shutdown at 5 s (test_launcher_shutdown_config):
an MJPEG camera stream never ends by itself, so uvicorn cancels it. Each cancelled
request was then logged at ERROR with a full traceback ("Exception in ASGI
application ... CancelledError: Task cancelled, timeout graceful shutdown
exceeded"), on every restart with a camera view open. Expected, harmless, and
alarming in a support bundle. It is now one INFO line, and uvicorn's own
"Cancel N running task(s)" line is INFO too. A real error at any time, and any
other cancellation, keeps its level and traceback.
"""

import asyncio
import logging

import pytest

from backend.app.core.logging_filters import ShutdownCancelledRequestFilter

SHUTDOWN_CANCEL = "Task cancelled, timeout graceful shutdown exceeded"


def _record(msg, args=None, exc=None, level=logging.ERROR):
    exc_info = (type(exc), exc, None) if exc is not None else None
    return logging.LogRecord("uvicorn.error", level, "", 0, msg, args, exc_info)


@pytest.fixture
def f():
    return ShutdownCancelledRequestFilter()


def test_a_request_cancelled_at_shutdown_is_one_info_line(f):
    record = _record("Exception in ASGI application\n", exc=asyncio.CancelledError(SHUTDOWN_CANCEL))

    assert f.filter(record) is True
    assert (record.levelno, record.levelname) == (logging.INFO, "INFO")
    assert record.exc_info is None and record.exc_text is None
    assert "closed at shutdown" in record.getMessage()


def test_uvicorns_count_of_cancelled_tasks_is_info(f):
    record = _record("Cancel %s running task(s), timeout graceful shutdown exceeded", (2,))

    f.filter(record)

    assert record.levelno == logging.INFO
    assert record.getMessage() == "Cancel 2 running task(s), timeout graceful shutdown exceeded"


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("boom"), asyncio.CancelledError(), asyncio.CancelledError("client went away")],
)
def test_other_failures_keep_their_traceback(f, exc):
    record = _record("Exception in ASGI application\n", exc=exc)

    f.filter(record)

    assert record.levelno == logging.ERROR
    assert record.exc_info is not None


def test_it_is_on_uvicorns_logger_ahead_of_the_traceback_filters():
    """Attached by main; first, so no other filter renders the traceback."""
    from backend.app import main  # noqa: F401 - installs the filters

    filters = logging.getLogger("uvicorn.error").filters
    assert isinstance(filters[0], ShutdownCancelledRequestFilter)


def test_what_the_log_file_gets():
    """Through the real logger: a line, no traceback."""
    import io

    from backend.app import main  # noqa: F401 - installs the filters

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("uvicorn.error")
    logger.addHandler(handler)
    try:
        try:
            raise asyncio.CancelledError(SHUTDOWN_CANCEL)
        except asyncio.CancelledError as exc:
            logger.error("Exception in ASGI application\n", exc_info=exc)
    finally:
        logger.removeHandler(handler)

    written = stream.getvalue()
    assert written.startswith("INFO Request closed at shutdown")
    assert "Traceback" not in written
