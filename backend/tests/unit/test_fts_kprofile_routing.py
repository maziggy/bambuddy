"""K-profiles follow an AMS when it moves between Filament Track Switch inlets.

Measured on the maintainer's H2C, 2026-08-16. Spool 75, "HF Bambu PLA Matte
Black", is calibrated on both hotends and Bambuddy stores both:

    extruder 1 (left)   K 0.018   cali_idx 16
    extruder 0 (right)  K 0.020   cali_idx 15

A tray holds exactly one ``cali_idx``, and the printer's calibration table is
numbered per nozzle — so index 16 exists on both hotends and means a different
profile on each. Moving that AMS from In-A to In-B therefore leaves the slot
bound to the *left* profile while it now feeds the right nozzle. The printer
does not re-resolve it, and an RFID re-read only re-asserts the same wrong
index. That is the state the live printer was in when this was written:

    AMS 1  info=10002E03  inlet=IN-B   tray 0: PLA 000000  cali_idx=16
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.utils.fts_routing import FTS_INLET_EXTRUDER, extruder_for_inlet, slot_extruder


class TestInletToNozzle:
    def test_in_a_is_the_left_hotend(self):
        assert extruder_for_inlet("A") == 1

    def test_in_b_is_the_right_hotend(self):
        assert extruder_for_inlet("B") == 0

    @pytest.mark.parametrize("value", [None, "", "C", "left"])
    def test_anything_else_is_unknown(self, value):
        assert extruder_for_inlet(value) is None

    def test_the_two_inlets_do_not_share_a_nozzle(self):
        """A table that mapped both inlets to one hotend would silently bind
        every slot to the same K-profile."""
        assert sorted(FTS_INLET_EXTRUDER.values()) == [0, 1]


class TestSlotExtruder:
    def test_a_real_extruder_id_wins(self):
        """A non-0xE binding is authoritative even on a machine with a switch,
        which is how BambuStudio treats it too."""
        assert slot_extruder(2, 0, {"2": 1}, {"2": "B"}) == 1

    def test_falls_back_to_the_switch_inlet(self):
        """The FTS case: every AMS reports 0xE, so the map is empty."""
        assert slot_extruder(1, 0, {}, {"1": "B"}) == 0
        assert slot_extruder(2, 3, {}, {"2": "A"}) == 1

    def test_unknown_is_none_not_zero(self):
        """The bug this replaced: three call sites ended in `else 0`, which on a
        switch machine filed every profile under the right-hand nozzle."""
        assert slot_extruder(1, 0, {}, {}) is None
        assert slot_extruder(1, 0, None, None) is None

    def test_external_slots_name_their_own_side(self):
        assert slot_extruder(255, 0, {}, {}) == 1  # Ext-L
        assert slot_extruder(255, 1, {}, {}) == 0  # Ext-R

    def test_an_unmapped_ams_does_not_borrow_another_units_inlet(self):
        assert slot_extruder(3, 0, {}, {"1": "A", "2": "B"}) is None

    def test_the_maintainers_h2c(self):
        """The live layout: AMS 0/1/128 on In-B, AMS 2 on In-A, empty map."""
        inlets = {"0": "B", "1": "B", "2": "A", "128": "B"}
        assert slot_extruder(0, 0, {}, inlets) == 0
        assert slot_extruder(1, 0, {}, inlets) == 0
        assert slot_extruder(2, 1, {}, inlets) == 1
        assert slot_extruder(128, 0, {}, inlets) == 0


def _profile(cali_idx: int, extruder: int, k: float):
    return MagicMock(
        cali_idx=cali_idx, extruder=extruder, k_value=k, name="HF Bambu PLA Matte Black", filament_id="GFA01"
    )


class TestReSelectOnInletMove:
    """The callback that re-points a moved AMS's slots."""

    @staticmethod
    def _run(inlet, tray, profile, *, connected=True):
        """Drive on_fts_inlet_change for one AMS holding one tray."""
        from backend.app import main as main_module

        client = MagicMock()
        client.extrusion_cali_sel = MagicMock(return_value=True)
        state = MagicMock()
        state.raw_data = {"ams": [{"id": "1", "tray": [tray]}]}
        state.nozzles = [MagicMock(nozzle_diameter="0.4")]

        pm = MagicMock()
        pm.get_client.return_value = client if connected else None
        pm.get_status.return_value = state

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock()

        with (
            patch("backend.app.main.printer_manager", pm),
            patch("backend.app.main.async_session", return_value=session),
            patch(
                "backend.app.main.find_slot_kprofile_for_extruder",
                new=AsyncMock(return_value=profile),
            ) as lookup,
        ):
            import asyncio

            asyncio.run(main_module.on_fts_inlet_change(7, 1, inlet))
        return client, lookup

    def test_moving_to_in_b_selects_the_right_hotends_profile(self):
        """The reported case, with the maintainer's real numbers."""
        tray = {"id": "0", "tray_type": "PLA", "cali_idx": 16, "tray_info_idx": "GFA01"}
        client, lookup = self._run("B", tray, _profile(15, 0, 0.020))

        assert lookup.await_args.args[4] == 0, "must look up the RIGHT hotend's profile"
        client.extrusion_cali_sel.assert_called_once()
        assert client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 15

    def test_moving_to_in_a_selects_the_left_hotends_profile(self):
        tray = {"id": "0", "tray_type": "PLA", "cali_idx": 15, "tray_info_idx": "GFA01"}
        client, lookup = self._run("A", tray, _profile(16, 1, 0.018))

        assert lookup.await_args.args[4] == 1
        assert client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 16

    def test_an_already_correct_slot_is_left_alone(self):
        """No point re-sending a binding the printer already holds, and every
        avoided write is one less chance of the firmware mislinking it."""
        tray = {"id": "0", "tray_type": "PLA", "cali_idx": 15, "tray_info_idx": "GFA01"}
        client, _ = self._run("B", tray, _profile(15, 0, 0.020))

        client.extrusion_cali_sel.assert_not_called()

    def test_a_spool_with_no_profile_for_that_nozzle_is_untouched(self):
        """Calibrated on one hotend only: keep whatever the operator set by hand
        rather than swapping it for a guess."""
        tray = {"id": "0", "tray_type": "PLA", "cali_idx": 16, "tray_info_idx": "GFA01"}
        client, _ = self._run("B", tray, None)

        client.extrusion_cali_sel.assert_not_called()

    def test_an_empty_slot_is_skipped(self):
        tray = {"id": "0", "tray_type": "", "cali_idx": -1}
        client, lookup = self._run("B", tray, _profile(15, 0, 0.020))

        lookup.assert_not_awaited()
        client.extrusion_cali_sel.assert_not_called()

    def test_a_disconnected_printer_is_a_no_op(self):
        tray = {"id": "0", "tray_type": "PLA", "cali_idx": 16, "tray_info_idx": "GFA01"}
        client, _ = self._run("B", tray, _profile(15, 0, 0.020), connected=False)

        client.extrusion_cali_sel.assert_not_called()

    def test_an_unknown_inlet_is_a_no_op(self):
        tray = {"id": "0", "tray_type": "PLA", "cali_idx": 16, "tray_info_idx": "GFA01"}
        client, lookup = self._run("C", tray, _profile(15, 0, 0.020))

        lookup.assert_not_awaited()
        client.extrusion_cali_sel.assert_not_called()


class TestTheMoveIsDetected:
    """The MQTT side: only a genuine move fires the callback."""

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")

    @staticmethod
    def _info(inlet_bits: int) -> str:
        return f"{(inlet_bits << 24) | (0xE << 8) | 1:08X}"

    def _push(self, client, inlet_bits):
        client._process_message(
            {
                "print": {
                    "gcode_state": "IDLE",
                    "device": {"fila_switch": {"in": [-1, -1], "out": [1, 1], "stat": 1, "info": 0}},
                    "ams": {"ams": [{"id": "1", "info": self._info(inlet_bits), "tray": []}]},
                }
            }
        )

    def test_a_move_fires_once(self, mqtt_client):
        seen = []
        mqtt_client.on_fts_inlet_change = lambda ams_id, inlet: seen.append((ams_id, inlet))

        self._push(mqtt_client, 1)  # In-A
        self._push(mqtt_client, 0)  # In-B

        assert seen == [(1, "B")]

    def test_the_first_sighting_does_not_fire(self, mqtt_client):
        """Every reconnect learns the bindings afresh. Re-applying K-profiles
        there would fight a binding the operator set deliberately."""
        seen = []
        mqtt_client.on_fts_inlet_change = lambda ams_id, inlet: seen.append((ams_id, inlet))

        self._push(mqtt_client, 1)

        assert seen == []

    def test_repeated_frames_do_not_fire(self, mqtt_client):
        seen = []
        mqtt_client.on_fts_inlet_change = lambda ams_id, inlet: seen.append((ams_id, inlet))

        for _ in range(4):
            self._push(mqtt_client, 1)

        assert seen == []

    def test_moving_back_fires_again(self, mqtt_client):
        seen = []
        mqtt_client.on_fts_inlet_change = lambda ams_id, inlet: seen.append((ams_id, inlet))

        self._push(mqtt_client, 1)
        self._push(mqtt_client, 0)
        self._push(mqtt_client, 1)

        assert seen == [(1, "B"), (1, "A")]
