"""Unit tests for `extract_filament_requirements` (#1188).

The helper is the parser the scheduler used to own and the VP queue-mode
write path now also uses. Pin the contract end-to-end so a refactor of one
caller can't silently break the other.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from backend.app.services.filament_requirements import extract_filament_requirements


def _make_3mf(
    file_path: Path,
    *,
    plates: list[tuple[int, list[dict]]] | None = None,
    flat_filaments: list[dict] | None = None,
) -> None:
    """Build a minimal 3MF zip. Either ``plates`` (list of
    ``(plate_index, filaments)``) or ``flat_filaments`` (no plate wrapper)
    drives the slice_info.config shape."""

    def _filament_xml(filaments: list[dict]) -> str:
        return "".join(
            f'<filament id="{f["id"]}" type="{f["type"]}" color="{f["color"]}" '
            f'used_g="{f["used_g"]}" tray_info_idx="{f.get("tray_info_idx", "")}"/>'
            for f in filaments
        )

    if plates is not None:
        plate_xml = "".join(
            f'<plate><metadata key="index" value="{idx}"/>{_filament_xml(fs)}</plate>' for idx, fs in plates
        )
        body = plate_xml
    elif flat_filaments is not None:
        body = _filament_xml(flat_filaments)
    else:
        body = ""
    config = f'<?xml version="1.0" encoding="utf-8"?><config>{body}</config>'
    with zipfile.ZipFile(file_path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", config)


class TestExtractFilamentRequirements:
    def test_returns_per_slot_dicts_for_plate(self, tmp_path: Path):
        f = tmp_path / "model.3mf"
        _make_3mf(
            f,
            plates=[
                (
                    1,
                    [
                        {"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "12.5"},
                        {"id": "2", "type": "PETG", "color": "#000000", "used_g": "4.2"},
                    ],
                )
            ],
        )
        out = extract_filament_requirements(f, plate_id=1)
        assert out == [
            {"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "", "used_grams": 12.5},
            {"slot_id": 2, "type": "PETG", "color": "#000000", "tray_info_idx": "", "used_grams": 4.2},
        ]

    def test_skips_zero_use_filaments(self, tmp_path: Path):
        """Slot present in slice_info.config but `used_g <= 0` means the
        plate doesn't actually consume that filament — must not show up."""
        f = tmp_path / "model.3mf"
        _make_3mf(
            f,
            plates=[
                (
                    1,
                    [
                        {"id": "1", "type": "PLA", "color": "#FFFFFF", "used_g": "10.0"},
                        {"id": "2", "type": "ABS", "color": "#FF0000", "used_g": "0"},
                        {"id": "3", "type": "PETG", "color": "#00FF00", "used_g": "-1"},
                    ],
                )
            ],
        )
        out = extract_filament_requirements(f, plate_id=1)
        assert [r["slot_id"] for r in out] == [1]

    def test_filters_to_requested_plate(self, tmp_path: Path):
        f = tmp_path / "multi.3mf"
        _make_3mf(
            f,
            plates=[
                (1, [{"id": "1", "type": "PLA", "color": "#FFF", "used_g": "5"}]),
                (2, [{"id": "1", "type": "PETG", "color": "#000", "used_g": "5"}]),
            ],
        )
        assert extract_filament_requirements(f, plate_id=1)[0]["type"] == "PLA"
        assert extract_filament_requirements(f, plate_id=2)[0]["type"] == "PETG"

    def test_no_plate_id_walks_flat_filaments(self, tmp_path: Path):
        """When the slice_info.config has no plate wrapper (some older
        Studio versions), we still pick up flat ``./filament`` children."""
        f = tmp_path / "flat.3mf"
        _make_3mf(
            f,
            flat_filaments=[{"id": "1", "type": "PLA", "color": "#FFF", "used_g": "5"}],
        )
        out = extract_filament_requirements(f, plate_id=None)
        assert len(out) == 1
        assert out[0]["type"] == "PLA"

    def test_returns_empty_list_for_unparseable_file(self, tmp_path: Path):
        f = tmp_path / "bad.3mf"
        f.write_bytes(b"not a zip")
        assert extract_filament_requirements(f, plate_id=1) == []

    def test_returns_empty_list_for_missing_file(self, tmp_path: Path):
        assert extract_filament_requirements(tmp_path / "nope.3mf", plate_id=1) == []

    def test_returns_empty_list_when_slice_info_missing(self, tmp_path: Path):
        """3MF without `Metadata/slice_info.config` (e.g. a model-only
        export) must degrade gracefully."""
        f = tmp_path / "no-config.3mf"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
        assert extract_filament_requirements(f, plate_id=1) == []

    def test_results_are_sorted_by_slot_id(self, tmp_path: Path):
        f = tmp_path / "unordered.3mf"
        _make_3mf(
            f,
            plates=[
                (
                    1,
                    [
                        {"id": "3", "type": "PLA", "color": "#FFF", "used_g": "1"},
                        {"id": "1", "type": "PLA", "color": "#000", "used_g": "1"},
                        {"id": "2", "type": "PLA", "color": "#F00", "used_g": "1"},
                    ],
                )
            ],
        )
        out = extract_filament_requirements(f, plate_id=1)
        assert [r["slot_id"] for r in out] == [1, 2, 3]
