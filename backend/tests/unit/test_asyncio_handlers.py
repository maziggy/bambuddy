"""Tests for the Windows asyncio Proactor cleanup-RST filter (#1113)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from backend.app.core.asyncio_handlers import (
    _is_proactor_connection_reset,
    _proactor_reset_filter,
    install_proactor_reset_filter,
)


# `_is_proactor_connection_reset` short-circuits on non-Windows; pretend we're
# on Windows for the discrimination tests so they exercise the actual logic.
@pytest.fixture
def fake_windows():
    with patch("backend.app.core.asyncio_handlers.sys.platform", "win32"):
        yield


class TestIsProactorConnectionReset:
    """The discriminator that decides whether a context is the noise we silence."""

    def test_matches_proactor_cleanup_reset(self, fake_windows):
        ctx = {
            "exception": ConnectionResetError(10054, "An existing connection was forcibly closed"),
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        }
        assert _is_proactor_connection_reset(ctx) is True

    def test_rejects_when_not_on_windows(self):
        # No `fake_windows` fixture — sys.platform reflects the real OS.
        ctx = {
            "exception": ConnectionResetError(10054, "irrelevant"),
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        }
        # The whole point of the filter is to be a Windows-only no-op.
        with patch("backend.app.core.asyncio_handlers.sys.platform", "linux"):
            assert _is_proactor_connection_reset(ctx) is False

    def test_rejects_unrelated_connection_reset(self, fake_windows):
        """A real `ConnectionResetError` raised inside an app coroutine —
        not from the Proactor cleanup path — must NOT be suppressed.
        Otherwise we'd hide genuine connectivity bugs."""
        ctx = {
            "exception": ConnectionResetError(),
            "message": "Task exception was never retrieved",
        }
        assert _is_proactor_connection_reset(ctx) is False

    def test_rejects_other_exception_types(self, fake_windows):
        """Other OSErrors (BrokenPipeError, ConnectionAbortedError) might
        share the cleanup path but they're a different signal worth
        keeping visible — we only silence the specific 10054 family."""
        ctx = {
            "exception": BrokenPipeError(),
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        }
        assert _is_proactor_connection_reset(ctx) is False

    def test_rejects_when_no_exception(self, fake_windows):
        """asyncio sometimes invokes the handler with no exception object
        (e.g. resource warnings) — those shouldn't blanket-match."""
        ctx = {"message": "_call_connection_lost was slow"}
        assert _is_proactor_connection_reset(ctx) is False


class TestProactorResetFilter:
    """The handler glue itself — does it suppress the right ones and
    pass everything else through to the default handler?"""

    @pytest.mark.asyncio
    async def test_suppresses_proactor_reset(self, fake_windows):
        loop = asyncio.get_running_loop()
        with patch.object(loop, "default_exception_handler") as default:
            _proactor_reset_filter(
                loop,
                {
                    "exception": ConnectionResetError(10054, "forcibly closed"),
                    "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
                },
            )
        # Suppression = default handler is never reached.
        default.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_unrelated_through_to_default(self, fake_windows):
        """A different uncaught exception must go through asyncio's normal
        path so it surfaces in logs and tests as an actual problem."""
        loop = asyncio.get_running_loop()
        ctx = {
            "exception": ValueError("real bug"),
            "message": "Task exception was never retrieved",
        }
        with patch.object(loop, "default_exception_handler") as default:
            _proactor_reset_filter(loop, ctx)
        default.assert_called_once_with(ctx)


class TestInstallation:
    """Wiring: install_proactor_reset_filter only runs on Windows."""

    @pytest.mark.asyncio
    async def test_install_is_no_op_on_non_windows(self):
        """Linux/macOS use the Selector loop, which doesn't hit this code
        path — the install must be inert so the Linux production path
        keeps the default exception handler untouched."""
        loop = asyncio.get_running_loop()
        with (
            patch("backend.app.core.asyncio_handlers.sys.platform", "linux"),
            patch.object(loop, "set_exception_handler") as setter,
        ):
            installed = install_proactor_reset_filter(loop)
        assert installed is False
        setter.assert_not_called()

    @pytest.mark.asyncio
    async def test_install_attaches_handler_on_windows(self, fake_windows):
        loop = asyncio.get_running_loop()
        with patch.object(loop, "set_exception_handler") as setter:
            installed = install_proactor_reset_filter(loop)
        assert installed is True
        setter.assert_called_once_with(_proactor_reset_filter)
