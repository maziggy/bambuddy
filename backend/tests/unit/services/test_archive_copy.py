"""
Tests for the 3MF archive copy path.

Regression guards for #1032 where large 3MF files were silently truncated
during archiving on Raspberry Pi OS / armv7l, leaving the archive row in
place but the on-disk file no longer a valid ZIP.
"""

import io
import logging
import os
import zipfile
from pathlib import Path

import pytest

from backend.app.services.archive import ThreeMFParser, _copy_and_fsync


def _make_3mf(path: Path, payload_size: int = 0) -> None:
    """Write a minimal valid 3MF (ZIP) file with an optional large payload."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/slice_info.config", "<config/>")
        if payload_size:
            # Uncompressible payload forces real bytes on disk.
            zf.writestr("blob.bin", os.urandom(payload_size))


class TestCopyAndFsync:
    def test_copies_small_file_byte_for_byte(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        src.write_bytes(b"hello world")

        _copy_and_fsync(src, dst)

        assert dst.read_bytes() == b"hello world"

    def test_copies_large_file_byte_for_byte(self, tmp_path: Path) -> None:
        """Spans multiple 1 MiB chunks to exercise the copy loop."""
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        payload = os.urandom(5 * 1024 * 1024 + 123)  # 5 MiB + change
        src.write_bytes(payload)

        _copy_and_fsync(src, dst)

        assert dst.stat().st_size == len(payload)
        assert dst.read_bytes() == payload

    def test_preserves_mtime_via_copystat(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        src.write_bytes(b"x")
        os.utime(src, (1_700_000_000, 1_700_000_000))

        _copy_and_fsync(src, dst)

        assert int(dst.stat().st_mtime) == 1_700_000_000

    def test_overwrites_existing_destination(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        src.write_bytes(b"new")
        dst.write_bytes(b"old old old")

        _copy_and_fsync(src, dst)

        assert dst.read_bytes() == b"new"

    def test_produces_valid_zip_on_3mf(self, tmp_path: Path) -> None:
        """The whole point of #1032: copy of a valid 3MF stays a valid ZIP."""
        src = tmp_path / "src.3mf"
        dst = tmp_path / "dst.3mf"
        _make_3mf(src, payload_size=2 * 1024 * 1024)  # 2 MiB, multi-chunk
        assert zipfile.is_zipfile(src)

        _copy_and_fsync(src, dst)

        assert zipfile.is_zipfile(dst)


class TestThreeMFParserErrorVisibility:
    def test_parse_logs_warning_on_corrupted_zip(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Silent `except Exception: pass` was how #1032 escaped detection;
        parse() must now surface the failure at WARNING."""
        corrupted = tmp_path / "bad.3mf"
        corrupted.write_bytes(b"not a zip")

        with caplog.at_level(logging.WARNING, logger="backend.app.services.archive"):
            result = ThreeMFParser(corrupted).parse()

        assert result == {}
        assert any("failed to parse" in rec.message and str(corrupted) in rec.message for rec in caplog.records), (
            "Expected a WARNING mentioning the failed parse and file path"
        )

    def test_parse_returns_partial_metadata_without_raising(
        self,
        tmp_path: Path,
    ) -> None:
        """A valid-but-minimal 3MF must still parse without raising."""
        p = tmp_path / "ok.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("Metadata/slice_info.config", "<config/>")

        result = ThreeMFParser(p).parse()

        # No assertions about which keys are present — just that it didn't blow up.
        assert isinstance(result, dict)

    def test_filament_metadata_only_includes_filaments_with_used_g(
        self,
        tmp_path: Path,
    ) -> None:
        """slice_and_persist_as_archive uses parsed_metadata.filament_type/color
        to populate the new archive's filament list. The parser must filter
        out filaments whose used_g==0 — otherwise the resulting archive card
        shows every project-wide AMS slot (16+ swatches) for what was
        actually a 2-color print on a single plate.
        """
        p = tmp_path / "two-of-eighteen.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
            # 4 declared slots, only 2 actually consumed on this plate.
            zf.writestr(
                "Metadata/slice_info.config",
                """<?xml version="1.0"?>
                <config>
                  <plate>
                    <metadata key="index" value="1"/>
                    <filament id="1" type="PLA"  color="#FFFFFF" used_g="25.0" used_m="8.5"/>
                    <filament id="2" type="PETG" color="#FF0000" used_g="0"    used_m="0"/>
                    <filament id="3" type="PLA"  color="#000000" used_g="12.5" used_m="4.2"/>
                    <filament id="4" type="ABS"  color="#00FF00" used_g="0"    used_m="0"/>
                  </plate>
                </config>""",
            )

        result = ThreeMFParser(p).parse()

        # Both fields should be comma-joined strings of only the consumed
        # filaments — slot 2 (PETG #FF0000) and slot 4 (ABS #00FF00) must
        # not appear on the new archive card. The parser dedupes types,
        # so both PLA slots collapse into a single "PLA" entry; colors
        # are unique per swatch and stay distinct.
        types = result.get("filament_type", "")
        assert "PLA" in types
        assert "PETG" not in types  # used_g=0 → excluded
        assert "ABS" not in types
        colors = result.get("filament_color", "")
        assert "#FFFFFF" in colors
        assert "#000000" in colors
        assert "#FF0000" not in colors
        assert "#00FF00" not in colors


class TestZipFileSentinel:
    """Sanity check the sentinel the archive pipeline relies on."""

    def test_is_zipfile_on_truncated_zip_returns_false(self, tmp_path: Path) -> None:
        """Truncating a valid ZIP mid-stream must flip is_zipfile() to False.
        This is the exact post-condition archive_print now trusts."""
        src = tmp_path / "src.3mf"
        _make_3mf(src, payload_size=1024 * 1024)
        full = src.read_bytes()
        assert zipfile.is_zipfile(io.BytesIO(full))

        truncated = tmp_path / "truncated.3mf"
        # Strip the trailing end-of-central-directory record — exactly what a
        # short sendfile return would leave behind.
        truncated.write_bytes(full[: len(full) // 2])

        assert not zipfile.is_zipfile(truncated)
