"""Tests for ``backend.app.core.trace`` — the per-request trace ID
plumbing that ties uvicorn HTTP access lines to the application log
records produced while handling that request.

These tests stay at the unit level: the ContextVar / filter / inbound-
ID validator can each be exercised directly without spinning up a real
FastAPI app, and going through Starlette's TestClient just to assert
"the middleware sets a header" would obscure rather than illuminate the
contract.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from backend.app.core.trace import (
    TRACE_ID_PLACEHOLDER,
    TraceIDFilter,
    generate_trace_id,
    get_trace_id,
    normalise_inbound_trace_id,
    trace_id_var,
)


@pytest.fixture(autouse=True)
def _reset_trace_id():
    """Each test gets a fresh ``trace_id_var`` — without the reset, a
    test that sets the var would leak its value into the next test
    running on the same event loop, producing surprising 'why is this
    other test seeing my ID?' failures."""
    token = trace_id_var.set(TRACE_ID_PLACEHOLDER)
    try:
        yield
    finally:
        trace_id_var.reset(token)


def _record(message: str = "irrelevant") -> logging.LogRecord:
    """Build a vanilla log record — the filter doesn't care about its
    contents, only the surrounding ContextVar value at filter time."""
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0, msg=message, args=None, exc_info=None
    )


class TestPlaceholderWhenUnset:
    def test_get_trace_id_returns_placeholder_outside_request(self):
        """Code paths with no HTTP request scope (startup, MQTT
        callbacks, scheduled tasks) must see the placeholder rather
        than ``None``, so the format-string column is always populated
        and missing values stay greppable."""
        assert get_trace_id() == TRACE_ID_PLACEHOLDER

    def test_filter_sets_placeholder_when_no_request_context(self):
        """The filter must annotate every record, including those
        emitted when no request is in flight — the format string would
        otherwise raise KeyError on those records."""
        record = _record()
        assert TraceIDFilter().filter(record) is True
        assert record.trace_id == TRACE_ID_PLACEHOLDER


class TestRequestScopePropagation:
    def test_filter_picks_up_active_request_id(self):
        """Inside a request, the ContextVar holds that request's ID and
        the filter copies it onto the record — this is the whole point
        of the plumbing."""
        trace_id_var.set("abc12345")
        record = _record()
        TraceIDFilter().filter(record)
        assert record.trace_id == "abc12345"

    @pytest.mark.asyncio
    async def test_id_propagates_into_spawned_task(self):
        """asyncio copies the current context into ``create_task``, so
        background work spawned from inside a request inherits the same
        trace ID without explicit threading. This is why a ContextVar
        beats ``request.state``: state doesn't survive the hop."""
        trace_id_var.set("parent01")

        captured: list[str] = []

        async def _child():
            captured.append(get_trace_id())

        await asyncio.create_task(_child())
        assert captured == ["parent01"]

    @pytest.mark.asyncio
    async def test_concurrent_requests_do_not_leak_ids_into_each_other(self):
        """Two concurrent requests each see only their own trace ID —
        if the filter ever started reading from the wrong context (e.g.
        a process-global) this test would catch it immediately."""
        seen: dict[str, str] = {}

        async def _request(label: str, tid: str):
            trace_id_var.set(tid)
            # Yield to the scheduler so the other coroutine has a chance
            # to overwrite a poorly-scoped global if one existed.
            await asyncio.sleep(0)
            seen[label] = get_trace_id()

        await asyncio.gather(
            _request("a", "aaaaaaaa"),
            _request("b", "bbbbbbbb"),
        )
        assert seen == {"a": "aaaaaaaa", "b": "bbbbbbbb"}


class TestGenerateTraceId:
    def test_generated_ids_are_hex(self):
        tid = generate_trace_id()
        int(tid, 16)  # raises ValueError if not hex
        assert tid

    def test_generated_ids_are_unique_across_calls(self):
        """secrets.token_hex; collisions across a handful of calls would
        signal a generator regression rather than statistical bad luck."""
        ids = {generate_trace_id() for _ in range(200)}
        assert len(ids) == 200


class TestNormaliseInboundTraceId:
    """Hostile / buggy callers sending ``X-Trace-Id`` must NOT be able
    to push log-injection payloads (newlines, control chars, megabyte
    blobs) into bambuddy.log via the trace ID column. Anything that
    fails the gate gets ``None`` so the middleware mints fresh."""

    def test_none_input_returns_none(self):
        assert normalise_inbound_trace_id(None) is None

    def test_empty_string_returns_none(self):
        """An explicit ``X-Trace-Id:`` header with empty value is
        indistinguishable from no header for our purposes — mint fresh.
        """
        assert normalise_inbound_trace_id("") is None

    def test_short_alphanumeric_accepted(self):
        assert normalise_inbound_trace_id("abc123") == "abc123"

    def test_uuid_format_accepted(self):
        """32-char hex (UUID-style without dashes) is the most common
        real-world correlation ID format — must round-trip unchanged."""
        uuid_like = "0123456789abcdef0123456789abcdef"
        assert normalise_inbound_trace_id(uuid_like) == uuid_like

    def test_dash_and_underscore_accepted(self):
        """Datadog / OpenTelemetry frequently use dashes between span
        components; underscores show up in some Bambu-internal IDs we
        might want to echo. Both stay in the whitelist."""
        assert normalise_inbound_trace_id("trace-abc_123") == "trace-abc_123"

    @pytest.mark.parametrize(
        "hostile",
        [
            "abc def",  # space — could split log-line columns
            "abc\ndef",  # newline — log injection
            "abc\rdef",  # carriage return — log injection
            "abc\tdef",  # tab — column drift
            'abc"def',  # quote — could break grep-friendly delimiters
            "abc;def",  # semicolon — script-injection-shaped
            "abc<def",  # angle bracket — XSS-shaped
            "abc/def",  # slash — looks like a path
        ],
    )
    def test_hostile_payloads_rejected(self, hostile):
        """Each rejected character is one the regex whitelist intentionally
        excludes; this parametrised set documents the threat model and
        will fail loud if the regex ever drifts."""
        assert normalise_inbound_trace_id(hostile) is None

    def test_overlong_input_rejected(self):
        """A 1KB X-Trace-Id should never end up in every log line for
        the duration of a request — bound it strictly."""
        assert normalise_inbound_trace_id("a" * 65) is None

    def test_max_length_boundary_accepted(self):
        """The configured cap (currently 64) must accept exactly 64
        chars; one off-by-one would silently reject UUID-like IDs that
        happen to land at the boundary."""
        assert normalise_inbound_trace_id("a" * 64) == "a" * 64


class TestFilterMustBeAttachedToHandlerNotLogger:
    """A filter on a Logger only fires for records that *originate* at that
    logger — records propagated up from child loggers (every backend.* logger
    in the app) never trigger it. Attaching TraceIDFilter to root_logger meant
    child-logger records arrived at the file handler with no trace_id
    attribute, the formatter raised KeyError, and the record was silently
    dropped — manifesting as "logs/bambuddy.log only shows logs partially".
    The filter must live on each *handler* so every record passing through it
    gets annotated regardless of which logger emitted it."""

    def test_handler_level_filter_fires_on_child_logger_propagation(self):
        import io

        root = logging.getLogger("test_trace_filter_handler_path")
        root.setLevel(logging.DEBUG)
        root.handlers.clear()
        root.filters.clear()

        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        handler.setFormatter(logging.Formatter("%(trace_id)s|%(message)s"))
        handler.addFilter(TraceIDFilter())
        root.addHandler(handler)

        child = logging.getLogger("test_trace_filter_handler_path.child")
        try:
            child.info("hi from child")
            handler.flush()
            assert f"{TRACE_ID_PLACEHOLDER}|hi from child" in captured.getvalue()
        finally:
            root.handlers.clear()
            root.filters.clear()
