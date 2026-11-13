"""A printer receiving Bambuddy's upload is quiet, not stale (#3538).

An A1 publishes nothing over MQTT while it receives a large FTPS upload. A
19 MB file took three minutes, the stale check force-closed the connection
42 ms before the print command, and the command found the printer
disconnected and failed the job. Silence during an upload, and within
STALE_TIMEOUT after it, no longer counts.
"""

import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.printer_manager import PrinterManager
from backend.tests.unit.test_scheduler_busy_defer_2598 import dispatch_case  # noqa: F401 - fixture


def _silent_client(silence: float) -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="A1", access_code="12345678")
    client.state.connected = True
    client._last_message_time = time.time() - silence
    return client


class TestStaleCheck:
    def test_a_silent_printer_is_still_stale(self):
        assert _silent_client(BambuMQTTClient.STALE_TIMEOUT + 10).is_stale()

    def test_not_stale_while_an_upload_is_running(self):
        client = _silent_client(BambuMQTTClient.STALE_TIMEOUT + 120)
        client.begin_upload()

        assert not client.is_stale()

    def test_the_timeout_counts_from_the_end_of_the_upload(self):
        client = _silent_client(BambuMQTTClient.STALE_TIMEOUT + 120)
        client.begin_upload()
        client.end_upload()

        assert not client.is_stale(), "the printer gets STALE_TIMEOUT after the upload to speak"

        client._last_upload_end = time.time() - BambuMQTTClient.STALE_TIMEOUT - 1
        assert client.is_stale(), "silence long after the upload is staleness again"

    def test_no_forced_reconnect_under_the_upload(self):
        client = _silent_client(BambuMQTTClient.STALE_TIMEOUT + 120)
        client.force_reconnect_stale_session = MagicMock()
        client._client = MagicMock()
        client.begin_upload()

        assert client.check_staleness() is True
        assert client.state.connected
        client._client.disconnect.assert_not_called()

    def test_overlapping_uploads_keep_it_quiet_until_the_last_ends(self):
        client = _silent_client(BambuMQTTClient.STALE_TIMEOUT + 120)
        client.begin_upload()
        client.begin_upload()
        client.end_upload()

        assert client._uploads_in_flight == 1
        assert not client.is_stale()


class TestUploadingContext:
    def test_marks_the_printers_client_for_the_upload(self):
        manager = PrinterManager()
        client = _silent_client(0)
        manager._clients[7] = client

        with manager.uploading(7):
            assert client._uploads_in_flight == 1
        assert client._uploads_in_flight == 0
        assert client._last_upload_end > 0

    def test_ends_on_the_client_it_began_on(self):
        manager = PrinterManager()
        old, new = _silent_client(0), _silent_client(0)
        manager._clients[7] = old

        with manager.uploading(7):
            manager._clients[7] = new  # reconnect replaced the client meanwhile
        assert old._uploads_in_flight == 0
        assert new._uploads_in_flight == 0

    def test_ends_when_the_upload_raises(self):
        manager = PrinterManager()
        client = _silent_client(0)
        manager._clients[7] = client

        with pytest.raises(RuntimeError), manager.uploading(7):
            raise RuntimeError("upload failed")
        assert client._uploads_in_flight == 0

    def test_an_unknown_printer_is_a_no_op(self):
        with PrinterManager().uploading(None), PrinterManager().uploading(99):
            pass


@pytest.mark.asyncio
async def test_the_queue_marks_its_upload(dispatch_case):
    """The queue's upload runs inside the uploading window of its printer."""
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.services.print_scheduler import PrintScheduler, printer_manager
    from backend.tests.unit.test_scheduler_busy_defer_2598 import _base_patches

    scheduler = PrintScheduler()
    client = _silent_client(0)
    seen: list[int] = []

    async def upload(*args, **kwargs):
        seen.append(client._uploads_in_flight)
        return True

    get_status = MagicMock(return_value=MagicMock(state="IDLE", subtask_id=None, gcode_file=None))
    async with dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, dispatch_case.ids.item_id)
        with ExitStack() as stack:
            for p in _base_patches(scheduler, dispatch_case, AsyncMock(side_effect=upload), MagicMock(), get_status):
                stack.enter_context(p)
            stack.enter_context(patch.dict(printer_manager._clients, {dispatch_case.ids.printer_id: client}))
            await scheduler._start_print(db, item)

    assert seen == [1]
    assert client._uploads_in_flight == 0


@pytest.mark.asyncio
async def test_the_firmware_upload_marks_its_upload():
    """The firmware update's upload is quiet for the printer the same way."""
    import inspect

    from backend.app.services import firmware_update

    source = inspect.getsource(firmware_update)
    upload_at = source.index("success = await with_ftp_retry(")
    window_at = source.rindex("with printer_manager.uploading(printer_id):", 0, upload_at)
    assert upload_at - window_at < 200
