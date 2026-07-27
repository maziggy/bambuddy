"""A2L "AMS Lite" unit-id normalisation (memory a2l-am-unit-16).

The A2L reports its 4-slot AMS Lite as physical unit id 16, but its tray
bitmasks sit at bit base 24 (= id 6) and it reports tray_now as a local 0-3
slot. We normalise 16 -> 6 at the MQTT ingest boundary so global tray ids land
at 24-27 and every ams_id*4+slot consumer works unchanged, and translate back to
the physical id 16 only on the outbound wire.

Field values here mirror the confirmed capture (2026-07-20): physical slots 1
empty, 2 & 3 loaded, 4 empty; tray_exist_bits "6000000"; tray_now "2" while
printing physical slot 3.
"""

import json
from unittest.mock import MagicMock

from backend.app.services.bambu_mqtt import (
    A2L_LITE_GLOBAL_BASE,
    A2L_LITE_NORMALIZED_AMS_ID,
    A2L_LITE_PHYSICAL_AMS_ID,
    BambuMQTTClient,
    a2l_lite_wire_ids,
    normalize_am_unit_id,
)


def _client(model: str = "A2L") -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="A2L", access_code="c", model=model)


def _wired(client: BambuMQTTClient) -> BambuMQTTClient:
    client._client = MagicMock()
    client.state.connected = True
    return client


def _capture_frame() -> dict:
    """One push_status frame matching Mike's 2026-07-20 capture."""
    return {
        "ams": [
            {
                "id": 16,
                "tray": [
                    {"id": 0},
                    {
                        "id": 1,
                        "state": 3,
                        "remain": 100,
                        "tray_type": "",
                        "tray_info_idx": "",
                        "tray_color": "FFFFFF00",
                    },
                    {
                        "id": 2,
                        "state": 3,
                        "remain": 100,
                        "tray_type": "",
                        "tray_info_idx": "",
                        "tray_color": "FFFFFF00",
                    },
                    {"id": 3},
                ],
            }
        ],
        "ams_exist_bits": "1000",
        "tray_exist_bits": "6000000",
        "tray_now": "2",
        "tray_pre": "2",
        "tray_tar": "2",
    }


def _last_payload(client: BambuMQTTClient) -> dict:
    return json.loads(client._client.publish.call_args[0][1])["print"]


class TestHelpers:
    def test_normalize_touches_only_16(self):
        assert normalize_am_unit_id(A2L_LITE_PHYSICAL_AMS_ID) == A2L_LITE_NORMALIZED_AMS_ID
        for other in (0, 1, 2, 3, 6, 15, 128, 135, 254, 255):
            assert normalize_am_unit_id(other) == other

    def test_wire_ids_only_for_normalised_6(self):
        # (physical ams id, local slot, physical global tray)
        assert a2l_lite_wire_ids(6, 2) == (16, 2, 66)
        assert a2l_lite_wire_ids(6, 0) == (16, 0, 64)
        # tray_id is taken modulo 4, so a global tray works too.
        assert a2l_lite_wire_ids(6, 26) == (16, 2, 66)
        # Any other unit id is left alone (returns None).
        for ams in (0, 3, 16, 128, 255):
            assert a2l_lite_wire_ids(ams, 2) is None


class TestIngestNormalisation:
    def test_unit_id_16_normalised_to_6(self):
        client = _client()
        client._handle_ams_data(_capture_frame())
        assert client.state.raw_data["ams"][0]["id"] == A2L_LITE_NORMALIZED_AMS_ID
        assert client._has_a2l_am_unit is True

    def test_exists_annotation_uses_bit_base_24(self):
        # tray_exist_bits "6000000" = bits 25,26 -> global_bit 24+slot -> slots 1,2.
        client = _client()
        client._handle_ams_data(_capture_frame())
        trays = {t["id"]: t for t in client.state.raw_data["ams"][0]["tray"]}
        assert trays[1]["exists"] is True
        assert trays[2]["exists"] is True
        assert trays[0]["exists"] is False
        assert trays[3]["exists"] is False

    def test_regular_ams_untouched(self):
        client = _client(model="X1C")
        frame = {
            "ams": [{"id": 0, "tray": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}]}],
            "tray_exist_bits": "3",
            "tray_now": "1",
        }
        client._handle_ams_data(frame)
        assert client.state.raw_data["ams"][0]["id"] == 0
        assert client._has_a2l_am_unit is False
        assert client.state.tray_now == 1  # regular AMS 0 slot 1 == global 1

    def test_bare_list_ams_shape_is_also_normalised(self):
        # Some firmware/shapes deliver the unit list directly (no dict wrapper).
        client = _client()
        client._handle_ams_data([{"id": 16, "tray": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}]}])
        assert client.state.raw_data["ams"][0]["id"] == A2L_LITE_NORMALIZED_AMS_ID
        assert client._has_a2l_am_unit is True


class TestTrayNowGlobalisation:
    def test_local_tray_now_globalised_to_24_plus_slot(self):
        client = _client()
        client._handle_ams_data(_capture_frame())
        # local slot 2 -> global 26 (24 + 2)
        assert client.state.tray_now == A2L_LITE_GLOBAL_BASE + 2 == 26

    def test_globalised_tray_passes_last_valid_guard(self):
        # last_loaded_tray is only written when the valid-tray guard accepts tn.
        client = _client()
        client._handle_ams_data(_capture_frame())
        assert client.state.last_loaded_tray == 26


class TestOutboundTranslation:
    def test_set_filament_setting_uses_physical_16_local_slot(self):
        client = _wired(_client())
        assert client.ams_set_filament_setting(
            ams_id=6,
            tray_id=2,
            tray_info_idx="GFL05",
            tray_type="PLA",
            tray_sub_brands="PLA Basic",
            tray_color="FF0000FF",
            nozzle_temp_min=190,
            nozzle_temp_max=230,
        )
        p = _last_payload(client)
        assert p["ams_id"] == A2L_LITE_PHYSICAL_AMS_ID
        assert p["tray_id"] == 2
        assert p["slot_id"] == 2

    def test_reset_slot_uses_physical_16_local_slot(self):
        client = _wired(_client())
        assert client.reset_ams_slot(ams_id=6, tray_id=3)
        p = _last_payload(client)
        assert p["ams_id"] == A2L_LITE_PHYSICAL_AMS_ID
        assert p["tray_id"] == 3
        assert p["slot_id"] == 3

    def test_cali_sel_uses_physical_global_tray(self):
        client = _wired(_client())
        assert client.extrusion_cali_sel(ams_id=6, tray_id=2, cali_idx=1, filament_id="GFL05")
        p = _last_payload(client)
        assert p["ams_id"] == A2L_LITE_PHYSICAL_AMS_ID
        assert p["tray_id"] == 66  # 16*4 + 2 (extrapolated physical global)
        assert p["slot_id"] == 2

    def test_cali_set_remaps_global_tray(self):
        client = _wired(_client())
        assert client.extrusion_cali_set(tray_id=26, k_value=0.02, filament_id="GFL05")
        p = _last_payload(client)
        assert p["filaments"][0]["tray_id"] == 66  # 26 (normalised) -> 66 (physical)

    def test_load_filament_target_and_ams(self):
        client = _wired(_client())
        assert client.ams_load_filament(tray_id=26)
        p = _last_payload(client)
        assert p["ams_id"] == A2L_LITE_PHYSICAL_AMS_ID
        assert p["slot_id"] == 2
        assert p["target"] == 66

    def test_unload_uses_physical_ams(self):
        client = _wired(_client())
        client.state.tray_now = 26
        assert client.ams_unload_filament()
        assert _last_payload(client)["ams_id"] == A2L_LITE_PHYSICAL_AMS_ID

    def test_refresh_tray_uses_physical_16(self):
        client = _wired(_client())
        client.state.tray_now = 255  # nothing loaded, so refresh is allowed
        ok, _ = client.ams_refresh_tray(ams_id=6, tray_id=2)
        assert ok
        p = _last_payload(client)
        assert p["ams_id"] == A2L_LITE_PHYSICAL_AMS_ID
        assert p["slot_id"] == 2

    def test_drying_uses_physical_16(self):
        client = _wired(_client())
        assert client.send_drying_command(ams_id=6, temp=55, duration=4, mode=1, filament="PLA")
        assert _last_payload(client)["ams_id"] == A2L_LITE_PHYSICAL_AMS_ID

    def test_regular_ams_command_unchanged(self):
        client = _wired(_client(model="X1C"))
        assert client.ams_set_filament_setting(
            ams_id=0,
            tray_id=2,
            tray_info_idx="GFL05",
            tray_type="PLA",
            tray_sub_brands="PLA Basic",
            tray_color="FF0000FF",
            nozzle_temp_min=190,
            nozzle_temp_max=230,
        )
        p = _last_payload(client)
        assert p["ams_id"] == 0
        assert p["tray_id"] == 2
