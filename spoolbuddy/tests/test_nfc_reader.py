"""Tests for daemon.nfc_reader — tray UUID extraction and raw blocks from Bambu tag blocks."""

from unittest.mock import MagicMock

from daemon.nfc_reader import NFCReader, NFCState, _extract_tray_uuid, _raw_blocks, capacity_refusal, choose_ndef

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

    NTAG215_CC = bytes([0xE1, 0x10, 0x3E, 0x00])

    def _reader(
        self,
        cc: bytes | None = NTAG215_CC,
        read_error: Exception | None = None,
        read_back=None,
    ):
        """A reader over a fake PN5180 whose tag keeps what is written to it.

        *read_back* replaces what reading the written pages returns: a
        callable taking the written bytes, or an exception to raise.
        """
        reader = NFCReader.__new__(NFCReader)
        reader._state = NFCState.TAG_PRESENT
        reader._current_uid = self.UID.hex().upper()
        reader._current_sak = 0x00
        nfc = MagicMock()
        nfc.reactivate_card.return_value = (self.UID, 0x00)
        tag = bytearray()

        def write_pages(start_page, data):
            tag[:] = data + bytes(-len(data) % 4)
            return True

        def read_pages(start_page, num_pages):
            if start_page == 3:
                if read_error is not None:
                    raise read_error
                return cc
            if isinstance(read_back, Exception):
                raise read_back
            if read_back is not None:
                return read_back(bytes(tag))
            return bytes(tag[: num_pages * 4])

        nfc.ntag_read_pages.side_effect = read_pages
        nfc.ntag_write_pages.side_effect = write_pages
        reader._nfc = nfc
        return reader, nfc

    def _written(self, nfc) -> bytes:
        return nfc.ntag_write_pages.call_args.kwargs["data"]

    def test_an_ntag215_is_written_v2(self):
        reader, nfc = self._reader(cc=bytes([0xE1, 0x10, 0x3E, 0x00]))

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert ok
        assert "v2" in msg
        assert nfc.ntag_read_pages.call_args_list[0].kwargs == {"start_page": 3, "num_pages": 1}
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

    def test_without_v2_the_capacity_is_still_checked(self):
        # #3076: v1 alone is checked against the tag's size too.
        reader, nfc = self._reader(cc=bytes([0xE1, 0x10, 0x06, 0x00]))  # 48 bytes

        ok, msg = reader.write_ntag(TestChooseNdef.V1)

        assert not ok
        assert msg == "Tag too small: 129 bytes to write, 48 bytes of space"
        nfc.ntag_write_pages.assert_not_called()

    def test_a_tag_without_an_ndef_cc_is_refused(self):
        # A blank MIFARE Ultralight reports SAK 0x00 like an NTAG, but has no
        # NDEF Capability Container (#3076).
        reader, nfc = self._reader(cc=bytes(4))

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert not ok
        assert "Not an NDEF-formatted NTAG" in msg
        nfc.ntag_write_pages.assert_not_called()

    def test_v1_that_fits_an_ntag213_is_written(self):
        reader, nfc = self._reader(cc=bytes([0xE1, 0x10, 0x12, 0x00]))  # 144 bytes

        ok, _ = reader.write_ntag(TestChooseNdef.V1)

        assert ok
        assert self._written(nfc) == TestChooseNdef.V1


class TestWriteNtagReadBack:
    """#3076: the PN5180 can't see the tag's ACK, so the pages are read back."""

    def test_matching_data_is_a_verified_write(self):
        reader, nfc = TestWriteNtag()._reader()

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert (ok, msg) == (True, "Write successful (OpenTag3D v2)")
        # Read from page 4 over the whole payload, after a fresh select.
        assert nfc.ntag_read_pages.call_args_list[-1].kwargs == {"start_page": 4, "num_pages": 48}
        assert nfc.reactivate_card.call_count == 3

    def test_other_data_fails_the_write_and_names_the_page(self):
        def corrupt_page_9(written: bytes) -> bytes:
            data = bytearray(written)
            data[20] ^= 0xFF  # page 4 + 20 // 4
            return bytes(data)

        reader, _ = TestWriteNtag()._reader(read_back=corrupt_page_9)

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert not ok
        assert msg == "Verification failed at page 9: the tag holds other data"

    def test_no_answer_to_the_read_back_leaves_it_unverified(self):
        reader, _ = TestWriteNtag()._reader(read_back=lambda written: None)

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert (ok, msg) == (True, "Write successful (OpenTag3D v2, not verified)")

    def test_a_short_answer_leaves_it_unverified(self):
        reader, _ = TestWriteNtag()._reader(read_back=lambda written: written[:16])

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert (ok, msg) == (True, "Write successful (OpenTag3D v2, not verified)")

    def test_a_read_error_leaves_it_unverified(self):
        reader, _ = TestWriteNtag()._reader(read_back=RuntimeError("BUSY timeout"))

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert (ok, msg) == (True, "Write successful (OpenTag3D v2, not verified)")

    def test_no_answer_to_the_reselect_leaves_it_unverified(self):
        reader, nfc = TestWriteNtag()._reader()
        nfc.reactivate_card.side_effect = [(TestWriteNtag.UID, 0x00), (TestWriteNtag.UID, 0x00), None]

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert (ok, msg) == (True, "Write successful (OpenTag3D v2, not verified)")

    def test_a_failed_write_is_reported_as_such(self):
        reader, nfc = TestWriteNtag()._reader()
        nfc.ntag_write_pages.side_effect = None
        nfc.ntag_write_pages.return_value = False

        ok, msg = reader.write_ntag(TestChooseNdef.V1, TestChooseNdef.V2)

        assert (ok, msg) == (False, "Write failed")


class TestCapacityRefusal:
    def test_an_ntag213_holds_144_bytes(self):
        cc = bytes([0xE1, 0x10, 0x12, 0x00])
        assert capacity_refusal(cc, 144) is None
        assert capacity_refusal(cc, 145) == "Tag too small: 145 bytes to write, 144 bytes of space"

    def test_a_cc_that_is_not_ndef_is_refused(self):
        assert capacity_refusal(bytes([0x00, 0x00, 0x3E, 0x00]), 10) is not None

    def test_an_unread_cc_proves_nothing(self):
        assert capacity_refusal(None, 500) is None
        assert capacity_refusal(b"\xe1", 500) is None
