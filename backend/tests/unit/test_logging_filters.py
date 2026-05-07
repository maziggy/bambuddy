"""Tests for ``backend.app.core.logging_filters.WriteRequestsOnlyFilter``.

The filter is attached to uvicorn's ``access`` logger so we get an on-disk
record of state-changing HTTP requests in ``bambuddy.log`` (incident-triage
need: trace which endpoint fired a state change, e.g. ``stop_print``)
without churning the rotation window with the frontend's high-volume GET
status polls.

Tests use ``logging.LogRecord`` directly rather than spinning up a real
uvicorn server — the contract under test is purely "given this record, do
we keep it" and going through uvicorn would be a heavyweight integration
test for a one-line predicate.
"""

from __future__ import annotations

import logging

import pytest

from backend.app.core.logging_filters import WriteRequestsOnlyFilter


def _record(message: str) -> logging.LogRecord:
    """Build a LogRecord whose message matches uvicorn's access-log shape."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=message,
        args=None,
        exc_info=None,
    )


@pytest.fixture
def filter_under_test() -> WriteRequestsOnlyFilter:
    return WriteRequestsOnlyFilter()


class TestWriteVerbsKept:
    @pytest.mark.parametrize(
        "verb",
        ["POST", "PUT", "PATCH", "DELETE"],
    )
    def test_state_changing_verb_passes(self, filter_under_test, verb):
        """The four verbs that can mutate server state must all be kept —
        POST is by far the most common (the rogue ``stop_print`` we couldn't
        trace was a POST), but PATCH/PUT/DELETE are equally important when
        triaging "who edited / replaced / deleted X at 09:23?"."""
        record = _record(f'192.168.1.42:54812 - "{verb} /api/v1/printers/1/print/stop HTTP/1.1" 200')
        assert filter_under_test.filter(record) is True


class TestReadOnlyVerbsDropped:
    @pytest.mark.parametrize(
        "request_line",
        [
            "GET /api/v1/printers/1/status HTTP/1.1",
            "GET /api/v1/printers/1/camera/stream HTTP/1.1",
            "HEAD /api/v1/health HTTP/1.1",
            "OPTIONS /api/v1/printers/1/status HTTP/1.1",
        ],
    )
    def test_read_only_verb_dropped(self, filter_under_test, request_line):
        """GET / HEAD / OPTIONS account for the bulk of access traffic on a
        running install (status polls, camera streams, CORS preflights) and
        none of them can change server state, so dropping them keeps
        bambuddy.log focused on lines that matter for incident triage."""
        record = _record(f'192.168.1.42:54812 - "{request_line}" 200')
        assert filter_under_test.filter(record) is False


class TestNoFalseMatchInUrl:
    """The matcher anchors on ``" `` + verb + space so an unrelated literal
    substring inside a URL can't false-match. Important because URLs in
    Bambuddy include things like ``/print/stop``, ``/print/pause`` — words
    that happen to contain the verb names as substrings."""

    def test_url_containing_verb_substring_does_not_match(self, filter_under_test):
        """A literal "POST" inside the URL of a GET request must not flip
        the filter — the verb must appear as the actual HTTP method, with
        the surrounding quote+space anchors uvicorn's format guarantees."""
        record = _record('192.168.1.42:54812 - "GET /api/posts/POST_123 HTTP/1.1" 200')
        assert filter_under_test.filter(record) is False

    def test_path_segment_named_delete_does_not_match(self, filter_under_test):
        record = _record('192.168.1.42:54812 - "GET /api/v1/library/files/DELETE_ME HTTP/1.1" 200')
        assert filter_under_test.filter(record) is False


class TestEdgeCases:
    def test_empty_message_dropped(self, filter_under_test):
        """Defensive: a malformed access record with no body should not
        accidentally pass through."""
        assert filter_under_test.filter(_record("")) is False

    def test_unrelated_log_line_dropped(self, filter_under_test):
        """If the filter ever ends up attached to the wrong logger by
        mistake, it must not leak unrelated records — silent fallthrough
        on application logs would defeat the whole point."""
        record = _record("Bambuddy starting - debug=False, log_level=INFO")
        assert filter_under_test.filter(record) is False

    def test_filter_is_idempotent_across_records(self, filter_under_test):
        """Sanity: filter has no internal state, so repeated calls with the
        same record return the same answer."""
        kept = _record('192.168.1.42:54812 - "POST /api/v1/print/stop HTTP/1.1" 200')
        dropped = _record('192.168.1.42:54812 - "GET /api/v1/printers HTTP/1.1" 200')

        for _ in range(3):
            assert filter_under_test.filter(kept) is True
            assert filter_under_test.filter(dropped) is False
