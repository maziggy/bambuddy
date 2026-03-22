"""Tests for daemon.tag_parser — parse_bambu_blocks()."""

from daemon.tag_parser import parse_bambu_blocks


class TestParseBambuBlocks:
    """parse_bambu_blocks() extracts metadata from MIFARE Classic blocks."""

    def test_empty_dict_returns_empty(self):
        result = parse_bambu_blocks({})
        assert result == {}

    def test_tray_uuid_from_blocks_4_and_5(self):
        # 16 bytes per block, UUID is first 16 bytes of block4+block5
        block4 = bytes(range(16))  # 00010203...0f
        block5 = bytes(range(16, 32))  # 10111213...1f
        blocks = {4: block4, 5: block5}

        result = parse_bambu_blocks(blocks)

        # UUID = first 16 bytes of (block4 + block5) = block4 itself
        expected_uuid = block4.hex().upper()
        assert result["tray_uuid"] == expected_uuid

    def test_tray_uuid_missing_block_4(self):
        blocks = {5: b"\x00" * 16}
        result = parse_bambu_blocks(blocks)
        assert "tray_uuid" not in result

    def test_tray_uuid_missing_block_5(self):
        blocks = {4: b"\x00" * 16}
        result = parse_bambu_blocks(blocks)
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
        blocks = {1: block1, 2: block2, 4: block4, 5: block5}

        result = parse_bambu_blocks(blocks)

        assert "tray_uuid" in result
        assert "material_raw" in result
        assert "block2_raw" in result

    def test_extra_blocks_ignored(self):
        """Blocks not in {1, 2, 4, 5} don't affect output."""
        blocks = {0: b"\x00" * 16, 3: b"\x03" * 16, 6: b"\x06" * 16}
        result = parse_bambu_blocks(blocks)
        assert result == {}

    def test_tray_uuid_hex_uppercase(self):
        block4 = b"\xab\xcd\xef\x12\x34\x56\x78\x9a\xbc\xde\xf0\x11\x22\x33\x44\x55"
        block5 = b"\x00" * 16
        blocks = {4: block4, 5: block5}

        result = parse_bambu_blocks(blocks)

        # Verify uppercase hex
        assert result["tray_uuid"] == result["tray_uuid"].upper()
        assert "abcdef" not in result["tray_uuid"]  # no lowercase
