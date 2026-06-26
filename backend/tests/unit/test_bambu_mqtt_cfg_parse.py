"""Tests for ``parse_ams_filament_backup_from_cfg`` (#1766 prefer_lowest gate).

The function extracts bit 18 of Bambu's top-level ``print.cfg`` hex string,
which OrcaSlicer's DeviceManager.cpp:4961 maps to AMS Filament Backup. These
tests pin the bit position + cover the absent / malformed cases A1-family
printers and pre-init pushes produce.
"""

import pytest

from backend.app.services.bambu_mqtt import parse_ams_filament_backup_from_cfg


class TestParseAmsFilamentBackupFromCfg:
    def test_h2d_on_capture(self):
        # Captured 2026-06-20 from H2D fw 01.03.00.00 with backup ON.
        # Hex "C0340FC219" has bit 18 set (nibble 5 = F = 0b1111).
        assert parse_ams_filament_backup_from_cfg("C0340FC219") is True

    def test_h2d_off_capture(self):
        # Same printer, backup toggled OFF — only bit 18 flips:
        # "C0340BC219" — nibble 5 = B = 0b1011.
        assert parse_ams_filament_backup_from_cfg("C0340BC219") is False

    def test_x1c_short_hex_string_on(self):
        # X1C cfg in the investigation snapshots is short ("FCA09").
        # Bit 18 of 0xFCA09 = 0b1111110010100001001, bit18 set.
        assert parse_ams_filament_backup_from_cfg("FCA09") is True

    def test_lowercase_hex(self):
        # Robustness: int(s, 16) accepts both cases; check we don't regress.
        assert parse_ams_filament_backup_from_cfg("c0340fc219") is True

    def test_only_bit_18_isolated(self):
        # Sanity: a value with ONLY bit 18 set must parse as True.
        assert parse_ams_filament_backup_from_cfg(hex(1 << 18)[2:]) is True

    def test_bit_18_clear_but_others_set(self):
        # Set every bit EXCEPT 18 — must parse as False.
        mask = (~(1 << 18)) & 0xFFFFFFFF
        assert parse_ams_filament_backup_from_cfg(hex(mask)[2:]) is False

    @pytest.mark.parametrize(
        "value",
        [
            None,  # field omitted (A1 family old protocol)
            "",  # empty string
            123,  # firmware-emitted int instead of hex string (defensive)
            "not_hex",  # malformed
            "0xZZ",  # invalid hex
            ["FCA09"],  # wrong shape
            {"cfg": "FCA09"},  # nested by mistake
        ],
    )
    def test_invalid_returns_none(self, value):
        # None preserves today's behaviour for callers gating on backup state —
        # NOT False. Treating absent as OFF would regress A1-family scheduling.
        assert parse_ams_filament_backup_from_cfg(value) is None
