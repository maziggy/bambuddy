"""Tests for daemon.nfc_reader — tray UUID extraction and raw blocks from Bambu tag blocks."""

from unittest.mock import MagicMock

from daemon.nfc_reader import NFCReader, NFCState, _extract_tray_uuid, _raw_blocks, choose_ndef

# Measured on a real spool: block 9 equals the tray_uuid the AMS reports (#984).
BLOCK9 = bytes.fromhex("9E0B0717BEE94D7887EB1D8DFD1A14F3")
# Block 4 of a "PLA Matte" spool -- what the old blocks 4+5 read sent as tray_uuid.
BLOCK4_PLA_MATTE = b"PLA Matte" + b"\x00" * 7


class TestExtractTrayUuid:
    def test_reads_block_9(self):
        blocks = {1: b"\x01" * 16, 2: b"\x02" * 16, 4: BLOCK4_PLA_MATTE, 5: b"\x00" * 16, 9: BLOCK9}
        assert _extract_tray_uuid(blocks) == "9E0B0717BEE94D7887EB1D8DFD1A14F3"

    def test_ignores_blocks_4_and_5(self):
        """Without block 9 there is no tray UUID, never the filament type from block 4."""
        blocks = {4: BLOCK4_PLA_MATTE, 5: b"\x00" * 16}
        assert _extract_tray_uuid(blocks) is None

    def test_all_zero_block_9_is_no_uuid(self):
        assert _extract_tray_uuid({9: b"\x00" * 16}) is None

    def test_short_block_9_is_no_uuid(self):
        assert _extract_tray_uuid({9: b"\x9e\x0b"}) is None

    def test_empty_blocks(self):
        assert _extract_tray_uuid({}) is None


class TestRawBlocks:
    def test_blocks_as_uppercase_hex_keyed_by_block_number(self):
        assert _raw_blocks({9: BLOCK9, 4: BLOCK4_PLA_MATTE}) == {
            "4": "504C41204D6174746500000000000000",
            "9": "9E0B0717BEE94D7887EB1D8DFD1A14F3",
        }

    def test_empty_blocks(self):
        assert _raw_blocks({}) == {}


class TestChooseNdef:
    """#3500: OpenTag3D v2 only where the tag has room for it."""

    V1 = b"\x03" + bytes(128)  # 129 bytes, fits every NTAG
    V2 = b"\x03" + bytes(188)  # 189 bytes, more than an NTAG213 holds

    def test_ntag215_gets_v2(self):
        assert choose_ndef(bytes([0xE1, 0x10, 0x3E, 0x00]), self.V1, self.V2) == (self.V2, "v2")

    def test_ntag216_gets_v2(self):
        assert choose_ndef(bytes([0xE1, 0x10, 0x6D, 0x00]), self.V1, self.V2) == (self.V2, "v2")

    def test_ntag213_gets_v1(self):
        assert choose_ndef(bytes([0xE1, 0x10, 0x12, 0x00]), self.V1, self.V2) == (self.V1, "v1")

    def test_a_cc_that_is_not_ndef_gets_v1(self):
        assert choose_ndef(bytes([0x00, 0x00, 0x3E, 0x00]), self.V1, self.V2) == (self.V1, "v1")

    def test_an_unread_cc_gets_v1(self):
        assert choose_ndef(None, self.V1, self.V2) == (self.V1, "v1")
        assert choose_ndef(b"\xe1", self.V1, self.V2) == (self.V1, "v1")


class TestWriteNtag:
    UID = bytes.fromhex("04A1B2C3D4E5F6")

    def _reader(self, cc: bytes | None = None, read_error: Exception | None = None):
        reader = NFCReader.__new__(NFCReader)
        reader._state = NFCState.TAG_PRESENT
        reader._current_uid = self.UID.hex().upper()
        reader._current_sak = 0x00
        nfc = MagicMock()
        nfc.reactivate_card.return_value = (self.UID, 0x00)
        if read_error is not None:
            nfc.ntag_read_pages.side_effect = read_error
        else:
            nfc.ntag_read_pages.return_value = cc
        nfc.ntag_write_pages.return_value = True
        reader._nfc = nfc
        return reader, nfc

    def _written(self, nfc) -> bytes:
        return nfc.ntag_write_pages.call_args.kwargs["data"]

    def test_an_ntag215_is_written_v2(self):
        reader, nfc = self._reader(cc=bytes([0xE1, 0x10, 0x3E, 0x00]))

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert ok
        assert "v2" in msg
        nfc.ntag_read_pages.assert_called_once_with(start_page=3, num_pages=1)
        assert self._written(nfc) == TestChooseNdef.V2
        assert nfc.ntag_write_pages.call_args.kwargs["start_page"] == 4

    def test_an_ntag213_is_written_v1(self):
        reader, nfc = self._reader(cc=bytes([0xE1, 0x10, 0x12, 0x00]))

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert ok
        assert "v1" in msg
        assert self._written(nfc) == TestChooseNdef.V1

    def test_a_failed_cc_read_writes_v1(self):
        reader, nfc = self._reader(read_error=RuntimeError("BUSY timeout"))

        ok, _ = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert ok
        assert self._written(nfc) == TestChooseNdef.V1

    def test_without_v2_the_tag_is_not_read_first(self):
        reader, nfc = self._reader()

        ok, _ = reader.write_ntag(TestChooseNdef.V1)

        assert ok
        nfc.ntag_read_pages.assert_not_called()
        assert self._written(nfc) == TestChooseNdef.V1
