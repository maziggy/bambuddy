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

    def __init__(self, uid: bytes, sak: int, parts: list[bytes] | None = None, bad_bcc_level=None, gone_at_level=None):
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
        self.response = self._answer(frame)

    # -- tag --

    def _answer(self, frame: bytes) -> bytes:
        crc = bool(self.regs[0x19] & 0x01) and bool(self.regs[0x12] & 0x01)
        if frame in (b"\x52", b"\x26"):
            self.level = 0
            return b"\x44\x00"  # ATQA
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
    for name in ("reset", "load_rf_config", "rf_on", "rf_off"):
        setattr(nfc, name, lambda *_args: None)
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
