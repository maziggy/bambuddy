"""Tests for daemon.tag_parser — parse_bambu_blocks()."""

from daemon.tag_parser import parse_bambu_blocks


class TestParseBambuBlocks:
    """parse_bambu_blocks() extracts metadata from MIFARE Classic blocks."""

    def test_empty_dict_returns_empty(self):
        result = parse_bambu_blocks({})
        assert result == {}

    def test_tray_uuid_from_block_9(self):
        block9 = bytes.fromhex("9E0B0717BEE94D7887EB1D8DFD1A14F3")
        result = parse_bambu_blocks({9: block9})
        assert result["tray_uuid"] == "9E0B0717BEE94D7887EB1D8DFD1A14F3"

    def test_tray_uuid_not_taken_from_blocks_4_and_5(self):
        """Blocks 4-5 hold the filament type, never the tray UUID (#984)."""
        block4 = b"PLA Matte" + b"\x00" * 7
        result = parse_bambu_blocks({4: block4, 5: b"\x00" * 16})
        assert "tray_uuid" not in result

    def test_tray_uuid_missing_block_9(self):
        result = parse_bambu_blocks({1: b"\x00" * 16, 2: b"\x00" * 16})
        assert "tray_uuid" not in result

    def test_material_raw_from_block_1(self):
        block1 = b"\x50\x4c\x41\x00\x00\x00\x00\x00" + b"\xff" * 8
        blocks = {1: block1}

        result = parse_bambu_blocks(blocks)

        assert result["material_raw"] == block1[:8].hex().upper()

    def test_block2_raw_from_block_2(self):
        block2 = bytes([0xAA, 0xBB] + [0x00] * 14)
        blocks = {2: block2}

        result = parse_bambu_blocks(blocks)

        assert result["block2_raw"] == block2.hex().upper()

    def test_all_blocks_present(self):
        block1 = b"\x01" * 16
        block2 = b"\x02" * 16
        block4 = b"\x04" * 16
        block5 = b"\x05" * 16
        block9 = b"\x09" * 16
        blocks = {1: block1, 2: block2, 4: block4, 5: block5, 9: block9}

        result = parse_bambu_blocks(blocks)

        assert "tray_uuid" in result
        assert "material_raw" in result
        assert "block2_raw" in result

    def test_extra_blocks_ignored(self):
        """Blocks not in {1, 2, 9} don't affect output."""
        blocks = {0: b"\x00" * 16, 3: b"\x03" * 16, 4: b"\x04" * 16, 5: b"\x05" * 16, 6: b"\x06" * 16}
        result = parse_bambu_blocks(blocks)
        assert result == {}

    def test_tray_uuid_hex_uppercase(self):
        block9 = b"\xab\xcd\xef\x12\x34\x56\x78\x9a\xbc\xde\xf0\x11\x22\x33\x44\x55"
        blocks = {9: block9}

        result = parse_bambu_blocks(blocks)

        # Verify uppercase hex
        assert result["tray_uuid"] == result["tray_uuid"].upper()
        assert "abcdef" not in result["tray_uuid"]  # no lowercase
