"""Query-string tokens must not reach the logs.

The SPA authenticates its WebSocket and media requests with a short-lived
token in the query string, and uvicorn logs every path with its query -- the
access line for a GET and the "WebSocket ... [accepted]" line for an upgrade.
Those go to the console, and from there into `docker logs` and support
bundles built from them. ``QueryTokenRedactFilter`` masks the value on the way
out; ``sanitize_log_content`` does the same for log text already written.
"""

from __future__ import annotations

import io
import logging

import pytest

from backend.app.core.logging_filters import QueryTokenRedactFilter, redact_query_tokens
from backend.app.services.log_reader import sanitize_log_content

# Made up. Never paste a token from a real log here, expired or not.
TOKEN = "fake-test-token"


def _record(msg: str, args) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.error", level=logging.INFO, pathname="", lineno=0, msg=msg, args=args, exc_info=None
    )


class TestRedactQueryTokens:
    @pytest.mark.parametrize(
        "text, expected",
        [
            (f"/api/v1/ws?token={TOKEN}", "/api/v1/ws?token=[REDACTED]"),
            (
                f"/api/v1/printers/1/camera/stream?fps=10&token={TOKEN}",
                "/api/v1/printers/1/camera/stream?fps=10&token=[REDACTED]",
            ),
            (f"/x?token={TOKEN}&fps=5", "/x?token=[REDACTED]&fps=5"),
            (f'"WebSocket /api/v1/ws?token={TOKEN}" [accepted]', '"WebSocket /api/v1/ws?token=[REDACTED]" [accepted]'),
            (f"/x?access_token={TOKEN}", "/x?access_token=[REDACTED]"),
            (f"/x?api_key={TOKEN}", "/x?api_key=[REDACTED]"),
        ],
    )
    def test_masks_the_value(self, text, expected):
        assert redact_query_tokens(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "/api/v1/printers?status=online",
            "/api/v1/archives?mytoken=abc",  # a different parameter that merely ends in "token"
            "token=abc in prose, not a query",
            "",
            None,
        ],
    )
    def test_leaves_everything_else_alone(self, text):
        assert redact_query_tokens(text) == text


class TestQueryTokenRedactFilter:
    def test_websocket_accept_line(self):
        """Uvicorn's own format: the path is an argument, not part of msg."""
        record = _record('%s - "WebSocket %s" [accepted]', ("192.168.255.4:0", f"/api/v1/ws?token={TOKEN}"))
        assert QueryTokenRedactFilter().filter(record) is True
        assert TOKEN not in record.getMessage()
        assert record.getMessage() == '192.168.255.4:0 - "WebSocket /api/v1/ws?token=[REDACTED]" [accepted]'

    def test_access_line_keeps_its_other_arguments(self):
        record = _record(
            '%s - "%s %s HTTP/%s" %d',
            ("10.0.0.2:5000", "GET", f"/api/v1/printers/1/camera/stream?token={TOKEN}", "1.1", 200),
        )
        QueryTokenRedactFilter().filter(record)
        assert record.getMessage() == (
            '10.0.0.2:5000 - "GET /api/v1/printers/1/camera/stream?token=[REDACTED] HTTP/1.1" 200'
        )

    def test_message_without_arguments(self):
        record = _record(f"GET /api/v1/ws?token={TOKEN}", None)
        QueryTokenRedactFilter().filter(record)
        assert record.getMessage() == "GET /api/v1/ws?token=[REDACTED]"

    def test_through_a_real_logger_and_handler(self):
        """Attached to the logger, it covers whatever handler writes the line."""
        logger = logging.getLogger("test.query_token_redaction")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        logger.addFilter(QueryTokenRedactFilter())
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            logger.info('%s - "WebSocket %s" [accepted]', "fe80::1:0", f"/api/v1/ws?token={TOKEN}")
        finally:
            logger.removeHandler(handler)
        assert TOKEN not in stream.getvalue()
        assert "token=[REDACTED]" in stream.getvalue()


def test_main_attaches_the_filter_to_both_uvicorn_loggers():
    import backend.app.main  # noqa: F401 -- importing configures logging

    for name in ("uvicorn.access", "uvicorn.error"):
        assert any(isinstance(f, QueryTokenRedactFilter) for f in logging.getLogger(name).filters), name


def test_support_bundle_sanitizer_masks_tokens():
    line = f'INFO: 192.168.255.4:0 - "WebSocket /api/v1/ws?token={TOKEN}" [accepted]'
    assert TOKEN not in sanitize_log_content(line)
    assert "token=[REDACTED]" in sanitize_log_content(line)
