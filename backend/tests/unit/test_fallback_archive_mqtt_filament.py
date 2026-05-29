"""Tests for _extract_filament_data_from_mqtt (#1533).

The fallback PrintArchive path in main.py fires when the source 3MF can't
be downloaded from the printer at print start — common on P1S / A1 / P2S
firmwares that lock the file during printing. Before this fix the
fallback archive had every filament field NULL even though the MQTT
print-start payload already carried the AMS state and the slicer's
slot-per-print-filament mapping. The helper extracts a comma-separated
``filament_type`` / ``filament_color`` from that payload so the inventory
views can at least show what's loaded, and operators planning AMS
expansion can count filaments per print.
"""

import pytest

from backend.app.main import _extract_filament_data_from_mqtt


def _ams_unit(unit_id: int, trays: list[dict]) -> dict:
    return {"id": unit_id, "tray": trays}


def _tray(tray_id: int, ttype: str | None, color: str | None) -> dict:
    out: dict = {"id": tray_id}
    if ttype is not None:
        out["tray_type"] = ttype
    if color is not None:
        out["tray_color"] = color
    return out


class TestExtractFilamentDataFromMqtt:
    def test_empty_payload_returns_empty_dict(self):
        assert _extract_filament_data_from_mqtt({}) == {}
        assert _extract_filament_data_from_mqtt({"ams": None}) == {}
        assert _extract_filament_data_from_mqtt({"ams": {}}) == {}
        assert _extract_filament_data_from_mqtt({"ams": {"ams": []}}) == {}

    def test_no_loaded_slots_returns_empty(self):
        """All slots empty (no tray_type) → nothing to report."""
        data = {
            "ams": {
                "ams": [
                    _ams_unit(0, [_tray(i, "", "") for i in range(4)]),
                ],
            }
        }
        assert _extract_filament_data_from_mqtt(data) == {}

    def test_no_mapping_lists_all_loaded_slots_sorted(self):
        data = {
            "ams": {
                "ams": [
                    _ams_unit(
                        0,
                        [
                            _tray(0, "PLA", "FF0000"),
                            _tray(1, "PETG", "00FF00"),
                            _tray(2, "", ""),  # Empty slot — skipped.
                            _tray(3, "ABS", "0000ff"),
                        ],
                    ),
                ],
            }
        }
        result = _extract_filament_data_from_mqtt(data)
        # Order is by ascending global tray id, colors uppercased.
        assert result == {"filament_type": "PLA,PETG,ABS", "filament_color": "FF0000,00FF00,0000FF"}

    def test_ams_mapping_narrows_to_used_slots(self):
        """The slicer's slot-per-print-filament mapping wins — only used
        slots contribute, in the slicer's order (which is the order the
        print materially consumes them)."""
        data = {
            "ams": {
                "ams": [
                    _ams_unit(
                        0,
                        [
                            _tray(0, "PLA", "FF0000"),
                            _tray(1, "PETG", "00FF00"),
                            _tray(2, "ABS", "0000FF"),
                            _tray(3, "TPU", "FFFF00"),
                        ],
                    ),
                ],
            }
        }
        # Print uses slots 3 then 0 then 1 (slot 2 untouched, no entry).
        result = _extract_filament_data_from_mqtt(data, ams_mapping=[3, 0, 1])
        assert result == {"filament_type": "TPU,PLA,PETG", "filament_color": "FFFF00,FF0000,00FF00"}

    def test_ams_mapping_with_vt_tray_sentinels_filtered_out(self):
        """ams_mapping entries equal to -1 represent the VT tray (external
        spool feed). We have no AMS tray data for them — they must be
        skipped, not treated as global tray id 0."""
        data = {
            "ams": {
                "ams": [
                    _ams_unit(
                        0,
                        [
                            _tray(0, "PLA", "FF0000"),
                            _tray(1, "PETG", "00FF00"),
                        ],
                    ),
                ],
            }
        }
        result = _extract_filament_data_from_mqtt(data, ams_mapping=[-1, 0, 1])
        assert result == {"filament_type": "PLA,PETG", "filament_color": "FF0000,00FF00"}

    def test_dual_ams_global_ids_use_unit4_offset(self):
        """A dual-AMS rig has unit 0 → trays 0-3, unit 1 → trays 4-7.
        ``ams_mapping=4`` must resolve to unit 1, tray 0 — not unit 0."""
        data = {
            "ams": {
                "ams": [
                    _ams_unit(0, [_tray(0, "PLA", "FF0000")]),
                    _ams_unit(1, [_tray(0, "PETG-CF", "112233")]),
                ],
            }
        }
        result = _extract_filament_data_from_mqtt(data, ams_mapping=[4, 0])
        assert result == {"filament_type": "PETG-CF,PLA", "filament_color": "112233,FF0000"}

    def test_mapping_pointing_at_unknown_slot_falls_through_to_known_only(self):
        data = {
            "ams": {
                "ams": [
                    _ams_unit(0, [_tray(0, "PLA", "FF0000")]),
                ],
            }
        }
        # Slot 7 isn't in our AMS — entry skipped, only slot 0 remains.
        result = _extract_filament_data_from_mqtt(data, ams_mapping=[7, 0])
        assert result == {"filament_type": "PLA", "filament_color": "FF0000"}

    def test_mapping_entirely_unknown_returns_empty(self):
        """If every mapped slot is unknown the helper returns {} rather
        than silently misreporting from the all-slots fallback — the
        slicer was explicit about which slots to use."""
        data = {
            "ams": {
                "ams": [
                    _ams_unit(0, [_tray(0, "PLA", "FF0000")]),
                ],
            }
        }
        assert _extract_filament_data_from_mqtt(data, ams_mapping=[5, 6]) == {}

    def test_color_truncation_at_column_limit(self):
        """filament_color column is VARCHAR(200); long multi-color prints
        must not exceed it."""
        # 16 trays of 6-char colors + 15 commas = 96+15 = 111 chars. Safe.
        # Construct an oversized synthetic case with many distinct colors.
        trays = [_tray(i, "PLA", f"{i:06X}") for i in range(4)]
        data = {"ams": {"ams": [_ams_unit(u, trays) for u in range(8)]}}
        result = _extract_filament_data_from_mqtt(data)
        assert "filament_color" in result
        assert len(result["filament_color"]) <= 200

    def test_type_truncation_at_column_limit(self):
        """filament_type column is VARCHAR(50). Many filaments must truncate."""
        # 16 PETG-CF entries: 7 chars × 16 + 15 commas = 127 chars.
        trays = [_tray(i, "PETG-CF", "AABBCC") for i in range(4)]
        data = {"ams": {"ams": [_ams_unit(u, trays) for u in range(4)]}}
        result = _extract_filament_data_from_mqtt(data)
        assert "filament_type" in result
        assert len(result["filament_type"]) <= 50

    def test_color_missing_only_emits_type(self):
        """A tray with type but blank color still contributes to filament_type."""
        data = {
            "ams": {
                "ams": [
                    _ams_unit(0, [_tray(0, "PLA", "")]),
                ],
            }
        }
        result = _extract_filament_data_from_mqtt(data)
        assert result == {"filament_type": "PLA"}
        # filament_color absent — not empty string.
        assert "filament_color" not in result

    def test_malformed_unit_skipped_without_crash(self):
        """Defensive: unexpected MQTT shapes (non-dict in ams list, missing
        id, string tray.id) must not raise. The fallback-archive write
        runs in a hot path during print start — anything that throws here
        would bubble up and break the print log entirely."""
        data = {
            "ams": {
                "ams": [
                    "garbage",
                    {"id": "not-an-int", "tray": []},
                    _ams_unit(0, [_tray(0, "PLA", "FF0000"), {"id": "x", "tray_type": "PETG"}]),
                ],
            }
        }
        result = _extract_filament_data_from_mqtt(data)
        # Only the well-formed entry contributes; no exception.
        assert result.get("filament_type") == "PLA"

    @pytest.mark.parametrize("data", [None, {}, {"ams": "weird-string"}])
    def test_garbage_top_level_is_empty(self, data):
        assert _extract_filament_data_from_mqtt(data or {}) == {}


class TestOnPrintStartCallbackShape:
    """Regression: the callback wrapper shape the bambu_mqtt service
    actually hands to ``on_print_start`` at runtime (#1533 follow-up).

    The original #1533 fix only handled the bare ``{"ams": {"ams": [...]}}``
    inner shape, but the call site at ``backend/app/main.py::on_print_start``
    receives the wrapper
    ``{"filename", "subtask_name", "remaining_time", "raw_data": <mqtt>,
       "ams_mapping"}`` from ``backend/app/services/bambu_mqtt.py:2971-2980``.
    The lookup at ``data["ams"]`` therefore missed every real print and
    fallback archives kept their filament fields NULL — the exact regression
    the fix was supposed to close. Reproduced from JmanB52D's support
    bundle whose print start log line showed
    ``AMS 0: T0(type=PETG, color=FFFFFFFF, …)`` was sitting right there at
    ``data["raw_data"]["ams"]["ams"][0]["tray"][0]``.
    """

    def test_callback_wrapper_payload_resolves_raw_data_path(self):
        """The wrapper-shape payload must produce the same result the
        inner-shape payload would."""
        inner = {
            "ams": {
                "ams": [
                    _ams_unit(0, [_tray(0, "PETG", "FFFFFFFF")]),
                ],
            },
        }
        wrapper = {
            "filename": "/data/Metadata/plate_1.gcode",
            "subtask_name": "xyz-10mm-calibration-cube",
            "remaining_time": 1200,
            "raw_data": inner,
            "ams_mapping": [0],
        }
        result = _extract_filament_data_from_mqtt(wrapper, ams_mapping=[0])
        # The 6-char rgba `FFFFFF` is what the AMS reports (with `FF` alpha
        # tail trimmed by the catalog) — the helper preserves whatever the
        # firmware sends.
        assert result == {"filament_type": "PETG", "filament_color": "FFFFFFFF"}

    def test_wrapper_with_no_ams_mapping_falls_back_to_all_loaded(self):
        """Wrapper shape without an ams_mapping behaves the same as the
        inner-shape no-mapping path: lists every loaded slot."""
        inner = {
            "ams": {
                "ams": [
                    _ams_unit(0, [_tray(0, "PLA", "FF0000"), _tray(1, "PETG", "00FF00")]),
                ],
            },
        }
        wrapper = {"raw_data": inner}
        result = _extract_filament_data_from_mqtt(wrapper)
        assert result == {"filament_type": "PLA,PETG", "filament_color": "FF0000,00FF00"}

    def test_inner_shape_still_supported_after_wrapper_lookup(self):
        """Existing callers that pass the inner shape directly (e.g. the
        unit tests above) must keep working — the new lookup is additive."""
        inner = {
            "ams": {
                "ams": [_ams_unit(0, [_tray(0, "ASA", "112233")])],
            },
        }
        assert _extract_filament_data_from_mqtt(inner) == {
            "filament_type": "ASA",
            "filament_color": "112233",
        }

    def test_wrapper_with_missing_raw_data_returns_empty(self):
        """No raw_data wrapper AND no top-level ams → empty, no raise."""
        wrapper = {"filename": "foo.gcode", "ams_mapping": [0]}
        assert _extract_filament_data_from_mqtt(wrapper, ams_mapping=[0]) == {}

    def test_wrapper_with_non_dict_raw_data_falls_through_to_inner_lookup(self):
        """Defensive: a junk raw_data value (string / None) shouldn't crash
        and shouldn't shadow a present inner ``ams`` either. Lets us catch
        the case where MQTT decoding partially fails but the rest of the
        payload is fine."""
        wrapper = {
            "raw_data": "garbage",
            "ams": {"ams": [_ams_unit(0, [_tray(0, "PLA", "FF0000")])]},
        }
        assert _extract_filament_data_from_mqtt(wrapper) == {
            "filament_type": "PLA",
            "filament_color": "FF0000",
        }
