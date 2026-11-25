"""A printer that reconnects is not held to its upload backoff (#3563).

An X2D's file service stopped finishing the TLS handshake, so every upload
timed out and the backoff grew to an hour. Restarting the printer fixed it, but
the queue kept waiting out the hour: only the timer ended the backoff, and only
a Bambuddy restart, which forgets it, got the job going.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app import main as main_module
from backend.app.services.bambu_ftp import BambuFTPClient
from backend.app.services.print_scheduler import UPLOAD_FAILURE_BACKOFF_SECONDS, PrintScheduler
from backend.tests.unit.test_printer_offline_notification import _reset_edge_state, _spawn_patch, _state
from backend.tests.unit.test_scheduler_upload_requeue_3210 import (
    HANDSHAKE,
    REFUSING_IP,
    _item,
    _remaining,
    _scheduler,
    _upload_refused_by,
    farm,
)

__all__ = ["_reset_edge_state", "farm"]  # fixtures, used by name

pytestmark = pytest.mark.unit


class TestTheScheduler:
    @pytest.mark.asyncio
    async def test_a_reconnected_printer_gets_the_job_without_waiting(self, farm):
        item_id = await farm.add_item(printer_id=farm.ids.refusing)
        scheduler = PrintScheduler()
        async with _scheduler(farm, _upload_refused_by(REFUSING_IP, HANDSHAKE), scheduler=scheduler):
            await scheduler.check_queue()
        assert farm.ids.refusing in scheduler._upload_backoff

        scheduler.printer_reconnected(farm.ids.refusing, REFUSING_IP)

        async with _scheduler(farm, AsyncMock(return_value=True), scheduler=scheduler):
            await scheduler.check_queue()
        assert (await _item(farm, item_id)).status == "printing"

    @pytest.mark.asyncio
    async def test_a_printer_that_still_refuses_gets_the_next_longer_wait_and_no_second_notice(self, farm):
        await farm.add_item(printer_id=farm.ids.refusing)
        refused = _upload_refused_by(REFUSING_IP, HANDSHAKE)
        scheduler = PrintScheduler()
        waiting = AsyncMock()
        async with _scheduler(farm, refused, scheduler=scheduler, waiting_notify=waiting):
            await scheduler.check_queue()

        scheduler.printer_reconnected(farm.ids.refusing, REFUSING_IP)
        async with _scheduler(farm, refused, scheduler=scheduler, waiting_notify=waiting):
            await scheduler.check_queue()

        assert _remaining(scheduler, farm.ids.refusing) == pytest.approx(2 * UPLOAD_FAILURE_BACKOFF_SECONDS, abs=5)
        assert waiting.await_count == 1

    def test_the_ftp_cool_off_from_before_the_reconnect_goes(self):
        """Inside it the first retry would fail without reaching the printer."""
        scheduler = PrintScheduler()
        BambuFTPClient._handshake_blocked_until["10.0.0.50"] = time.monotonic() + 300
        BambuFTPClient._handshake_blocked_until["10.0.0.51"] = time.monotonic() + 300
        try:
            scheduler.printer_reconnected(7, "10.0.0.50")

            assert not BambuFTPClient.handshake_blocked("10.0.0.50")
            assert BambuFTPClient.handshake_blocked("10.0.0.51")
        finally:
            BambuFTPClient._handshake_blocked_until.pop("10.0.0.50", None)
            BambuFTPClient._handshake_blocked_until.pop("10.0.0.51", None)

    def test_only_the_reconnected_printer_is_let_go(self):
        scheduler = PrintScheduler()
        scheduler._upload_backoff.update({1: time.monotonic() + 600, 2: time.monotonic() + 600})
        scheduler._upload_refusals.update({1: 2, 2: 2})

        scheduler.printer_reconnected(1, None)

        assert 1 not in scheduler._upload_backoff
        assert 2 in scheduler._upload_backoff
        assert scheduler._upload_refusals == {1: 2, 2: 2}


class TestTheReconnect:
    @staticmethod
    async def _status(*connected: bool) -> MagicMock:
        """Feed the status handler these connection states; return the scheduler stand-in."""
        ws_mgr = MagicMock()
        ws_mgr.send_printer_status = AsyncMock()
        relay = MagicMock()
        relay.on_printer_status = AsyncMock()
        relay.on_printer_online = AsyncMock()
        pm = MagicMock()
        pm.get_printer.return_value = SimpleNamespace(name="X2D", serial_number="SN1")
        pm.get_model.return_value = ""
        pm.is_awaiting_plate_clear.return_value = False
        pm.get_client.return_value = SimpleNamespace(ip_address="10.0.0.8")
        scheduler = MagicMock()
        with (
            patch("backend.app.main.ws_manager", ws_mgr),
            patch("backend.app.main.mqtt_relay", relay),
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.print_scheduler", scheduler),
            patch("backend.app.main._maybe_notify_printer_offline", new=AsyncMock()),
            _spawn_patch(),
            patch("backend.app.main.printer_state_to_dict", return_value={}),
        ):
            for c in connected:
                await main_module.on_printer_status_change(1, _state(connected=c))
        return scheduler

    @pytest.mark.asyncio
    async def test_a_printer_back_from_a_dropped_connection_is_let_go(self):
        scheduler = await self._status(True, False, True)

        scheduler.printer_reconnected.assert_called_once_with(1, "10.0.0.8")

    @pytest.mark.asyncio
    async def test_bambuddys_own_start_and_further_updates_are_no_reconnect(self):
        scheduler = await self._status(True, True, True)

        scheduler.printer_reconnected.assert_not_called()
