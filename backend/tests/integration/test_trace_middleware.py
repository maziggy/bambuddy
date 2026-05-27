"""Integration test for the trace-ID middleware contract.

Tests focus on observable surface — what headers go out, what
ContextVar value the route handler sees — rather than re-testing the
ContextVar / filter primitives (those are covered in
``tests/unit/test_trace.py``).

A minimal FastAPI app is used instead of the production ``backend.app.main``
app: importing main.py would pull in the entire startup graph (DB
migrations, MQTT subscribers, scheduler, etc.) just to assert "the
middleware sets a header", and that overhead would dwarf the test value.
The middleware function is copied inline so the test pins the exact
contract expected of it.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.trace import (
    generate_trace_id,
    get_trace_id,
    normalise_inbound_trace_id,
    trace_id_var,
)


def _build_app_with_trace_middleware() -> FastAPI:
    """Construct a minimal FastAPI app with the trace middleware wired up
    the same way main.py does it."""
    app = FastAPI()

    @app.middleware("http")
    async def trace_id_middleware(request, call_next):
        inbound = normalise_inbound_trace_id(request.headers.get("X-Trace-Id"))
        trace_id = inbound if inbound is not None else generate_trace_id()
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.get("/echo-trace")
    async def echo_trace():
        # Read the ContextVar from inside the request handler so the
        # test can assert that what's in the header matches what
        # downstream code sees. If these ever diverge, application
        # logs would be stamped with a different ID than the one the
        # client gets back — useless for correlation.
        return {"trace_id": get_trace_id()}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app_with_trace_middleware())


class TestGeneratedTraceId:
    def test_response_carries_x_trace_id_header(self, client):
        """Every response must echo X-Trace-Id so a client can paste it
        into a server-side log search later — without it, the trace ID
        column in bambuddy.log is one-way only."""
        response = client.get("/echo-trace")
        assert response.status_code == 200
        assert "X-Trace-Id" in response.headers
        assert response.headers["X-Trace-Id"]

    def test_generated_id_matches_handler_view(self, client):
        """The X-Trace-Id header value must equal what the route handler
        saw in its ContextVar — otherwise client-side and server-side
        log searches use different keys and never join up."""
        response = client.get("/echo-trace")
        body_id = response.json()["trace_id"]
        header_id = response.headers["X-Trace-Id"]
        assert body_id == header_id

    def test_each_request_gets_a_unique_id(self, client):
        """Two consecutive requests should produce two different IDs —
        otherwise the column in the log file is useless for telling
        requests apart."""
        first = client.get("/echo-trace").headers["X-Trace-Id"]
        second = client.get("/echo-trace").headers["X-Trace-Id"]
        assert first != second

    def test_generated_id_format_is_short_hex(self, client):
        """Bound the visible width and shape of the column. If the
        generator ever switches format (e.g. UUID-with-dashes) the
        format-string column width changes and grep patterns that
        downstream tooling might rely on break — make the change
        deliberate by failing this test instead."""
        tid = client.get("/echo-trace").headers["X-Trace-Id"]
        assert re.fullmatch(r"[0-9a-f]+", tid), tid
        assert 4 <= len(tid) <= 32


class TestInboundTraceIdRespected:
    def test_safe_inbound_id_is_echoed(self, client):
        """When the caller sends a sane X-Trace-Id, we honour it — this
        is the cross-system correlation case (caller's tracing system
        wants its span ID propagated)."""
        response = client.get("/echo-trace", headers={"X-Trace-Id": "client-sent-abc123"})
        assert response.headers["X-Trace-Id"] == "client-sent-abc123"
        assert response.json()["trace_id"] == "client-sent-abc123"

    def test_hostile_inbound_id_is_replaced(self, client):
        """A header that fails the validator (control chars,
        log-injection-shaped chars, etc.) must NOT reach the response
        header or the log column — silently mint fresh and carry on,
        so a hostile/buggy caller can't break our log file but also
        can't break their own request by sending a bad header."""
        response = client.get("/echo-trace", headers={"X-Trace-Id": "abc\ndef rm -rf /"})
        echoed = response.headers["X-Trace-Id"]
        assert echoed != "abc\ndef rm -rf /"
        assert "\n" not in echoed
        assert " " not in echoed

    def test_overlong_inbound_id_is_replaced(self, client):
        """The cap protects bambuddy.log from a 1KB-per-line blowup if
        a caller sends a huge X-Trace-Id."""
        too_long = "a" * 100
        response = client.get("/echo-trace", headers={"X-Trace-Id": too_long})
        assert response.headers["X-Trace-Id"] != too_long


class TestContextResetAfterRequest:
    def test_trace_id_var_resets_after_request_completes(self, client):
        """The middleware must reset the ContextVar in its ``finally``
        block. Without this, a record emitted in a totally unrelated
        background task that happens to inherit the test client's
        context would keep referencing a long-gone request's ID."""
        from backend.app.core.trace import TRACE_ID_PLACEHOLDER

        client.get("/echo-trace")
        # After the request returns, the test fixture's context should
        # no longer hold the request's ID.
        assert get_trace_id() == TRACE_ID_PLACEHOLDER
