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
    assert effective_wled_status(_state("IDLE"), awaiting_plate_clear=True) == "queue_waiting"
    assert effective_wled_status(_state("PAUSE", ams_status_main=1)) == "filament_problem"
    assert effective_wled_status(_state("PAUSE", mc_print_sub_stage=3)) == "filament_problem"
    fault = HMSError(code="0300_0008", attr=0x03000200, module=3, severity=1, description="Nozzle temperature abnormal")
    assert effective_wled_status(_state("IDLE", hms_errors=[fault])) == "hms_error"
    assert effective_wled_status(_state("RUNNING", connected=False)) == "offline"
    assert effective_wled_status(_state("UNKNOWN")) is None


@pytest.mark.parametrize(
    ("state", "awaiting_plate_clear", "expected"),
    [
        ("FINISH", True, "queue_waiting"),
        ("FAILED", True, "queue_waiting"),
        ("IDLE", True, "queue_waiting"),
        ("FINISH", False, "finished"),
        ("FAILED", False, "error"),
        ("RUNNING", True, "printing"),
        ("PRINTING", True, "printing"),
        ("PREPARE", True, "prepare"),
        ("SLICING", True, "prepare"),
        ("PAUSE", True, "paused"),
        ("UNKNOWN", True, None),
    ],
)
def test_plate_gate_precedes_only_idle_finished_and_error(state, awaiting_plate_clear, expected):
    assert effective_wled_status(_state(state), awaiting_plate_clear=awaiting_plate_clear) == expected


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
            effective_wled_status(_state(state, hms_errors=[ignored, fault]), awaiting_plate_clear=True) == "hms_error"
        )
    for values in ({"ams_status_main": 1}, {"mc_print_sub_stage": 3}):
        assert (
            effective_wled_status(_state("PAUSE", hms_errors=[fault], **values), awaiting_plate_clear=True)
            == "filament_problem"
        )
    assert (
        effective_wled_status(_state("FINISH", connected=False, hms_errors=[fault]), awaiting_plate_clear=True)
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


def test_invalid_persisted_config_is_treated_as_disabled():
    manager = WLEDManager()
    manager.configure_printer(1, {"enabled": True, "base_url": None})
    assert manager._configs[1].enabled is False


@pytest.mark.asyncio
async def test_finished_timeout_sends_idle_and_new_status_cancels_it(caplog):
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
    await asyncio.sleep(0)
    runtime = manager._runtime[1]
    await manager._finish_timeout(1, runtime.generation, "http://wled.local", 1, 0)
    assert [request.content for request in sent] == [b'{"ps":9}', b'{"ps":1}']
    assert "[WLED] Printer 1 state=idle -> preset=1" in caplog.messages

    # Further FINISH telemetry must not reactivate the finished preset.
    manager.handle_status(1, _state("FINISH", progress=100))
    await asyncio.sleep(0)
    assert len(sent) == 2

    manager.handle_status(1, _state("IDLE"))
    manager.handle_status(1, _state("FINISH"))
    timer = manager._runtime[1].finished_task
    manager.handle_status(1, _state("RUNNING"))
    await asyncio.sleep(0)
    assert timer is not None and timer.cancelled()
    assert sent[-1].content == b'{"ps":3}'
    await manager.shutdown()
    await client.aclose()
