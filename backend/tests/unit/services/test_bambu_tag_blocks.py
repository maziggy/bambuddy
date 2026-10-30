"""Decoding Bambu tag blocks sent by SpoolBuddy (#2685)."""

import pytest
from pydantic import ValidationError

from backend.app.schemas.spoolbuddy import TagScannedRequest
from backend.app.services.bambu_tag_blocks import decode_bambu_tag_blocks

# Read from a real Bambu PLA Basic Cyan spool (product 10603)
CYAN_PLA_BASIC = {
    "1": "4130302D423038004746413030000000",
    "2": "504C4100000000000000000000000000",
    "4": "504C4120426173696300000000000000",
    "5": "0086D6FFE80300000000E03F00000000",
    "6": "3700080000000000E600BE0000000000",
    "9": "36B6C12DB3DA4A39A50C275F510BD399",
}


class TestDecodeBambuTagBlocks:
    def test_real_tag(self):
        assert decode_bambu_tag_blocks(CYAN_PLA_BASIC) == {
            "tray_id_name": "A00-B08",
            "tray_info_idx": "GFA00",
            "tray_type": "PLA",
            "tray_sub_brands": "PLA Basic",
            "tray_color": "0086D6FF",
            "tray_weight": 1000,
            "tray_diameter": 1.75,
            "drying_temp": 55,
            "drying_time": 8,
            "nozzle_temp_max": 230,
            "nozzle_temp_min": 190,
            "tray_uuid": "36B6C12DB3DA4A39A50C275F510BD399",
        }

    def test_translucent_colour_keeps_its_alpha(self):
        """Block 5 is RGBA: a translucent roll's alpha must survive, not be forced opaque."""
        # Bambu PETG Translucent, Translucent Pink, read on a real SpoolBuddy
        tray = decode_bambu_tag_blocks({**CYAN_PLA_BASIC, "5": "F9C1BD80E80300000000E03F00000000"})
        assert tray["tray_color"] == "F9C1BD80"
        assert tray["tray_weight"] == 1000
        assert tray["tray_diameter"] == 1.75

    def test_lowercase_hex_is_accepted(self):
        lower = {k: v.lower() for k, v in CYAN_PLA_BASIC.items()}
        assert decode_bambu_tag_blocks(lower) == decode_bambu_tag_blocks(CYAN_PLA_BASIC)

    def test_without_block_6_has_no_temperatures(self):
        blocks = {k: v for k, v in CYAN_PLA_BASIC.items() if k != "6"}
        tray = decode_bambu_tag_blocks(blocks)
        assert tray["tray_sub_brands"] == "PLA Basic"
        assert not {"nozzle_temp_min", "nozzle_temp_max", "drying_temp", "drying_time"} & tray.keys()

    def test_all_zero_block_9_is_no_tray_uuid(self):
        tray = decode_bambu_tag_blocks({**CYAN_PLA_BASIC, "9": "0" * 32})
        assert "tray_uuid" not in tray

    def test_missing_required_block_is_not_decoded(self):
        for missing in ("1", "2", "4", "5"):
            blocks = {k: v for k, v in CYAN_PLA_BASIC.items() if k != missing}
            assert decode_bambu_tag_blocks(blocks) is None, missing

    def test_unreadable_required_block_is_not_decoded(self):
        assert decode_bambu_tag_blocks({**CYAN_PLA_BASIC, "2": "not hex at all, 32 characters!!"}) is None
        assert decode_bambu_tag_blocks({**CYAN_PLA_BASIC, "5": "0086D6FF"}) is None

    def test_blank_filament_type_is_not_decoded(self):
        assert decode_bambu_tag_blocks({**CYAN_PLA_BASIC, "2": "0" * 32}) is None

    def test_no_blocks(self):
        assert decode_bambu_tag_blocks(None) is None
        assert decode_bambu_tag_blocks({}) is None

    def test_implausible_diameter_and_zero_weight_are_left_out(self):
        tray = decode_bambu_tag_blocks({**CYAN_PLA_BASIC, "5": "0086D6FF" + "0" * 24})
        assert "tray_weight" not in tray
        assert "tray_diameter" not in tray


class TestTagScannedRawBlocksValidation:
    BASE = {"device_id": "sb-1", "tag_uid": "865947E2"}

    def test_absent_is_none(self):
        assert TagScannedRequest(**self.BASE).raw_blocks is None

    def test_real_blocks_accepted(self):
        assert TagScannedRequest(**self.BASE, raw_blocks=CYAN_PLA_BASIC).raw_blocks == CYAN_PLA_BASIC

    @pytest.mark.parametrize(
        "raw_blocks",
        [
            {"x": "0" * 32},  # not a block number
            {"64": "0" * 32},  # past the last block of a 1K tag
            {"1": "0" * 30},  # short block
            {"1": "G" * 32},  # not hex
            {str(n): "0" * 32 for n in range(17)},  # more blocks than are ever sent
        ],
    )
    def test_malformed_blocks_rejected(self, raw_blocks):
        with pytest.raises(ValidationError):
            TagScannedRequest(**self.BASE, raw_blocks=raw_blocks)
