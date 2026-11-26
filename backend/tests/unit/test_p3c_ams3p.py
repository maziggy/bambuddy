"""P3C "AMS 3P" (model N11, #3737): one seven-slot rack AMS, numbered as Bambu Studio does.

Bambu Studio's printers/N11.json is a copy of the H3C's N10.json with a
seven-dock rack: the P3C reports its AMS 3P as ONE unit (id 33, AMS type 7 =
Studio's ZMULTI_B) with trays 0-6 and seven external inputs, vir_slot 249-255.
Its slots get Bambu Studio's global tray ids 37-43 (utils.tray_ids), and
``mapping`` / the extruder ``snow`` encode slot k as (33 << 8) | k.

``FRAME`` is trimmed from the reporter's support package (firmware
01.01.00.00, mid-print): slots 0-4 loaded, 5 and 6 empty, five PrintTools in
the rack (ids 16-20), the one of slot 4 on the toolhead (its snow 8452 =
(33 << 8) | 4). The capture carries no tray_now, so the tests use that slot.
Tags and serials are zeroed.
"""

import copy
import json
from unittest.mock import MagicMock

from backend.app.services import mqtt_ha_discovery as ha
from backend.app.services.bambu_mqtt import BambuMQTTClient, resolve_external_spools_in_mapping
from backend.app.services.printer_manager import ams_units_to_dicts, supports_chamber_heater, supports_chamber_temp
from backend.app.utils.tray_ids import (
    AmsType,
    TrayLayout,
    decode_wire_slot,
    rack_slot_count,
    unit_ams_type,
    unit_label,
)

_COLOURS = ["00AE42FF", "5E43B7FF", "F4EE2AFF", "FFFFFFFF", "BB3D43FF"]


def _tray(slot: int) -> dict:
    if slot >= len(_COLOURS):
        return {"id": str(slot), "state": 27, "tray_type": "", "remain": -1, "tag_uid": "0000000000000000"}
    return {
        "id": str(slot),
        "state": 283,
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Basic",
        "tray_color": _COLOURS[slot],
        "tray_info_idx": "GFA00",
        "remain": 93,
        "tag_uid": "0000000000000000",
        "tray_uuid": "00000000000000000000000000000000",
    }


FRAME = {
    "command": "push_status",
    "ams": {
        "tray_now": "4",
        "ams": [
            {
                "id": "33",
                "info": "140001007",
                "humidity_raw": "27",
                "temp": "33.0",
                "tray": [_tray(slot) for slot in range(7)],
                "zone_info": [
                    {
                        "zone_id": 0,
                        "zone_slots": [0, 1, 2, 3, 4, 5, 6],
                        "humidity_raw": "27",
                        "temp": "33.0",
                        "info": "140001007",
                        "dry_time": 0,
                        "dry_sf_reason": [8],
                    }
                ],
            }
        ],
    },
    "vt_tray": [{"id": str(vir), "tray_type": "", "remain": -1} for vir in range(249, 256)],
    "mapping": [8452, 8450, 8449, 8452, 8451, 8448, 8450],
}


def _client(model: str = "P3C") -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="P3C", access_code="c", model=model)


def _ingested(model: str = "P3C", frame: dict | None = None) -> BambuMQTTClient:
    client = _client(model)
    client._process_message({"print": copy.deepcopy(frame if frame is not None else FRAME)})
    return client


def _unit(client: BambuMQTTClient) -> dict:
    (unit,) = client.state.raw_data["ams"]
    return unit


class TestModel:
    def test_the_codes_name_the_p3c(self):
        from backend.app.api.routes.slicer_presets import list_printer_models
        from backend.app.services.virtual_printer.manager import MODEL_SERIAL_PREFIXES, VIRTUAL_PRINTER_MODELS
        from backend.app.services.virtual_printer.mqtt_server import MODEL_PRODUCT_NAMES
        from backend.app.utils.printer_models import normalize_printer_model, normalize_printer_model_id

        assert normalize_printer_model_id("N11") == "P3C"
        assert normalize_printer_model("Bambu Lab P3C") == "P3C"
        # The known models presets are matched against, and the slice dialog's table.
        assert list_printer_models()["Bambu Lab P3C"] == "P3C"
        assert VIRTUAL_PRINTER_MODELS["N11"] == MODEL_PRODUCT_NAMES["N11"] == "P3C"
        # Studio's sn_prefix for the N11, and the reporter's first five characters.
        assert MODEL_SERIAL_PREFIXES["N11"].startswith("33189")

    def test_it_has_steel_rods_and_its_progress_counts_the_start(self):
        from backend.app.utils.printer_models import get_rod_type, usage_progress

        assert get_rod_type("P3C") == get_rod_type("N11") == "steel_rod"
        # Captured at 43% on layer 73 of 240.
        assert round(usage_progress("P3C", 43, 73, 240), 1) == 30.4

    def test_hardware_flags(self):
        from backend.app.services.camera import supports_rtsp
        from backend.app.services.printer_manager import supports_airduct
        from backend.app.utils.printer_models import model_rack_ams, uses_exhaust_fan_label

        for model in ("P3C", "N11"):
            assert supports_rtsp(model)
            assert supports_chamber_temp(model)
            # support_chamber_temp_edit is false in N11.json: a sensor, no heater.
            assert not supports_chamber_heater(model)
            assert supports_airduct(model)
            assert uses_exhaust_fan_label(model)
            assert model_rack_ams(model) == AmsType.ZMULTI_B
        assert model_rack_ams("H3C") == AmsType.ZMULTI_A
        assert model_rack_ams("X1C") is None

    def test_firmware_comes_from_its_own_wiki_page(self):
        from backend.app.services.firmware_check import API_KEY_TO_DEV_MODEL, API_KEY_TO_WIKI_PATH, MODEL_TO_API_KEY

        assert MODEL_TO_API_KEY["P3C"] == MODEL_TO_API_KEY["N11"] == "p3c"
        assert API_KEY_TO_DEV_MODEL["p3c"] == "N11"
        assert API_KEY_TO_WIKI_PATH["p3c"] == "/en/p3c/manual/firmware-release-history"

    def test_maintenance_offers_the_steel_rod_tasks(self):
        from backend.app.api.routes.maintenance import _should_apply_to_printer

        assert _should_apply_to_printer("Lubricate Steel Rods", "P3C")
        assert not _should_apply_to_printer("Lubricate Linear Rails", "P3C")
        assert not _should_apply_to_printer("Clean Carbon Rods", "N11")


class TestLayout:
    def test_unit_33_is_the_ams3p_with_trays_37_to_43(self):
        layout = _ingested().tray_layout
        assert layout.rack_units == ((33, AmsType.ZMULTI_B),)
        assert rack_slot_count(AmsType.ZMULTI_B) == 7
        assert [layout.global_tray_id(33, k) for k in range(7)] == list(range(37, 44))
        assert layout.slot_key(43) == (33, 6)
        assert layout.rack_slot(44) is None
        assert unit_label(33, AmsType.ZMULTI_B) == "AMS 3P"

    def test_its_seven_external_inputs_are_249_to_255(self):
        layout = _ingested().tray_layout
        assert layout.rack
        for vir in range(249, 256):
            assert layout.is_external(vir)
        assert layout.slot_key(249) == (255, 4)
        assert layout.tray_from_slot_key(255, 4) == 249

    def test_tray_now_is_the_global_tray_37_plus_slot(self):
        assert _ingested().state.tray_now == 41
        frame = copy.deepcopy(FRAME)
        frame["ams"]["tray_now"] = "6"
        assert _ingested(frame=frame).state.tray_now == 43

    def test_only_its_own_seven_slots_are_rack_slots(self):
        layout = _ingested().tray_layout
        assert layout.raw_slot_to_global(6) == 43
        assert layout.raw_slot_to_global(7) is None
        assert decode_wire_slot((33 << 8) | 6, layout) == 43
        assert decode_wire_slot((33 << 8) | 7, layout) is None

    def test_the_printers_mapping_decodes_to_its_slots(self):
        layout = _ingested().tray_layout
        assert [decode_wire_slot(v, layout) for v in FRAME["mapping"]] == [41, 39, 38, 41, 40, 37, 39]

    def test_without_ams_data_the_model_gives_its_layout(self):
        assert TrayLayout.from_state(None, AmsType.ZMULTI_B) == TrayLayout.rack_printer(AmsType.ZMULTI_B)
        assert TrayLayout.rack_printer(AmsType.ZMULTI_B).slot_key(40) == (33, 3)
        assert _client().tray_layout.rack_units == ((33, AmsType.ZMULTI_B),)

    def test_a_stored_ams3p_slot_keeps_its_key_without_live_data(self):
        assert TrayLayout().slot_key(37) == (33, 0)
        assert TrayLayout().slot_key(43) == (33, 6)
        assert unit_ams_type(33) == AmsType.ZMULTI_B

    def test_captured_mapping2_slot_becomes_the_global_tray(self):
        detail = [{"ams_id": 33, "slot_id": 4}, {"ams_id": 33, "slot_id": 7}]
        resolved = resolve_external_spools_in_mapping([41, -1], detail, False, _ingested().tray_layout)
        # Slot 7 is not an AMS 3P slot, so it is left as captured.
        assert resolved == [41, -1]


class TestStatusAndVersion:
    def test_status_unit_carries_its_ams_type(self):
        (unit,) = ams_units_to_dicts(_ingested().state.raw_data["ams"], lambda *a: None)
        assert (unit["id"], unit["ams_type"], len(unit["tray"])) == (33, 7, 7)
        assert unit["is_ams_ht"] is False

    def test_version_module_n11a_reaches_the_unit(self):
        client = _ingested()
        client._process_message(
            {"info": {"command": "get_version", "module": [{"name": "n11a", "sw_ver": "00.00.05.10", "sn": "SN3P"}]}}
        )
        unit = _unit(client)
        assert (unit["sw_ver"], unit["module_type"], unit["sn"]) == ("00.00.05.10", "n11a", "SN3P")

    def test_filament_setting_targets_unit_33(self):
        client = _ingested()
        client._client = MagicMock()
        client.state.connected = True
        assert client.ams_set_filament_setting(
            ams_id=33,
            tray_id=6,
            tray_info_idx="GFA00",
            tray_type="PLA",
            tray_sub_brands="PLA Basic",
            tray_color="FFFFFFFF",
            nozzle_temp_min=190,
            nozzle_temp_max=230,
        )
        sent = json.loads(client._client.publish.call_args[0][1])["print"]
        assert (sent["ams_id"], sent["tray_id"], sent["slot_id"]) == (33, 6, 6)

    def test_the_ams3p_cannot_be_loaded(self):
        """Bambu: the AMS 3P has no automatic feeding; only an AMS 3H slot loads."""
        client = _ingested()
        client._client = MagicMock()
        client.state.connected = True
        assert client.ams_load_filament(41) is False
        client._client.publish.assert_not_called()


class TestHomeAssistantDiscovery:
    def test_the_printer_has_a_door_entity(self):
        config = ha.build_printer_config(
            topic_prefix="bambuddy",
            serial="P3C",
            name="P3C",
            model="P3C",
            firmware_version="01.01.00.00",
            temperatures=None,
            vt_tray=None,
        )
        assert "door" in config["components"]

    def test_the_ams3p_device(self):
        (unit,) = ams_units_to_dicts(_ingested().state.raw_data["ams"], lambda *a: None)
        assert ha.ams_model(unit) == "AMS 3P"
        assert ha.ams_unit_name(unit) == "AMS 3P"
        config = ha.build_ams_config(topic_prefix="bambuddy", serial="P3C", printer_name="P3C", unit=unit)
        assert config["device"]["name"] == "P3C AMS 3P"
        assert sorted(k for k in config["components"] if k.startswith("slot_")) == [f"slot_{n}" for n in range(1, 8)]
