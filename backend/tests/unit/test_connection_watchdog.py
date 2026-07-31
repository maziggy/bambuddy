"""Tests for the dead-MQTT-session watchdog (#2732).

``check_staleness()`` guards the "connected but silent" session and returns
immediately once ``state.connected`` is False — from there, paho's own
auto-reconnect is the only thing still watching. The #2732 bundle shows what
happens when that stops making progress: a P1S dropped on a keep-alive timeout
at 02:19 and did not reconnect until 11:24, nine hours offline with the UI open
throughout.

This watchdog is the backstop. The rules it has to keep are narrow on purpose —
it must not interfere with a session that is recovering on its own, and it must
not churn clients for printers that are simply switched off.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import (
    CONNECTION_WATCHDOG_OFFLINE_GRACE,
    CONNECTION_WATCHDOG_RETRY_INTERVAL,
    _connection_watchdog_last_attempt,
    _recover_dead_printer_sessions,
)


def _client(*, connected: bool, last_message_age: float | None, ip: str = "192.168.1.100"):
    """Stand-in for BambuMQTTClient with only the fields the watchdog reads."""
    return SimpleNamespace(
        state=SimpleNamespace(connected=connected),
        _last_message_time=0.0 if last_message_age is None else time.time() - last_message_age,
        ip_address=ip,
        last_connect_error=None,
        force_reconnect_stale_session=MagicMock(),
    )


async def _sweep(clients: dict, *, port_open: bool = True):
    with (
        patch("backend.app.main.printer_manager._clients", clients),
        patch("backend.app.services.printer_diagnostic.check_port", AsyncMock(return_value=port_open)),
    ):
        return await _recover_dead_printer_sessions()


@pytest.fixture(autouse=True)
def _clear_cooldowns():
    _connection_watchdog_last_attempt.clear()
    yield
    _connection_watchdog_last_attempt.clear()


class TestRebuildsDeadSessions:
    @pytest.mark.asyncio
    async def test_rebuilds_a_long_dead_session(self):
        """The #2732 case: offline for hours, printer answering the whole time."""
        client = _client(connected=False, last_message_age=32718.0)

        assert await _sweep({1: client}) == 1
        client.force_reconnect_stale_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_reason_names_the_duration(self):
        client = _client(connected=False, last_message_age=32718.0)
        await _sweep({1: client})
        assert "32718" in client.force_reconnect_stale_session.call_args.args[0]

    @pytest.mark.asyncio
    async def test_sweeps_every_printer_in_the_farm(self):
        clients = {i: _client(connected=False, last_message_age=9999.0) for i in range(1, 4)}
        assert await _sweep(clients) == 3


class TestLeavesHealthyAndRecoveringSessionsAlone:
    @pytest.mark.asyncio
    async def test_connected_printer_is_untouched(self):
        client = _client(connected=True, last_message_age=99999.0)
        assert await _sweep({1: client}) == 0
        client.force_reconnect_stale_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_inside_the_grace_period_paho_keeps_the_job(self):
        """Below the grace window a reconnect may well be in flight; interrupting
        it would turn a self-healing blip into a forced session rebuild."""
        client = _client(connected=False, last_message_age=CONNECTION_WATCHDOG_OFFLINE_GRACE - 30)
        assert await _sweep({1: client}) == 0
        client.force_reconnect_stale_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_client_that_never_had_a_session_is_left_to_paho(self):
        """No inbound message ever means this is the initial connect, where
        retrying is both correct and the only thing to do."""
        client = _client(connected=False, last_message_age=None)
        assert await _sweep({1: client}) == 0
        client.force_reconnect_stale_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnecting_clears_the_cooldown(self):
        """A printer that comes back must not carry a stale cooldown into its
        next outage."""
        client = _client(connected=False, last_message_age=9999.0)
        await _sweep({1: client})
        assert 1 in _connection_watchdog_last_attempt

        client.state.connected = True
        await _sweep({1: client})
        assert 1 not in _connection_watchdog_last_attempt


class TestUnreachablePrinters:
    @pytest.mark.asyncio
    async def test_switched_off_printer_is_not_rebuilt(self):
        """Rebuilding a client against a host that isn't answering achieves
        nothing and would log a warning per printer all night."""
        client = _client(connected=False, last_message_age=9999.0)
        assert await _sweep({1: client}, port_open=False) == 0
        client.force_reconnect_stale_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_unreachable_printer_still_takes_the_cooldown(self):
        """Otherwise every sweep re-probes the port of every dead printer."""
        client = _client(connected=False, last_message_age=9999.0)
        await _sweep({1: client}, port_open=False)
        assert 1 in _connection_watchdog_last_attempt


class TestRetryInterval:
    @pytest.mark.asyncio
    async def test_does_not_rebuild_again_within_the_interval(self):
        client = _client(connected=False, last_message_age=9999.0)
        assert await _sweep({1: client}) == 1
        assert await _sweep({1: client}) == 0
        client.force_reconnect_stale_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_once_the_interval_has_passed(self):
        client = _client(connected=False, last_message_age=9999.0)
        await _sweep({1: client})
        _connection_watchdog_last_attempt[1] -= CONNECTION_WATCHDOG_RETRY_INTERVAL + 1

        assert await _sweep({1: client}) == 1
        assert client.force_reconnect_stale_session.call_count == 2


class TestSweepIsFaultTolerant:
    @pytest.mark.asyncio
    async def test_one_broken_client_does_not_stop_the_others(self):
        """A farm sweep that aborts on the first bad client would leave every
        printer after it unrecovered."""
        bad = _client(connected=False, last_message_age=9999.0)
        bad.force_reconnect_stale_session.side_effect = RuntimeError("boom")
        good = _client(connected=False, last_message_age=9999.0)

        assert await _sweep({1: bad, 2: good}) == 2
        good.force_reconnect_stale_session.assert_called_once()
