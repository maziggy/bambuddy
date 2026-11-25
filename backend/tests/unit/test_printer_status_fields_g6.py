"""Printer status fields the card reads, on both the REST status and the WebSocket.

- Nozzles, firmware version and developer mode were REST-only, so the card
  showed them up to 30 s late (the fallback poll) after a change.
- The print's AMS mapping (the "P1 / P2" slot pills, #1762) was read from the
  printer's push, which almost never carries it. Bambuddy captures it from the
  print command; that capture is what the status reports now.
- The runout hint on a printer with several AMS said "check the printer" unless
  the snow mapping named the slot. The AMS that feeds the active nozzle names it.
- AMS-HT was told apart by reporting one tray; it is the unit id 128-135.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app import main as main_module
from backend.app.services.bambu_mqtt import BambuMQTTClient, NozzleInfo, PrinterState
from backend.app.services.printer_manager import ams_units_to_dicts, printer_state_to_dict, resolve_expected_tray


def _state(**fields) -> PrinterState:
    state = PrinterState()
    state.connected = True
    state.state = "IDLE"
    for name, value in fields.items():
        setattr(state, name, value)
    return state


class TestTheWebSocketCarriesThem:
    def test_nozzles_firmware_and_developer_mode(self):
        state = _state(
            nozzles=[NozzleInfo(nozzle_type="hardened_steel", nozzle_diameter="0.4")],
            firmware_version="01.09.00.00",
            developer_mode=True,
        )

        pushed = printer_state_to_dict(state)

        assert pushed["nozzles"] == [{"nozzle_type": "hardened_steel", "nozzle_diameter": "0.4"}]
        assert pushed["firmware_version"] == "01.09.00.00"
        assert pushed["developer_mode"] is True


def _push_state(**overrides):
    fields = {
        "connected": True,
        "state": "IDLE",
        "progress": 0,
        "layer_num": 0,
        "total_layers": 0,
        "temperatures": {},
        "nozzles": [],
        "raw_data": {},
        "stg_cur": 0,
        "fila_switch": None,
        "ams_switch_inlet": {},
        "extruder_slots": {},
        "cooling_fan_speed": 0,
        "big_fan1_speed": 0,
        "big_fan2_speed": 0,
        "chamber_light": "",
        "active_extruder": 0,
        "tray_now": 255,
        "door_open": False,
        "subtask_name": "",
        "gcode_file": "",
        "remaining_time": None,
        "hms_errors": [],
        "ams_filament_backup": None,
        "firmware_version": "01.09.00.00",
        "developer_mode": False,
        "print_ams_mapping": None,
        "airduct_mode": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def ws_mgr():
    main_module._last_status_broadcast.clear()
    main_module._printer_last_connected.clear()
    main_module._printer_reconciled_since_connect.clear()
    mgr = MagicMock()
    mgr.send_printer_status = AsyncMock()
    yield mgr
    main_module._last_status_broadcast.clear()
    main_module._printer_last_connected.clear()
    main_module._printer_reconciled_since_connect.clear()


async def _push(ws_mgr, state) -> None:
    relay = MagicMock()
    relay.on_printer_status = AsyncMock()
    pm = MagicMock()
    pm.get_printer.return_value = None
    pm.get_model.return_value = ""
    with (
        patch("backend.app.main.ws_manager", ws_mgr),
        patch("backend.app.main.mqtt_relay", relay),
        patch("backend.app.main.printer_manager", pm),
        patch("backend.app.main.spawn_background_task", side_effect=lambda coro, **kwargs: coro.close()),
        patch("backend.app.main.printer_state_to_dict", return_value={}),
    ):
        await main_module.on_printer_status_change(1, state)


class TestAChangeAloneIsPushed:
    """An idle printer's other fields stand still, so the change has to move
    the broadcast's dedup key by itself."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "change",
        [
            {"developer_mode": True},
            {"firmware_version": "01.10.00.00"},
            # Cooling/heating switched on the printer's screen (PSF-4).
            {"airduct_mode": 1},
            {"nozzles": [NozzleInfo(nozzle_type="stainless_steel", nozzle_diameter="0.6")]},
            {"print_ams_mapping": [0, 2]},
        ],
    )
    async def test_it_breaks_the_dedup(self, ws_mgr, change):
        await _push(ws_mgr, _push_state())
        await _push(ws_mgr, _push_state(**change))

        assert ws_mgr.send_printer_status.await_count == 2


def _client() -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.9", serial_number="SN-G6", access_code="x", model="X1C")


class TestThePrintsMapping:
    def test_a_captured_mapping_is_in_the_status(self):
        client = _client()
        client._handle_request_message(
            {"print": {"command": "project_file", "url": "ftp://x.3mf", "ams_mapping": [2, 0, -1]}}
        )

        assert client.state.print_ams_mapping == [2, 0, -1]
        client.state.state = "RUNNING"
        assert printer_state_to_dict(client.state)["ams_mapping"] == [2, 0, -1]

    def test_it_is_gone_with_the_print(self):
        client = _client()
        client._captured_ams_mapping = [1]
        client._captured_ams_mapping = None

        assert client.state.print_ams_mapping is None
        assert printer_state_to_dict(client.state)["ams_mapping"] == []


class TestAMappingLeftFromAPrintThatNeverStarted:
    """A dispatch the printer refused or that was cancelled before it ran ends
    no print, so its captured mapping stayed and the next print from the
    printer's screen showed it."""

    def test_the_next_prints_own_mapping_replaces_it(self):
        client = _client()
        client._handle_request_message(
            {"print": {"command": "project_file", "url": "ftp://old.3mf", "ams_mapping": [3]}}
        )

        client._capture_report_project_file(
            {
                "command": "project_file",
                "result": "SUCCESS",
                "url": "file:///userdata/model/history/new.gcode.3mf",
                "ams_mapping": [1],
            }
        )

        assert client.state.print_ams_mapping == [1]

    def test_the_echo_of_the_same_dispatch_keeps_the_slicers_copy(self):
        client = _client()
        client._handle_request_message(
            {"print": {"command": "project_file", "url": "ftp://job.3mf", "ams_mapping": [3]}}
        )

        client._capture_report_project_file(
            {"command": "project_file", "result": "SUCCESS", "url": "ftp://job.3mf", "ams_mapping": [0]}
        )

        assert client.state.print_ams_mapping == [3]


class TestTheRunoutHintOnSeveralAms:
    """Two regular AMS, one per nozzle (H2D); no snow mapping in the push."""

    LAYOUT = [(0, False), (1, False)]
    NOZZLES = {"0": 0, "1": 1}

    def test_the_active_nozzles_ams_names_the_slot(self):
        assert resolve_expected_tray(2, self.LAYOUT, None, active_extruder=1, ams_extruder_map=self.NOZZLES) == 6

    def test_it_narrows_an_ambiguous_mapping(self):
        mapping = [0 * 256 + 2, 1 * 256 + 2]

        assert resolve_expected_tray(2, self.LAYOUT, mapping, active_extruder=0, ams_extruder_map=self.NOZZLES) == 2

    def test_the_mapping_picks_among_the_nozzles_ams(self):
        """Two AMS on the active nozzle: the mapping's entry for one of them
        decides, its entry on the other nozzle's AMS does not count."""
        layout = [(0, False), (1, False), (2, False)]
        nozzles = {"0": 0, "1": 0, "2": 1}
        mapping = [0 * 256 + 2, 2 * 256 + 2]

        assert resolve_expected_tray(2, layout, mapping, active_extruder=0, ams_extruder_map=nozzles) == 2

    def test_an_ams_ht_on_that_nozzle_counts_too(self):
        """Slot 0 can be the AMS-HT's one tray as well."""
        layout = [(0, False), (1, False), (128, True)]
        nozzles = {"0": 1, "1": 0, "128": 0}
        mapping = [1 * 256 + 0, 128 * 256 + 0]

        assert resolve_expected_tray(0, layout, mapping, active_extruder=0, ams_extruder_map=nozzles) is None
        assert resolve_expected_tray(0, layout, None, active_extruder=0, ams_extruder_map=nozzles) is None
        assert resolve_expected_tray(1, layout, None, active_extruder=0, ams_extruder_map=nozzles) == 5

    def test_two_ams_on_that_nozzle_still_ask(self):
        layout = [(0, False), (1, False), (2, False)]
        nozzles = {"0": 0, "1": 0, "2": 1}

        assert resolve_expected_tray(2, layout, None, active_extruder=0, ams_extruder_map=nozzles) is None

    def test_an_ams_on_the_track_switch_is_not_guessed(self):
        """An AMS on the Filament Track Switch has no nozzle entry: it can feed either."""
        assert resolve_expected_tray(2, self.LAYOUT, None, active_extruder=1, ams_extruder_map={"1": 1}) is None

    def test_without_a_nozzle_nothing_changes(self):
        assert resolve_expected_tray(2, self.LAYOUT, None) is None

    def test_a_fitted_track_switch_is_not_guessed_either(self):
        """The map keeps an AMS's old wiring after it moves onto the switch."""
        from backend.app.services.bambu_mqtt import FilaSwitchState

        state = _state(
            state="PAUSE",
            tray_tar=2,
            active_extruder=1,
            ams_extruder_map=dict(self.NOZZLES),
            fila_switch=FilaSwitchState(installed=True),
            raw_data={"ams": [{"id": "0", "tray": [{"id": str(i)} for i in range(4)]}, {"id": "1", "tray": []}]},
        )

        assert printer_state_to_dict(state)["expected_tray"] is None

    def test_rest_and_websocket_pass_the_nozzle(self):
        state = _state(
            state="PAUSE",
            tray_tar=2,
            active_extruder=1,
            ams_extruder_map=dict(self.NOZZLES),
            raw_data={"ams": [{"id": "0", "tray": [{"id": str(i)} for i in range(4)]}, {"id": "1", "tray": []}]},
        )

        assert printer_state_to_dict(state)["expected_tray"] == 6


class TestAmsHtIsItsUnitId:
    def test_a_regular_ams_reporting_one_tray_is_not_ht(self):
        units = ams_units_to_dicts([{"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}]}], lambda *a: None, None)

        assert units[0]["is_ams_ht"] is False

    def test_an_ht_unit_is_ht(self):
        units = ams_units_to_dicts([{"id": "128", "tray": [{"id": "0", "tray_type": "PLA"}]}], lambda *a: None, None)

        assert units[0]["is_ams_ht"] is True

    def test_the_scheduler_reads_the_unit_id(self):
        from backend.app.services.print_scheduler import PrintScheduler

        status = SimpleNamespace(
            raw_data={"ams": [{"id": "1", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"}]}]},
            nozzles=[],
        )
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = status
            pm.get_model.return_value = "X1C"
            filaments = PrintScheduler()._build_loaded_filaments(status)

        assert [(f["global_tray_id"], f["is_ht"]) for f in filaments] == [(4, False)]


@pytest.mark.asyncio
async def test_the_rest_status_reports_them_too(async_client, printer_factory):
    printer = await printer_factory()
    state = _state(
        state="PAUSE",
        tray_tar=2,
        active_extruder=1,
        ams_extruder_map={"0": 0, "1": 1},
        print_ams_mapping=[6, -1],
        raw_data={"ams": [{"id": "0", "tray": [{"id": str(i)} for i in range(4)]}, {"id": "1", "tray": []}]},
    )
    with patch("backend.app.api.routes.printers.printer_manager") as pm:
        pm.get_status = MagicMock(return_value=state)
        pm.is_awaiting_plate_clear = MagicMock(return_value=False)
        pm.get_drying_targets = MagicMock(return_value={})
        response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

    body = response.json()
    assert (body["expected_tray"], body["ams_mapping"]) == (6, [6, -1])
    assert [unit["is_ams_ht"] for unit in body["ams"]] == [False, False]
