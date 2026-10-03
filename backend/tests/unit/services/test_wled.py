import asyncio

import httpx
import pytest

from backend.app.services.bambu_mqtt import PrinterState
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
    assert effective_wled_status(_state("IDLE", hms_errors=[{"code": 1}])) == "hms_error"
    assert effective_wled_status(_state("RUNNING", connected=False)) == "offline"
    assert effective_wled_status(_state("UNKNOWN")) is None


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
async def test_send_preset_posts_expected_payload_and_handles_failures():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    manager, client = _manager(handler)
    assert await manager.send_preset(1, "http://wled.local", 3) is True
    assert requests[0].url == httpx.URL("http://wled.local/json/state")
    assert requests[0].content == b'{"ps":3}'

    for response in (httpx.Response(500), httpx.Response(200, text="bad")):
        failing, failing_client = _manager(lambda request, result=response: result)
        assert await failing.send_preset(1, "http://wled.local", 3) is False
        await failing_client.aclose()

    offline, offline_client = _manager(lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    assert await offline.send_preset(1, "http://wled.local", 3) is False
    await offline_client.aclose()
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
async def test_finished_timeout_sends_idle_and_new_status_cancels_it():
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
