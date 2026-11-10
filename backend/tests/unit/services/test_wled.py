import asyncio
import logging

import httpx
import pytest

from backend.app.services.bambu_mqtt import HMSError, PrinterState
from backend.app.services.wled import WLEDManager, WLEDResponseError, effective_wled_status


def _state(state: str, **values) -> PrinterState:
    return PrinterState(connected=values.pop("connected", True), state=state, **values)


def _manager(handler) -> tuple[WLEDManager, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WLEDManager(client), client


def test_effective_status_uses_existing_bambuddy_states_and_priorities():
    assert effective_wled_status(_state("IDLE")) == "idle"
    assert effective_wled_status(_state("RUNNING")) == "printing"
    assert effective_wled_status(_state("PRINTING")) == "printing"
    assert effective_wled_status(_state("PREPARE")) == "prepare"
    assert effective_wled_status(_state("SLICING")) == "prepare"
    assert effective_wled_status(_state("PAUSE")) == "paused"
    assert effective_wled_status(_state("FINISH")) == "finished"
    assert effective_wled_status(_state("FAILED")) == "error"
    assert effective_wled_status(_state("IDLE"), awaiting_plate_clear=True, require_plate_clear=True) == "queue_waiting"
    assert effective_wled_status(_state("PAUSE", stg_cur=6)) == "filament_problem"
    assert effective_wled_status(_state("PAUSE", stg_cur=16)) == "paused"
    assert effective_wled_status(_state("PAUSE", stg_cur=16, ams_status_main=1, mc_print_sub_stage=3)) == "paused"
    assert (
        effective_wled_status(_state("PAUSE", stg_cur=6, ams_status_main=0, mc_print_sub_stage=0)) == "filament_problem"
    )
    fault = HMSError(code="0300_0008", attr=0x03000200, module=3, severity=1, description="Nozzle temperature abnormal")
    assert effective_wled_status(_state("IDLE", hms_errors=[fault])) == "hms_error"
    assert effective_wled_status(_state("RUNNING", connected=False)) == "offline"
    assert effective_wled_status(_state("UNKNOWN")) is None


@pytest.mark.parametrize(
    ("state", "awaiting_plate_clear", "require_plate_clear", "expected"),
    [
        ("FINISH", True, False, "finished"),
        ("FAILED", True, False, "error"),
        ("IDLE", True, False, "idle"),
        ("FINISH", True, True, "queue_waiting"),
        ("FAILED", True, True, "queue_waiting"),
        ("IDLE", True, True, "queue_waiting"),
        ("FINISH", False, True, "finished"),
        ("FAILED", False, True, "error"),
        ("RUNNING", True, True, "printing"),
        ("PRINTING", True, True, "printing"),
        ("PREPARE", True, True, "prepare"),
        ("SLICING", True, True, "prepare"),
        ("PAUSE", True, True, "paused"),
        ("UNKNOWN", True, True, None),
    ],
)
def test_plate_gate_precedes_only_idle_finished_and_error(state, awaiting_plate_clear, require_plate_clear, expected):
    assert (
        effective_wled_status(
            _state(state), awaiting_plate_clear=awaiting_plate_clear, require_plate_clear=require_plate_clear
        )
        == expected
    )


@pytest.mark.parametrize(
    ("severity", "description", "actions", "full_code", "expected"),
    [
        (0, "Invalid level", ["OK_BUTTON"], "0500000000004038", "printing"),
        (2, None, [], "0C0001000002001B", "printing"),
        (3, "The top cover is open", [], "0300970000030001", "printing"),
        (1, "Nozzle temperature abnormal", [], "0300020000010008", "hms_error"),
        (2, "Filament fault", [], "0500060000020005", "hms_error"),
        (3, None, ["OK_BUTTON"], "0300970000030001", "hms_error"),
        (3, "Unable to start drying", [], "1880C003", "hms_error"),
    ],
)
def test_hms_filter_matches_notifications(severity, description, actions, full_code, expected):
    fault = HMSError(
        code="fault", attr=0, module=0, severity=severity, description=description, actions=actions, full_code=full_code
    )
    assert effective_wled_status(_state("RUNNING", hms_errors=[fault])) == expected


def test_specific_fault_priorities_are_preserved_with_plate_gate():
    ignored = HMSError(code="ignored", attr=0, module=0, severity=0)
    fault = HMSError(code="fault", attr=0, module=0, severity=2, description="Filament fault")
    for state in ("RUNNING", "FINISH", "FAILED", "IDLE", "PAUSE"):
        assert (
            effective_wled_status(
                _state(state, hms_errors=[ignored, fault]), awaiting_plate_clear=True, require_plate_clear=True
            )
            == "hms_error"
        )
    assert (
        effective_wled_status(
            _state("PAUSE", stg_cur=6, hms_errors=[fault]), awaiting_plate_clear=True, require_plate_clear=True
        )
        == "filament_problem"
    )
    assert effective_wled_status(_state("PAUSE", stg_cur=16, hms_errors=[fault])) == "hms_error"
    assert (
        effective_wled_status(
            _state("FINISH", connected=False, hms_errors=[fault]), awaiting_plate_clear=True, require_plate_clear=True
        )
        == "offline"
    )


@pytest.mark.asyncio
async def test_list_presets_returns_only_valid_named_slots():
    manager, client = _manager(
        lambda request: httpx.Response(
            200,
            json={"0": {}, "3": {"n": "Printing Blue"}, "7": {"n": ""}, "251": {"n": "No"}},
        )
    )
    assert await manager.list_presets("http://wled.local") == [
        {"id": 3, "name": "Printing Blue"},
        {"id": 7, "name": "7"},
    ]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="not-json"), httpx.Response(200, json=[])],
)
async def test_list_presets_rejects_invalid_responses(response):
    manager, client = _manager(lambda request: response)
    with pytest.raises(WLEDResponseError):
        await manager.list_presets("http://wled.local")
    await client.aclose()


@pytest.mark.asyncio
async def test_get_info_returns_optional_wled_identity_and_rejects_invalid_response():
    manager, client = _manager(lambda request: httpx.Response(200, json={"name": " Kitchen LEDs ", "ver": "0.15.0"}))
    assert await manager.get_info("http://wled.local") == {"name": "Kitchen LEDs", "version": "0.15.0"}
    await client.aclose()

    invalid, invalid_client = _manager(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(WLEDResponseError):
        await invalid.get_info("http://wled.local")
    await invalid_client.aclose()


@pytest.mark.asyncio
async def test_send_preset_posts_expected_payload_and_handles_failures(caplog):
    caplog.set_level(logging.INFO, logger="backend.app.services.wled")
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    assert await manager.send_preset(1, "http://wled.local", 3) is True
    assert requests[0].url == httpx.URL("http://wled.local/json/state")
    assert requests[0].content == b'{"ps":3}'
    assert (logging.INFO, "[WLED] Printer 1 state=manual -> preset=3") in [
        (record.levelno, record.message) for record in caplog.records
    ]
    caplog.clear()

    for response in (httpx.Response(500), httpx.Response(200, text="bad")):
        failing, failing_client = _manager(lambda request, result=response: result)
        assert await failing.send_preset(1, "http://wled.local", 3) is False
        await failing_client.aclose()

    offline, offline_client = _manager(lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    assert await offline.send_preset(1, "http://wled.local", 3) is False
    records = [record for record in caplog.records if record.name == "backend.app.services.wled"]
    assert len(records) == 3
    assert all(record.levelno == logging.WARNING and "preset=3 failed" in record.message for record in records)
    assert all("wled.local" not in record.message for record in records)
    await offline_client.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_status_logging_follows_successful_sends_without_poll_duplicates(caplog):
    caplog.set_level(logging.INFO, logger="backend.app.services.wled")
    manager, client = _manager(lambda request: httpx.Response(200, json={"success": True}))
    manager.configure_printer(
        1,
        {
            "enabled": True,
            "base_url": "http://wled.local",
            "presets": {"printing": 32, "finished": 34, "queue_waiting": 36},
        },
    )
    manager.require_plate_clear = True
    for state, awaiting in (("RUNNING", False), ("FINISH", False), ("FINISH", True)):
        manager.handle_status(1, _state(state), awaiting_plate_clear=awaiting)
        await asyncio.sleep(0)
        manager.handle_status(1, _state(state), awaiting_plate_clear=awaiting)
        await asyncio.sleep(0)
    assert [
        (record.levelno, record.message) for record in caplog.records if record.name == "backend.app.services.wled"
    ] == [
        (logging.INFO, "[WLED] Printer 1 state=printing -> preset=32"),
        (logging.INFO, "[WLED] Printer 1 state=finished -> preset=34"),
        (logging.INFO, "[WLED] Printer 1 state=queue_waiting -> preset=36"),
    ]
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_preset_send_does_not_log_a_switch_or_failure(caplog):
    caplog.set_level(logging.INFO, logger="backend.app.services.wled")

    async def handler(request):
        raise asyncio.CancelledError

    manager, client = _manager(handler)
    with pytest.raises(asyncio.CancelledError):
        await manager.send_preset(1, "http://wled.local", 32, status="printing")
    assert not [record for record in caplog.records if record.name == "backend.app.services.wled"]
    await client.aclose()


@pytest.mark.asyncio
async def test_status_changes_are_deduplicated_and_missing_or_disabled_mappings_do_nothing():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    manager.configure_printer(
        1,
        {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3, "paused": 7}},
    )
    manager.handle_status(1, _state("RUNNING"))
    manager.handle_status(1, _state("RUNNING", progress=50))
    await asyncio.sleep(0)
    assert len(sent) == 1

    manager.handle_status(1, _state("PAUSE"))
    await asyncio.sleep(0)
    assert len(sent) == 2

    manager.handle_status(1, _state("IDLE"))
    await asyncio.sleep(0)
    assert len(sent) == 2

    manager.configure_printer(2, {"enabled": False, "base_url": "http://wled.local", "presets": {"idle": 1}})
    manager.handle_status(2, _state("IDLE"))
    await asyncio.sleep(0)
    assert len(sent) == 2
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_status_send_retries_after_the_interval_then_deduplicates_success(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="backend.app.services.wled")
    now = [1000.0]
    monkeypatch.setattr("backend.app.services.wled.time.monotonic", lambda: now[0])
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(503 if len(sent) <= 2 else 200, json={"success": True})

    manager, client = _manager(handler)
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3}})

    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert len(sent) == 1
    assert manager._runtime[1].status is None
    assert manager._runtime[1].preset_id is None

    # Status updates arrive every second or so: none of them retries before
    # the interval is over.
    for _ in range(5):
        now[0] += 1
        manager.handle_status(1, _state("RUNNING"))
        await asyncio.sleep(0)
    assert len(sent) == 1

    now[0] += 30
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert len(sent) == 2

    now[0] += 30
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert len(sent) == 3
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("printing", 3)

    manager.handle_status(1, _state("RUNNING"))
    await asyncio.sleep(0)
    assert len(sent) == 3
    messages = [
        (record.levelno, record.message) for record in caplog.records if record.name == "backend.app.services.wled"
    ]
    # One warning for the whole outage, not one per retry.
    assert messages == [
        (logging.WARNING, "[WLED] Printer 1 state=printing -> preset=3 failed (HTTP 503)"),
        (logging.INFO, "[WLED] Printer 1: retrying every 30s until WLED answers"),
        (logging.INFO, "[WLED] Printer 1 state=printing -> preset=3"),
        (logging.INFO, "[WLED] Printer 1: WLED answers again"),
    ]
    assert all("wled.local" not in message for _, message in messages)
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_a_new_state_is_sent_at_once_during_an_outage_without_a_new_warning(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="backend.app.services.wled")
    monkeypatch.setattr("backend.app.services.wled.time.monotonic", lambda: 1000.0)
    sent = []

    def handler(request: httpx.Request):
        sent.append(request.content)
        return httpx.Response(503)

    manager, client = _manager(handler)
    manager.configure_printer(
        1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3, "paused": 7}}
    )
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    manager.handle_status(1, _state("PAUSE"))
    await manager._runtime[1].send_task
    assert sent == [b'{"ps":3}', b'{"ps":7}']
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_identical_status_does_not_start_parallel_sends():
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def handler(request: httpx.Request):
        sent.append(request)
        started.set()
        await release.wait()
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3}})
    manager.handle_status(1, _state("RUNNING"))
    task = manager._runtime[1].send_task
    await asyncio.wait_for(started.wait(), 1)

    manager.handle_status(1, _state("RUNNING", progress=20))
    manager.handle_status(1, _state("RUNNING", progress=30))
    assert manager._runtime[1].send_task is task
    assert manager._runtime[1].status is None
    assert len(sent) == 1

    release.set()
    await task
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("printing", 3)
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_return_to_previously_confirmed_state_still_retries():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(503 if len(sent) == 2 else 200, json={"success": True})

    manager, client = _manager(handler)
    manager.retry_seconds = 0
    manager.configure_printer(
        1,
        {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3, "paused": 7}},
    )
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert manager._runtime[1].status == "printing"

    manager.handle_status(1, _state("PAUSE"))
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert len(sent) == 2
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("printing", 3)
    assert manager._runtime[1].confirmed_generation != manager._runtime[1].generation

    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert len(sent) == 3
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("printing", 3)
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_manual_preset_restores_automatic_mapping_once_on_next_status():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request.content)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3}})
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("printing", 3)

    assert await manager.test_preset(1, "http://wled.local", 11) is True
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    manager.handle_status(1, _state("RUNNING"))
    await asyncio.sleep(0)
    assert sent == [b'{"ps":3}', b'{"ps":11}', b'{"ps":3}']
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("printing", 3)
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_manual_preset_keeps_automatic_mapping_retryable():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request.content)
        return httpx.Response(503 if request.content == b'{"ps":11}' else 200, json={"success": True})

    manager, client = _manager(handler)
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3}})
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task

    assert await manager.test_preset(1, "http://wled.local", 11) is False
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert sent == [b'{"ps":3}', b'{"ps":11}', b'{"ps":3}']
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("printing", 3)
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_manual_preset_invalidates_slow_automatic_send(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def delayed_send(printer_id, base_url, preset_id, *, status=None, quiet=False):
        sent.append(preset_id)
        if preset_id == 3 and sent.count(3) == 1:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        return True

    manager = WLEDManager()
    monkeypatch.setattr(manager, "send_preset", delayed_send)
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3}})
    manager.handle_status(1, _state("RUNNING"))
    old_task = manager._runtime[1].send_task
    await asyncio.wait_for(started.wait(), 1)

    assert await manager.test_preset(1, "http://wled.local", 11) is True
    release.set()
    await old_task
    assert manager._runtime[1].status is None
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert sent == [3, 11, 3]
    assert manager._runtime[1].status == "printing"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_status_change_during_manual_send_restores_new_mapping(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def delayed_send(printer_id, base_url, preset_id, *, status=None, quiet=False):
        sent.append(preset_id)
        if preset_id == 11:
            started.set()
            await release.wait()
        return True

    manager = WLEDManager()
    monkeypatch.setattr(manager, "send_preset", delayed_send)
    manager.configure_printer(
        1,
        {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3, "paused": 7}},
    )
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task

    manual_task = asyncio.create_task(manager.test_preset(1, "http://wled.local", 11))
    await asyncio.wait_for(started.wait(), 1)
    manager.handle_status(1, _state("PAUSE"))
    await manager._runtime[1].send_task
    assert manager._runtime[1].status == "paused"

    release.set()
    assert await manual_task is True
    manager.handle_status(1, _state("PAUSE"))
    await manager._runtime[1].send_task
    assert sent == [3, 11, 7, 7]
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("paused", 7)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_offline_lifecycle_send_and_reconnect_restore_mapping():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request.content)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    manager.configure_printer(
        1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3, "offline": 9}}
    )
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    manager.handle_offline(1)
    await manager._runtime[1].send_task
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("offline", 9)
    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    manager.handle_status(1, _state("RUNNING"))
    await asyncio.sleep(0)
    assert sent == [b'{"ps":3}', b'{"ps":9}', b'{"ps":3}']
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_offline_lifecycle_skips_disabled_or_missing_preset():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request.content)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    manager.handle_offline(1)
    manager.configure_printer(1, {"enabled": False, "base_url": "http://wled.local", "presets": {"offline": 9}})
    manager.handle_offline(1)
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3}})
    manager.handle_offline(1)
    await asyncio.sleep(0)
    assert sent == []
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_offline_lifecycle_http_failure_does_not_block_and_is_retryable(caplog):
    manager, client = _manager(lambda request: httpx.Response(503, json={"error": "offline"}))
    manager.retry_seconds = 0
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": {"offline": 9}})
    manager.handle_offline(1)
    await manager._runtime[1].send_task
    assert manager._runtime[1].confirmed_generation is None
    assert "state=offline -> preset=9 failed (HTTP 503)" in caplog.text
    manager.handle_offline(1)
    await manager._runtime[1].send_task
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_late_automatic_send_cannot_confirm_over_offline(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def delayed_send(printer_id, base_url, preset_id, *, status=None, quiet=False):
        sent.append(preset_id)
        if preset_id == 3:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        return True

    manager = WLEDManager()
    monkeypatch.setattr(manager, "send_preset", delayed_send)
    manager.configure_printer(
        1, {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3, "offline": 9}}
    )
    manager.handle_status(1, _state("RUNNING"))
    old_task = manager._runtime[1].send_task
    await asyncio.wait_for(started.wait(), 1)
    manager.handle_offline(1)
    await manager._runtime[1].send_task
    release.set()
    await old_task
    assert sent == [3, 9]
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("offline", 9)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_late_old_send_cannot_confirm_over_new_state(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_send(printer_id, base_url, preset_id, *, status=None, quiet=False):
        if preset_id == 3:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        return True

    manager = WLEDManager()
    monkeypatch.setattr(manager, "send_preset", delayed_send)
    manager.configure_printer(
        1,
        {"enabled": True, "base_url": "http://wled.local", "presets": {"printing": 3, "paused": 7}},
    )
    manager.handle_status(1, _state("RUNNING"))
    old_task = manager._runtime[1].send_task
    await asyncio.wait_for(started.wait(), 1)

    manager.handle_status(1, _state("PAUSE"))
    assert old_task.cancelled() or old_task.cancelling()
    await manager._runtime[1].send_task
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("paused", 7)

    release.set()
    await old_task
    assert (manager._runtime[1].status, manager._runtime[1].preset_id) == ("paused", 7)
    await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_state", "stage", "hms", "gate", "required", "presets", "expected_state", "expected_preset"),
    [
        ("PAUSE", 6, False, False, True, {"paused": 7}, "paused", 7),
        ("PAUSE", 6, True, False, True, {"hms_error": 8, "paused": 7}, "hms_error", 8),
        ("PAUSE", 16, False, False, True, {"filament_problem": 6, "paused": 7}, "paused", 7),
        ("RUNNING", -1, True, False, True, {"printing": 3}, "printing", 3),
        ("FINISH", -1, False, True, False, {"queue_waiting": 5, "finished": 9}, "finished", 9),
        ("FINISH", -1, False, True, True, {"queue_waiting": 5, "finished": 9}, "queue_waiting", 5),
        ("FINISH", -1, False, True, True, {"finished": 9}, "finished", 9),
        ("FAILED", -1, False, True, True, {"error": 10}, "error", 10),
        ("IDLE", -1, False, True, True, {"idle": 1}, "idle", 1),
        ("FINISH", -1, False, True, True, {"idle": 1}, None, None),
        ("RUNNING", -1, False, False, True, {"idle": 1}, None, None),
    ],
)
async def test_missing_preset_falls_back_only_to_applicable_states(
    raw_state, stage, hms, gate, required, presets, expected_state, expected_preset
):
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    manager.require_plate_clear = required
    manager.configure_printer(1, {"enabled": True, "base_url": "http://wled.local", "presets": presets})
    fault = HMSError(code="fault", attr=0, module=0, severity=1, description="Nozzle fault")
    manager.handle_status(
        1,
        _state(raw_state, stg_cur=stage, hms_errors=[fault] if hms else []),
        awaiting_plate_clear=gate,
    )
    await asyncio.sleep(0)

    assert [request.content for request in sent] == (
        [f'{{"ps":{expected_preset}}}'.encode()] if expected_preset else []
    )
    assert manager._runtime[1].status == expected_state
    await manager.shutdown()
    await client.aclose()


def test_invalid_persisted_config_is_treated_as_disabled():
    manager = WLEDManager()
    manager.configure_printer(1, {"enabled": True, "base_url": None})
    assert manager._configs[1].enabled is False

    manager.configure_printer(
        1,
        {"enabled": True, "base_url": "http://169.254.169.254/latest/meta-data", "presets": {"printing": 3}},
    )
    manager.handle_status(1, _state("RUNNING"))
    assert manager._configs[1].enabled is False
    assert manager._runtime == {}
    assert manager._client is None


@pytest.mark.asyncio
async def test_finished_timeout_confirms_idle_until_new_print_cycle(caplog):
    caplog.set_level(logging.INFO, logger="backend.app.services.wled")
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    manager.configure_printer(
        1,
        {
            "enabled": True,
            "base_url": "http://wled.local",
            "presets": {"finished": 9, "idle": 1, "printing": 3},
            "finished_timeout_seconds": 10,
        },
    )
    manager.handle_status(1, _state("FINISH"))
    await manager._runtime[1].send_task
    runtime = manager._runtime[1]
    await manager._finish_timeout(1, runtime.generation, "http://wled.local", 1, 0)
    assert [request.content for request in sent] == [b'{"ps":9}', b'{"ps":1}']
    assert "[WLED] Printer 1 state=idle -> preset=1" in caplog.messages
    assert (runtime.status, runtime.preset_id) == ("idle", 1)

    manager.handle_status(1, _state("FINISH", progress=100))
    await asyncio.sleep(0)
    assert len(sent) == 2

    manager.handle_status(1, _state("IDLE"))
    await manager._runtime[1].send_task
    manager.handle_status(1, _state("FINISH"))
    await asyncio.sleep(0)
    assert [request.content for request in sent].count(b'{"ps":9}') == 1

    manager.handle_status(1, _state("RUNNING"))
    await manager._runtime[1].send_task
    assert sent[-1].content == b'{"ps":3}'
    manager.handle_status(1, _state("FINISH"))
    await manager._runtime[1].send_task
    assert [request.content for request in sent].count(b'{"ps":9}') == 2
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_finished_timeout_cancels_when_printing_resumes():
    manager, client = _manager(lambda request: httpx.Response(200, json={"success": True}))
    manager.configure_printer(
        1,
        {
            "enabled": True,
            "base_url": "http://wled.local",
            "presets": {"finished": 9, "idle": 1, "printing": 3},
            "finished_timeout_seconds": 10,
        },
    )
    manager.handle_status(1, _state("FINISH"))
    timer = manager._runtime[1].finished_task
    manager.handle_status(1, _state("RUNNING"))
    assert timer is not None and timer.cancelling()
    await manager._runtime[1].send_task
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_finished_timeout_idle_send_is_retryable():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(503 if request.content == b'{"ps":1}' and len(sent) == 2 else 200, json={"success": True})

    manager, client = _manager(handler)
    manager.retry_seconds = 0
    manager.configure_printer(
        1,
        {
            "enabled": True,
            "base_url": "http://wled.local",
            "presets": {"finished": 9, "idle": 1},
            "finished_timeout_seconds": 10,
        },
    )
    manager.handle_status(1, _state("FINISH"))
    runtime = manager._runtime[1]
    await runtime.send_task
    runtime.finished_task.cancel()
    runtime.finished_task = asyncio.create_task(
        manager._finish_timeout(1, runtime.generation, "http://wled.local", 1, 0)
    )
    await runtime.finished_task
    assert (runtime.status, runtime.preset_id) == ("finished", 9)

    manager.handle_status(1, _state("FINISH"))
    await runtime.finished_task
    assert [request.content for request in sent] == [b'{"ps":9}', b'{"ps":1}', b'{"ps":1}']
    assert (runtime.status, runtime.preset_id) == ("idle", 1)
    await manager.shutdown()
    await client.aclose()


@pytest.mark.asyncio
async def test_late_finished_timeout_idle_cannot_confirm_over_new_printing(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_send(printer_id, base_url, preset_id, *, status=None, quiet=False):
        if preset_id == 1:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        return True

    manager = WLEDManager()
    monkeypatch.setattr(manager, "send_preset", delayed_send)
    manager.configure_printer(
        1,
        {
            "enabled": True,
            "base_url": "http://wled.local",
            "presets": {"finished": 9, "idle": 1, "printing": 3},
            "finished_timeout_seconds": 10,
        },
    )
    manager.handle_status(1, _state("FINISH"))
    runtime = manager._runtime[1]
    await runtime.send_task
    runtime.finished_task.cancel()
    old_timeout = asyncio.create_task(manager._finish_timeout(1, runtime.generation, "http://wled.local", 1, 0))
    runtime.finished_task = old_timeout
    await asyncio.wait_for(started.wait(), 1)

    manager.handle_status(1, _state("RUNNING"))
    await runtime.send_task
    assert (runtime.status, runtime.preset_id) == ("printing", 3)
    release.set()
    await old_timeout
    assert (runtime.status, runtime.preset_id) == ("printing", 3)
    await manager.shutdown()
