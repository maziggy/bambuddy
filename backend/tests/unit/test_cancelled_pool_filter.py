"""Tests for the SQLAlchemy connection-pool cancellation noise filter (#1112)."""

from __future__ import annotations

import asyncio
import logging

from backend.app.core.logging_filters import CancelledPoolNoiseFilter


def _make_record(message: str, *, exc: BaseException | None = None) -> logging.LogRecord:
    """Build a `LogRecord` carrying `message` (no positional args) and
    optionally an `exc_info` tuple holding `exc`."""
    record = logging.LogRecord(
        name="sqlalchemy.pool.impl.AsyncAdaptedQueuePool",
        level=logging.ERROR,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=(),
        exc_info=(type(exc), exc, exc.__traceback__) if exc is not None else None,
    )
    return record


class TestCancelledPoolNoiseFilter:
    """Drops the cancellation cascade, keeps real pool errors visible."""

    def test_drops_terminate_with_cancelled_exc(self):
        cancel = asyncio.CancelledError("Cancelled via cancel scope")
        record = _make_record("Exception terminating connection <ABC>", exc=cancel)
        assert CancelledPoolNoiseFilter().filter(record) is False

    def test_drops_gc_cleanup_record(self):
        # GC cleanup messages have no exc_info attached — match by prefix.
        record = _make_record("The garbage collector is trying to clean up non-checked-in connection <ABC>")
        assert CancelledPoolNoiseFilter().filter(record) is False

    def test_keeps_terminate_with_real_oserror(self):
        """A genuine connection-terminate failure (network hiccup, broken
        socket) carries a non-cancellation exc_info chain. That's a real
        problem the user should see — must NOT be dropped."""
        oserr = OSError("broken pipe")
        record = _make_record("Exception terminating connection <ABC>", exc=oserr)
        assert CancelledPoolNoiseFilter().filter(record) is True

    def test_keeps_terminate_without_exc_info(self):
        """If for any reason `exc_info` is missing on a terminate record,
        keep it — only filter when we have positive evidence it's the
        cancellation cascade."""
        record = _make_record("Exception terminating connection <ABC>")
        assert CancelledPoolNoiseFilter().filter(record) is True

    def test_keeps_unrelated_pool_message(self):
        """Other pool messages (pool size warnings, etc.) keep flowing."""
        record = _make_record("Pool size has been exceeded; will spawn overflow")
        assert CancelledPoolNoiseFilter().filter(record) is True

    def test_drops_when_cancelled_is_in_cause_chain(self):
        """Real-world traceback: SQLAlchemy wraps the CancelledError in a
        chained exception. The filter walks `__cause__`/`__context__` so a
        chained CancelledError still counts."""
        cancel = asyncio.CancelledError()
        wrapper = RuntimeError("terminate failed")
        wrapper.__cause__ = cancel
        record = _make_record("Exception terminating connection <ABC>", exc=wrapper)
        assert CancelledPoolNoiseFilter().filter(record) is False

    def test_handles_self_referential_cause_chain(self):
        """Defensive: malformed exception chains (rare but possible) must
        not loop forever — the `seen` set guards against it."""
        a = RuntimeError("a")
        a.__cause__ = a  # pathological
        record = _make_record("Exception terminating connection <ABC>", exc=a)
        # Doesn't loop, doesn't raise, returns True (no CancelledError found).
        assert CancelledPoolNoiseFilter().filter(record) is True
