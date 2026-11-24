"""Tests for daemon.pn5180 — ISO 14443-3 cascade levels in activate_type_a() (#3258).

The PN5180 is replaced by a fake of its register and transceive commands with
one tag in the field, so the real anticollision and SELECT sequence runs.
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SEL = (0x93, 0x95, 0x97)
CT = 0x88

UID_4 = bytes.fromhex("1A2B3C4D")  # Bambu tag: MIFARE Classic 1K, single-size UID
UID_7 = bytes.fromhex("04ECE673D32A81")  # NTAG215 from #3258, double-size UID
UID_10 = bytes.fromhex("04112233445566778899")  # triple-size UID


def _bcc(part: bytes) -> int:
    return part[0] ^ part[1] ^ part[2] ^ part[3]


def _crc_a(data: bytes) -> bytes:
    """CRC_A (ISO 14443-3): CRC-16/ISO-IEC-14443-3-A, bit by bit, low byte first."""
    crc = 0x6363
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc.to_bytes(2, "little")


def _cascade_parts(uid: bytes) -> list[bytes]:
    """The four UID bytes a tag answers at each cascade level (ISO 14443-3)."""
    if len(uid) == 4:
        return [uid]
    if len(uid) == 7:
        return [bytes([CT]) + uid[:3], uid[3:]]
    return [bytes([CT]) + uid[:3], bytes([CT]) + uid[3:6], uid[6:]]


class FakeChip:
    """Register and transceive commands of a PN5180 with one Type A tag in the field.

    The tag answers WUPA/REQA, then anticollision and SELECT of the cascade level
    it is at, and nothing else. Like a real tag, it ignores an anticollision
    frame sent with CRC and a SELECT sent without one.
    """

    def __init__(
        self,
        uid: bytes,
        sak: int,
        parts: list[bytes] | None = None,
        bad_bcc_level=None,
        gone_at_level=None,
        memory: bytes | None = None,
        last_readable_page: int | None = None,
        one_read_per_reset: bool = False,
        strip_crc: bool = False,
        bad_crc_pages: tuple[int, ...] = (),
        bad_crc_once: tuple[int, ...] = (),
        rx_extra: bytes = b"",
    ):
        # An NTAG's memory from page 0, answered to READ (0x30) once selected;
        # pages from last_readable_page on don't answer, like a tag pulled away
        self.memory = memory
        self.last_readable_page = last_readable_page
        # A PN5180 that, as the Read Tag diagnostic found, answers nothing
        # after one NTAG READ until a hardware reset
        self.one_read_per_reset = one_read_per_reset
        self.stuck = False
        self.resets = 0
        # A READ reply carries the tag's CRC_A after the 16 bytes while RX CRC is
        # off, as a PN5180 delivers it; strip_crc leaves it off. The CRC is wrong
        # for pages in bad_crc_pages every time, in bad_crc_once the first time.
        # rx_extra is appended to a stripped reply, for odd lengths.
        self.strip_crc = strip_crc
        self.bad_crc_pages = bad_crc_pages
        self.bad_crc_once = set(bad_crc_once)
        self.rx_extra = rx_extra
        self.parts = parts if parts is not None else _cascade_parts(uid)
        self.sak = sak
        self.bad_bcc_level = bad_bcc_level
        self.gone_at_level = gone_at_level
        self.regs = {0x00: 0, 0x12: 0, 0x19: 0}
        self.frames: list[bytes] = []
        self.level = None  # cascade level the tag is at; None until woken
        self.response = b""

    # -- PN5180 commands --

    def write_reg(self, reg, val):
        self.regs[reg] = val

    def write_reg_or(self, reg, mask):
        self.regs[reg] = self.regs.get(reg, 0) | mask

    def write_reg_and(self, reg, mask):
        self.regs[reg] = self.regs.get(reg, 0) & mask

    def read_reg(self, reg):
        if reg == 0x13:  # RX_STATUS: received length in bits 0-8
            return len(self.response)
        return self.regs.get(reg, 0)

    def read_data(self, length):
        return self.response[:length]

    def send_data(self, data, valid_bits=0x00):
        frame = bytes(data)
        self.frames.append(frame)
        self.response = b"" if self.stuck else self._answer(frame)

    def hw_reset(self, *_args):
        """RST pin toggle: the reader recovers, the field drops and the tag goes idle."""
        self.resets += 1
        self.stuck = False
        self.level = None

    # -- tag --

    def _answer(self, frame: bytes) -> bytes:
        crc = bool(self.regs[0x19] & 0x01) and bool(self.regs[0x12] & 0x01)
        if frame in (b"\x52", b"\x26"):
            self.level = 0
            return b"\x44\x00"  # ATQA
        if frame[0] == 0x30 and len(frame) == 2 and self.memory is not None and self.level == len(self.parts):
            if not self.regs[0x19] & 0x01:
                return b""  # a tag ignores a READ sent without CRC
            page = frame[1]
            if page * 4 >= len(self.memory) or (
                self.last_readable_page is not None and page >= self.last_readable_page
            ):
                return b""
            if self.one_read_per_reset:
                self.stuck = True
            reply = self.memory[page * 4 : page * 4 + 16].ljust(16, b"\x00")
            if self.strip_crc or self.regs[0x12] & 0x01:
                return reply + self.rx_extra
            crc = _crc_a(reply)
            if page in self.bad_crc_pages or page in self.bad_crc_once:
                self.bad_crc_once.discard(page)
                crc = bytes([crc[0] ^ 0xFF, crc[1]])
            return reply + crc
        if self.level is None or self.level >= len(self.parts) or self.level == self.gone_at_level:
            return b""
        if frame[0] != SEL[self.level]:
            return b""
        part = self.parts[self.level]
        bcc = _bcc(part)
        if frame[1:] == b"\x20":
            if crc:
                return b""
            return part + bytes([bcc ^ 0xFF if self.level == self.bad_bcc_level else bcc])
        if frame[1:] == b"\x70" + part + bytes([bcc]):
            if not crc:
                return b""
            self.level += 1
            return bytes([0x04 if self.level < len(self.parts) else self.sak])
        return b""


@pytest.fixture
def pn5180_mod(monkeypatch):
    """daemon.pn5180 imported without gpiod/spidev, its sleeps skipped."""
    monkeypatch.setitem(sys.modules, "gpiod", MagicMock())
    monkeypatch.setitem(sys.modules, "spidev", MagicMock())
    monkeypatch.delitem(sys.modules, "daemon.pn5180", raising=False)
    mod = importlib.import_module("daemon.pn5180")
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(sleep=lambda _s: None, monotonic=lambda: 0.0))
    return mod


@pytest.fixture
def read_tag_mod(monkeypatch):
    """scripts/read_tag.py, the Read Tag diagnostic, imported without gpiod/spidev, its sleeps skipped."""
    monkeypatch.setitem(sys.modules, "gpiod", MagicMock())
    monkeypatch.setitem(sys.modules, "spidev", MagicMock())
    spec = importlib.util.spec_from_file_location(
        "spoolbuddy_read_tag", Path(__file__).resolve().parents[1] / "scripts" / "read_tag.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(sleep=lambda _s: None, monotonic=lambda: 0.0))
    return mod


def _reader(mod, chip: FakeChip):
    """A PN5180 whose register and transceive commands go to ``chip``."""
    nfc = mod.PN5180.__new__(mod.PN5180)
    for name in ("write_reg", "write_reg_or", "write_reg_and", "read_reg", "read_data", "send_data"):
        setattr(nfc, name, getattr(chip, name))
    for name in ("load_rf_config", "rf_on", "rf_off"):
        setattr(nfc, name, lambda *_args: None)
    nfc.reset = chip.hw_reset
    return nfc


def _sels(chip: FakeChip) -> list[int]:
    return [frame[0] for frame in chip.frames if len(frame) > 1]


class TestActivateTypeA:
    def test_single_size_uid_stops_at_level_1(self, pn5180_mod):
        chip = FakeChip(UID_4, sak=0x08)

        assert _reader(pn5180_mod, chip).activate_type_a() == (UID_4, 0x08)
        assert chip.frames == [
            b"\x52",
            b"\x93\x20",
            b"\x93\x70" + UID_4 + bytes([_bcc(UID_4)]),
        ]

    def test_double_size_uid_is_read_to_level_2(self, pn5180_mod):
        """An NTAG reports all seven bytes and its final SAK, not 88 + three bytes."""
        chip = FakeChip(UID_7, sak=0x00)

        uid, sak = _reader(pn5180_mod, chip).activate_type_a()

        assert uid.hex().upper() == "04ECE673D32A81"
        assert sak == 0x00
        level2 = UID_7[3:]
        assert chip.frames[-2:] == [b"\x95\x20", b"\x95\x70" + level2 + bytes([_bcc(level2)])]

    def test_triple_size_uid_is_read_to_level_3(self, pn5180_mod):
        chip = FakeChip(UID_10, sak=0x00)

        assert _reader(pn5180_mod, chip).activate_type_a() == (UID_10, 0x00)
        assert _sels(chip) == [0x93, 0x93, 0x95, 0x95, 0x97, 0x97]

    def test_double_size_mifare_classic_keeps_its_final_sak(self, pn5180_mod):
        chip = FakeChip(UID_7, sak=0x08)

        assert _reader(pn5180_mod, chip).activate_type_a() == (UID_7, 0x08)

    def test_crc_is_off_for_each_anticollision_and_on_for_each_select(self, pn5180_mod):
        """The fake tag ignores frames with the wrong CRC setting, so a full read proves it."""
        chip = FakeChip(UID_10, sak=0x00)

        assert _reader(pn5180_mod, chip).activate_type_a() is not None
        assert chip.level == 3

    def test_bad_bcc_at_level_2_is_no_tag(self, pn5180_mod):
        chip = FakeChip(UID_7, sak=0x00, bad_bcc_level=1)

        assert _reader(pn5180_mod, chip).activate_type_a() is None
        assert b"\x95\x20" in chip.frames
        assert not any(frame.startswith(b"\x95\x70") for frame in chip.frames)

    def test_no_answer_at_level_2_is_no_tag(self, pn5180_mod):
        """A tag that leaves the field after level 1 is not reported as 88 + three bytes."""
        chip = FakeChip(UID_7, sak=0x00, gone_at_level=1)

        assert _reader(pn5180_mod, chip).activate_type_a() is None

    def test_incomplete_sak_without_cascade_tag_is_no_tag(self, pn5180_mod):
        chip = FakeChip(UID_7, sak=0x00, parts=[UID_7[:4], UID_7[3:]])

        assert _reader(pn5180_mod, chip).activate_type_a() is None
        assert 0x95 not in _sels(chip)

    def test_uid_still_incomplete_after_level_3_is_no_tag(self, pn5180_mod):
        part = bytes([CT, 0x01, 0x02, 0x03])
        chip = FakeChip(UID_10, sak=0x00, parts=[part, part, part, part])

        assert _reader(pn5180_mod, chip).activate_type_a() is None
        assert _sels(chip) == [0x93, 0x93, 0x95, 0x95, 0x97, 0x97]


class TestNfcReaderPoll:
    def test_ntag_is_reported_with_its_full_uid(self, pn5180_mod):
        from daemon.nfc_reader import NFCReader

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_7, sak=0x00))

        event, data = reader.poll()

        assert event == "tag_detected"
        assert data["tag_uid"] == "04ECE673D32A81"
        assert data["sak"] == 0x00
        assert data["tag_type"] == "ntag"
        # NTAGs are not Bambu tags: no block read, nothing for the backend to decode (#2685)
        assert data["raw_blocks"] is None

    def test_bambu_tag_is_reported_with_its_data_blocks(self, pn5180_mod):
        """A MIFARE Classic poll sends the blocks read_bambu_tag() read, as hex, for the backend to decode (#2685)."""
        from daemon.nfc_reader import NFCReader

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_4, sak=0x08))
        # Whatever blocks the driver is told to read; block 9 holds the tray UUID
        tray_uuid = bytes.fromhex("36B6C12DB3DA4A39A50C275F510BD399")
        reader._nfc.read_bambu_tag = MagicMock(
            side_effect=lambda uid: {
                block: tray_uuid if block == 9 else bytes([block]) * 16 for block in pn5180_mod.BAMBU_BLOCKS
            }
        )

        event, data = reader.poll()

        assert event == "tag_detected"
        assert data["tag_type"] == "mifare_classic"
        reader._nfc.read_bambu_tag.assert_called_once_with(UID_4)
        # Block 6 (temperatures) included, not just the blocks the tray UUID needs
        assert sorted(data["raw_blocks"], key=int) == ["1", "2", "4", "5", "6", "9"]
        assert data["raw_blocks"]["6"] == "06" * 16
        assert data["raw_blocks"]["9"] == "36B6C12DB3DA4A39A50C275F510BD399"
        assert data["tray_uuid"] == "36B6C12DB3DA4A39A50C275F510BD399"

    def test_failed_bambu_read_sends_no_blocks(self, pn5180_mod):
        from daemon.nfc_reader import NFCReader

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_4, sak=0x08))
        reader._nfc.read_bambu_tag = MagicMock(return_value=None)

        event, data = reader.poll()

        assert event == "tag_detected"
        assert data["raw_blocks"] is None
        assert data["tray_uuid"] is None

    @pytest.mark.parametrize(
        "error",
        [TimeoutError("PN5180 BUSY timeout"), OSError(5, "Input/output error")],
        ids=["busy-timeout", "spi-error"],
    )
    def test_reader_fault_during_bambu_read_still_reports_the_tag(self, pn5180_mod, error, caplog):
        """A fault in read_bambu_tag() must not escape poll() and stop nfc_poll_loop (#3377)."""
        from daemon.nfc_reader import NFCReader, NFCState

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_4, sak=0x08))
        reader._nfc.read_bambu_tag = MagicMock(side_effect=error)

        with caplog.at_level("WARNING", logger="daemon.nfc_reader"):
            event, data = reader.poll()

        assert event == "tag_detected"
        assert data["tag_uid"] == UID_4.hex().upper()
        assert data["tag_type"] == "mifare_classic"
        # Matched by UID instead; the tag stays present, so it isn't reported again
        assert data["tray_uuid"] is None
        assert reader.state == NFCState.TAG_PRESENT
        assert any("Bambu tag read failed for " + UID_4.hex().upper() in r.getMessage() for r in caplog.records)


class TestReadTagDiagnostic:
    """The Read Tag diagnostic has its own activate_type_a() and shows the UID the daemon reports."""

    @pytest.mark.parametrize(
        ("uid", "sak"), [(UID_4, 0x08), (UID_7, 0x00), (UID_10, 0x00)], ids=["4-byte", "7-byte", "10-byte"]
    )
    def test_the_full_uid_is_read(self, read_tag_mod, uid, sak):
        chip = FakeChip(uid, sak=sak)

        assert _reader(read_tag_mod, chip).activate_type_a() == (uid, sak)

    def test_a_tag_gone_at_level_2_is_no_tag(self, read_tag_mod):
        chip = FakeChip(UID_7, sak=0x00, gone_at_level=1)

        assert _reader(read_tag_mod, chip).activate_type_a() is None


# --- read_bambu_tag retries (#3377) ---

BAMBU_UID = bytes.fromhex("8659A1B2")


def _sectors(mod) -> int:
    return len({block // 4 for block in mod.BAMBU_BLOCKS})


def _make_reader(mod, auth_results, reactivate_results=None):
    """A PN5180 with the hardware calls mocked out; auth_results feeds mfc_authenticate."""
    reader = mod.PN5180.__new__(mod.PN5180)
    reader.write_reg = MagicMock()
    reader.write_reg_and = MagicMock()
    reader.mfc_authenticate = MagicMock(side_effect=auth_results)
    reader.mfc_read_block = MagicMock(side_effect=lambda block: bytes([block]) * 16)
    if reactivate_results is None:
        reader.reactivate_card = MagicMock(return_value=(BAMBU_UID, 0x08))
    else:
        reader.reactivate_card = MagicMock(side_effect=reactivate_results)
    return reader


class TestReadBambuTagReselectRetry:
    def test_failed_initial_reselect_is_retried(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [True] * _sectors(mod), reactivate_results=[None, (BAMBU_UID, 0x08)])
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert sorted(blocks) == mod.BAMBU_BLOCKS
        assert reader.reactivate_card.call_count == 2
        assert reader.mfc_authenticate.call_count == _sectors(mod)

    def test_gives_up_when_the_card_never_reselects(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [], reactivate_results=[None] * mod.BAMBU_READ_ATTEMPTS)
        assert reader.read_bambu_tag(BAMBU_UID) is None
        assert reader.reactivate_card.call_count == mod.BAMBU_READ_ATTEMPTS
        reader.mfc_authenticate.assert_not_called()

    def test_a_different_tag_is_not_read(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [], reactivate_results=[(bytes.fromhex("12E97229"), 0x08)])
        assert reader.read_bambu_tag(BAMBU_UID) is None
        assert reader.reactivate_card.call_count == 1
        reader.mfc_authenticate.assert_not_called()


class TestReadBambuTagAuthRetry:
    def test_first_try_success_does_not_reselect(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [True] * _sectors(mod))
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert sorted(blocks) == mod.BAMBU_BLOCKS
        assert reader.mfc_authenticate.call_count == _sectors(mod)
        assert reader.reactivate_card.call_count == 1  # only the initial reset

    def test_transient_failure_is_retried_after_reselect(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [False] + [True] * _sectors(mod))
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert sorted(blocks) == mod.BAMBU_BLOCKS
        assert blocks[9] == bytes([9]) * 16
        assert reader.mfc_authenticate.call_count == _sectors(mod) + 1
        assert reader.reactivate_card.call_count == 2  # initial reset + one re-select

    def test_gives_up_after_all_attempts_fail(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [False] * mod.BAMBU_READ_ATTEMPTS)
        assert reader.read_bambu_tag(BAMBU_UID) is None
        assert reader.mfc_authenticate.call_count == mod.BAMBU_READ_ATTEMPTS
        assert reader.reactivate_card.call_count == mod.BAMBU_READ_ATTEMPTS
        reader.mfc_read_block.assert_not_called()

    def test_failed_reselect_skips_to_next_attempt(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(
            mod,
            [False] + [True] * _sectors(mod),
            reactivate_results=[(BAMBU_UID, 0x08), None, (BAMBU_UID, 0x08)],
        )
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert sorted(blocks) == mod.BAMBU_BLOCKS
        # The attempt whose re-select failed never tried to authenticate
        assert reader.mfc_authenticate.call_count == _sectors(mod) + 1
        assert reader.reactivate_card.call_count == 3

    def test_a_reselect_onto_a_different_tag_is_not_used(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(
            mod,
            [False] + [True] * _sectors(mod),
            reactivate_results=[(BAMBU_UID, 0x08), (bytes.fromhex("12E97229"), 0x08), (BAMBU_UID, 0x08)],
        )
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert sorted(blocks) == mod.BAMBU_BLOCKS
        assert reader.mfc_authenticate.call_count == _sectors(mod) + 1
        assert reader.reactivate_card.call_count == 3

    def test_failure_in_later_sector_is_retried(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [True, False, False, True, True])
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert sorted(blocks) == mod.BAMBU_BLOCKS
        retried_blocks = [c.args[0] for c in reader.mfc_authenticate.call_args_list[1:4]]
        assert all(block // 4 == 1 for block in retried_blocks)


def _flaky_reads(bad_reads):
    """mfc_read_block that returns bad_reads[block] (popped in order) before the real data."""

    def read(block):
        if bad_reads.get(block):
            return bad_reads[block].pop(0)
        return bytes([block]) * 16

    return read


class TestReadBambuTagBlockRetry:
    def test_every_block_is_read_twice(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [True] * _sectors(mod))
        reader.read_bambu_tag(BAMBU_UID)
        reads = [c.args[0] for c in reader.mfc_read_block.call_args_list]
        assert reads == [block for block in mod.BAMBU_BLOCKS for _ in range(2)]

    def test_failed_block_read_redoes_the_sector(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [True] * (_sectors(mod) + 1))
        reader.mfc_read_block.side_effect = _flaky_reads({9: [None]})
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert blocks[9] == bytes([9]) * 16
        assert reader.reactivate_card.call_count == 2  # initial reset + one re-select
        assert reader.mfc_authenticate.call_count == _sectors(mod) + 1
        assert reader.mfc_authenticate.call_args_list[-1].args[0] == 9

    def test_a_corrupted_read_is_not_returned(self, pn5180_mod):
        mod = pn5180_mod
        """Two reads that disagree are retried, never passed on (#3377: a wrong tray UUID)."""
        corrupted = bytes.fromhex("ABF589198C443E7722AAF1B987B46D7E")
        reader = _make_reader(mod, [True] * (_sectors(mod) + 1))
        reader.mfc_read_block.side_effect = _flaky_reads({9: [corrupted]})
        blocks = reader.read_bambu_tag(BAMBU_UID)
        assert blocks[9] == bytes([9]) * 16
        assert reader.mfc_authenticate.call_count == _sectors(mod) + 1

    def test_gives_up_when_a_block_never_reads(self, pn5180_mod):
        mod = pn5180_mod
        reader = _make_reader(mod, [True] * (_sectors(mod) + mod.BAMBU_READ_ATTEMPTS))
        reader.mfc_read_block.side_effect = _flaky_reads({4: [None] * mod.BAMBU_READ_ATTEMPTS})
        assert reader.read_bambu_tag(BAMBU_UID) is None
        assert reader.reactivate_card.call_count == mod.BAMBU_READ_ATTEMPTS


class TestMfcReadBlockStatus:
    """mfc_read_block() accepts a frame only when RX_STATUS shows 16 bytes and nothing else (#3377).

    The values are the ones measured on a real SpoolBuddy (PN5180, genuine Bambu tag):
    every good read gave RX_STATUS 0x010; a tag's NAK gave 0x18000; no answer gave 0.
    """

    @staticmethod
    def _reader(mod, rx_status: int):
        reader = mod.PN5180.__new__(mod.PN5180)
        reader.write_reg = MagicMock()
        reader.write_reg_or = MagicMock()
        reader.set_transceive_mode = MagicMock()
        reader.send_data = MagicMock()
        reader.read_reg = MagicMock(return_value=rx_status)
        reader.read_data = MagicMock(return_value=bytes(range(16)))
        return reader

    def test_good_read_returns_the_block(self, pn5180_mod):
        reader = self._reader(pn5180_mod, 0x00000010)
        assert reader.mfc_read_block(4) == bytes(range(16))
        reader.read_reg.assert_called_once_with(0x13)
        reader.read_data.assert_called_once_with(16)

    @pytest.mark.parametrize(
        "rx_status",
        [0x00018000, 0x00000000, 0x00000008, 0x00018010, 0x00000210],
        ids=["tag-nak", "no-answer", "short-frame", "16-bytes-with-error-bits", "16-bytes-with-a-frame-bit"],
    )
    def test_anything_but_a_clean_16_byte_frame_is_a_failed_read(self, pn5180_mod, rx_status):
        reader = self._reader(pn5180_mod, rx_status)
        assert reader.mfc_read_block(4) is None
        reader.read_data.assert_not_called()


# --- NDEF read on an NTAG scan (#3657) ------------------------------------------

# An NTAG215's capability container: NDEF, version 1.0, 496-byte data area, read/write
CC_NTAG215 = bytes([0xE1, 0x10, 0x3E, 0x00])
# An OpenTag3D 2.004 tag written by Bambuddy, as an ESP32 client read it (189 bytes)
OPENTAG3D_AREA = bytes.fromhex(
    "03bad215a26170706c69636174696f6e2f6f70656e746167336407d450455447204d6174746553756e6c752020202020"
    "202020202020426c61636b2020202020202020202020202020202020202020202020202020203f4141ff000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000006d60000000000000000000000000000000003e800fafe"
)


def _ntag(data_area: bytes, cc: bytes = CC_NTAG215, size: int = 4 * 135) -> bytes:
    """NTAG memory: UID/lock pages 0-2, the CC at page 3, the data area from page 4."""
    return (bytes(12) + cc + data_area).ljust(size, b"\x00")


def _reads(chip: FakeChip) -> list[int]:
    return [frame[1] for frame in chip.frames if len(frame) == 2 and frame[0] == 0x30]


class TestNdefAreaEnd:
    """Where the TLVs of a data area end, from the bytes read so far."""

    @pytest.mark.parametrize(
        "data, end",
        [
            (b"\x03\x00\xfe", 2),  # empty NDEF message
            (b"\xfe", 1),  # terminator only
            (b"\x00\x00\xfe", 3),
            (b"\x03\x10" + bytes(16), 18),
            (b"\x03\xff\x01\x2c", 4 + 300),  # 3-byte length
            (b"\x01\x03\xa0\x10\x44\x03\x05" + bytes(5), 12),  # Lock Control TLV first
            (b"\xe1\x10\x3e\x00\x03\x05" + bytes(5), 11),  # CC copy from before #3500
            (b"\xff" * 12, 12),  # wiped: not TLVs, so everything read goes to the backend
            (b"\x00\x00\x42\x10", 4),  # other data after NULL TLVs
        ],
    )
    def test_end(self, data, end):
        from daemon.nfc_reader import ndef_area_end

        assert ndef_area_end(data) == end

    @pytest.mark.parametrize("data", [b"", b"\x00", b"\x03", b"\x03\xff\x01", b"\x01\x03\xa0"])
    def test_too_short_to_tell(self, data):
        from daemon.nfc_reader import ndef_area_end

        assert ndef_area_end(data) is None


class TestReadNdefArea:
    def test_opentag3d_tag_is_read_to_the_end_of_its_message(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA))
        area = read_ndef_area(_reader(pn5180_mod, chip), UID_7)

        assert area == OPENTAG3D_AREA[:-1]  # up to the message's end, not the terminator
        # Page 3 (CC and the first 12 bytes), then 4-page reads up to byte 188 of the data area
        assert _reads(chip) == [3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47]

    def test_blank_tag_costs_one_read(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(b"\x03\x00\xfe"))
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) == b"\x03\x00"
        assert _reads(chip) == [3]

    def test_cc_copy_from_before_3500_is_kept_for_the_backend(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        area = CC_NTAG215 + b"\x03\x05" + b"ABCDE" + b"\xfe"
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(area))
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) == area[:-1]

    def test_never_reads_past_the_data_area_the_cc_reports(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        # An NTAG213 (144-byte data area) whose TLV claims more than fits:
        # reading on would only fetch the whole data area, so it stops
        cc = bytes([0xE1, 0x10, 0x12, 0x00])
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(b"\x03\xff\x01\x2c", cc=cc, size=4 * 45))
        area = read_ndef_area(_reader(pn5180_mod, chip), UID_7)
        assert area == (b"\x03\xff\x01\x2c" + bytes(8))
        assert _reads(chip) == [3]

    def test_an_ntag216_tlv_claiming_64_kb_is_not_read_to_the_end(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        cc = bytes([0xE1, 0x10, 0x6D, 0x00])
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(b"\x03\xff\xff\xfe", cc=cc, size=4 * 231))
        assert len(read_ndef_area(_reader(pn5180_mod, chip), UID_7)) == 12
        assert _reads(chip) == [3]

    def test_a_zero_filled_tag_stops_after_a_run_of_null_tlvs(self, pn5180_mod):
        from daemon.nfc_reader import NDEF_NULL_RUN, read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(bytes(496)))
        area = read_ndef_area(_reader(pn5180_mod, chip), UID_7)
        assert area == bytes(44)  # 12 + 16 + 16 bytes, the first read at or past NDEF_NULL_RUN
        assert len(area) >= NDEF_NULL_RUN
        assert _reads(chip) == [3, 7, 11]

    def test_a_cc_with_no_data_area_sends_nothing(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA, cc=bytes([0xE1, 0x10, 0x00, 0x00])))
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) == b""
        assert _reads(chip) == [3]

    def test_a_tag_that_never_answers_page_3_gives_none(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), last_readable_page=3)
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) is None

    def test_a_read_that_stops_sends_what_was_read(self, pn5180_mod, caplog):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), last_readable_page=15)
        with caplog.at_level("WARNING", logger="daemon.nfc_reader"):
            area = read_ndef_area(_reader(pn5180_mod, chip), UID_7)
        # Pages 4-14 arrived; the backend sees the message is cut off
        assert area == OPENTAG3D_AREA[:44]
        assert "NDEF read stopped at page 15" in caplog.text

    def test_tag_without_an_ndef_cc_is_not_read(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        # A blank MIFARE Ultralight also answers SAK 0x00, with no CC
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(b"\x03\x00\xfe", cc=bytes(4)))
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) is None
        assert _reads(chip) == [3]

    def test_a_different_tag_is_not_read(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA))
        assert read_ndef_area(_reader(pn5180_mod, chip), bytes.fromhex("04AABBCCDDEEFF")) is None
        assert _reads(chip) == []


class TestReadNdefAreaRecovery:
    """A plain READ first; a reset and re-select only when a READ gets no answer."""

    def test_a_reader_that_answers_every_read_is_never_reset(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA))
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) == OPENTAG3D_AREA[:-1]
        assert chip.resets == 0

    def test_a_reader_that_answers_one_read_per_reset_still_reads_the_tag(self, pn5180_mod):
        from daemon.nfc_reader import NdefReadStats, read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), one_read_per_reset=True)
        stats = NdefReadStats()
        area = read_ndef_area(_reader(pn5180_mod, chip), UID_7, stats)

        assert area == OPENTAG3D_AREA[:-1]
        # Every READ after the first needed one reset: 11 more chunks after page 3.
        # Pages 7 and 11 tried a plain READ first; after two in a row the reader
        # resets first.
        assert chip.resets == 11
        assert _reads(chip) == [3, 7, 7, 11, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47]
        assert (stats.reads, stats.resets, stats.reset_first) == (14, 11, True)

    def test_a_reader_known_to_need_resets_skips_the_plain_read(self, pn5180_mod):
        from daemon.nfc_reader import NdefReadStats, read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), one_read_per_reset=True)
        stats = NdefReadStats(reset_first=True)

        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7, stats) == OPENTAG3D_AREA[:-1]
        assert _reads(chip) == [3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47]

    def test_a_reader_fault_on_one_read_is_retried(self, pn5180_mod):
        from daemon.nfc_reader import NdefReadStats, read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA))
        nfc = _reader(pn5180_mod, chip)
        real_read = nfc.ntag_read_pages
        calls = []

        def flaky(*args, **kwargs):
            calls.append(kwargs.get("start_page"))
            if len(calls) == 3:
                raise TimeoutError("PN5180 BUSY timeout")
            return real_read(*args, **kwargs)

        nfc.ntag_read_pages = flaky
        stats = NdefReadStats()
        assert read_ndef_area(nfc, UID_7, stats) == OPENTAG3D_AREA[:-1]
        assert (stats.errors, stats.resets) == (1, 1)

    def test_a_crc_error_on_one_read_is_retried(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), bad_crc_once=(11,))
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) == OPENTAG3D_AREA[:-1]
        assert chip.resets == 1

    def test_a_different_tag_after_the_reset_is_not_read_on(self, pn5180_mod, caplog):
        from daemon.nfc_reader import read_ndef_area

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), one_read_per_reset=True)
        nfc = _reader(pn5180_mod, chip)
        real_reset = nfc.reset

        def swap_tag(*args):
            # The spool was swapped for another while the reader reset
            real_reset(*args)
            chip.parts = _cascade_parts(bytes.fromhex("04AABBCCDDEEFF"))

        nfc.reset = swap_tag
        with caplog.at_level("WARNING", logger="daemon.nfc_reader"):
            area = read_ndef_area(nfc, UID_7)

        # Only the first READ's 12 bytes, from the scanned tag
        assert area == OPENTAG3D_AREA[:12]
        # The plain READ of page 7 got no answer; no READ went to the other tag
        assert _reads(chip) == [3, 7]
        assert chip.resets == 3  # NDEF_READ_ATTEMPTS - 1, then it gives up
        assert "NDEF read stopped at page 7" in caplog.text

    def test_wiped_tag_stops_after_the_first_read(self, pn5180_mod):
        from daemon.nfc_reader import read_ndef_area

        # The 12 bytes go to the backend, which tells a wiped tag from a blank one
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(b"\xff" * 200))
        assert read_ndef_area(_reader(pn5180_mod, chip), UID_7) == b"\xff" * 12
        assert _reads(chip) == [3]


class TestNtagReadCrc:
    def test_reply_with_its_crc_is_read(self, pn5180_mod):
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA))
        nfc = _reader(pn5180_mod, chip)
        assert nfc.activate_type_a() is not None

        assert nfc.ntag_read_pages(start_page=4, num_pages=8) == OPENTAG3D_AREA[:32]
        assert len(chip.response) == 18  # the check ran

    @pytest.mark.parametrize("extra", [b"", b"\x00"], ids=["16-bytes", "17-bytes"])
    def test_reply_without_a_whole_crc_is_taken_as_is(self, pn5180_mod, extra):
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), strip_crc=True, rx_extra=extra)
        nfc = _reader(pn5180_mod, chip)
        assert nfc.activate_type_a() is not None

        assert nfc.ntag_read_pages(start_page=4, num_pages=4) == OPENTAG3D_AREA[:16]

    def test_reply_with_a_wrong_crc_is_refused(self, pn5180_mod, caplog):
        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), bad_crc_pages=(8,))
        nfc = _reader(pn5180_mod, chip)
        assert nfc.activate_type_a() is not None

        with caplog.at_level("WARNING", logger="daemon.pn5180"):
            assert nfc.ntag_read_pages(start_page=4, num_pages=8) is None
        assert "NTAG read page 8: CRC mismatch" in caplog.text

    def test_crc_matches_the_tags(self, pn5180_mod):
        """The driver's table-free CRC_A agrees with a bit-by-bit one, and with HLTA's known 57 CD."""
        assert pn5180_mod._crc_a(bytes([0x50, 0x00])) == bytes([0x57, 0xCD])
        for data in (OPENTAG3D_AREA[:16], bytes(16), b"\xff" * 16, bytes(range(16))):
            assert pn5180_mod._crc_a(data) == _crc_a(data)


class TestNfcReaderPollNdef:
    def test_ntag_is_reported_with_its_ndef_data(self, pn5180_mod):
        from daemon.nfc_reader import NFCReader

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA)))

        event, data = reader.poll()

        assert event == "tag_detected"
        assert data["tag_type"] == "ntag"
        assert data["ndef_hex"] == OPENTAG3D_AREA[:-1].hex().upper()
        assert data["raw_blocks"] is None

    def test_wiped_ntag_sends_what_it_read(self, pn5180_mod):
        from daemon.nfc_reader import NFCReader

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_7, sak=0x00, memory=_ntag(b"\xff" * 200)))

        event, data = reader.poll()

        assert event == "tag_detected"
        assert data["ndef_hex"] == "FF" * 12

    def test_bambu_tag_sends_no_ndef_data(self, pn5180_mod):
        from daemon.nfc_reader import NFCReader

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_4, sak=0x08))
        reader._nfc.read_bambu_tag = MagicMock(return_value=None)

        _, data = reader.poll()

        assert data["ndef_hex"] is None

    @pytest.mark.parametrize(
        "error",
        [TimeoutError("PN5180 BUSY timeout"), OSError(5, "Input/output error")],
        ids=["busy-timeout", "spi-error"],
    )
    def test_reader_fault_during_ndef_read_still_reports_the_tag(self, pn5180_mod, error, caplog):
        """As for a Bambu read (#3377), a fault costs the tag data, not the scan."""
        from daemon.nfc_reader import NFCReader, NFCState

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA)))
        reader._nfc.ntag_read_pages = MagicMock(side_effect=error)

        with caplog.at_level("INFO", logger="daemon.nfc_reader"):
            event, data = reader.poll()

        assert event == "tag_detected"
        assert data["tag_uid"] == UID_7.hex().upper()
        assert data["ndef_hex"] is None
        assert reader.state == NFCState.TAG_PRESENT
        assert any(
            r.getMessage().startswith(f"NDEF read of {UID_7.hex().upper()}: 0 bytes") and "4 errors" in r.getMessage()
            for r in caplog.records
        )

    def test_a_fault_outside_the_reads_still_reports_the_tag(self, pn5180_mod, caplog):
        from daemon.nfc_reader import NFCReader

        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA)))
        reader._nfc.reactivate_card = MagicMock(side_effect=OSError(5, "Input/output error"))

        with caplog.at_level("WARNING", logger="daemon.nfc_reader"):
            event, data = reader.poll()

        assert event == "tag_detected"
        assert data["ndef_hex"] is None
        assert "NDEF read failed for " + UID_7.hex().upper() in caplog.text

    def test_a_reader_stuck_after_read_keeps_the_tag_present(self, pn5180_mod):
        """After the NDEF read the reader is reset, so the next polls find the same tag.

        Without it, a PN5180 that answers nothing after a READ until a hardware
        reset would report the tag removed and found again, over and over.
        """
        from daemon.nfc_reader import NFCReader

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), one_read_per_reset=True)
        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, chip)

        events = [reader.poll()[0] for _ in range(8)]

        assert events == ["tag_detected"] + ["none"] * 7
        # And a write right after the scan can select the tag
        assert reader._nfc.reactivate_card() == (UID_7, 0x00)

    def test_the_reset_need_is_remembered_for_the_next_scan(self, pn5180_mod):
        from daemon.nfc_reader import NFCReader, NFCState

        chip = FakeChip(UID_7, sak=0x00, memory=_ntag(OPENTAG3D_AREA), one_read_per_reset=True)
        reader = NFCReader()
        reader._nfc = _reader(pn5180_mod, chip)
        reader.poll()
        assert reader._ndef_reset_first is True

        # The tag is taken off and put back: no plain READ is wasted on page 7 this time
        chip.frames.clear()
        reader._state = NFCState.IDLE
        reader.poll()
        assert _reads(chip) == [3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47]
