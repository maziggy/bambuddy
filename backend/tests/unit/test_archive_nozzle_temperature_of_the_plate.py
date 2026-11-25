"""An archive's nozzle temperature is that of the filament its plate prints (PL-C11).

``project_settings.config`` lists one nozzle temperature per filament of the
whole project. Reading the first entry gave a plate that prints only
filament 3 (PETG at 250 °C) the temperature of filament 1 (PLA at 220 °C).
"""

import json
import zipfile
from pathlib import Path

from backend.app.services.archive import ThreeMFParser


def _3mf(path: Path, plates: dict[int, list[int]], unused: tuple[int, ...] = ()) -> Path:
    """A 3MF whose plate N prints the filaments listed for it (1-based ids);
    ``unused`` ones are listed on every plate with 0 g."""
    plate_xml = "".join(
        "<plate>"
        f"<metadata key='index' value='{index}'/>"
        + "".join(f"<filament id='{fid}' type='PLA' color='#FFFFFF' used_g='0'/>" for fid in unused)
        + "".join(f"<filament id='{fid}' type='PLA' color='#FFFFFF' used_g='5'/>" for fid in filaments)
        + "</plate>"
        for index, filaments in plates.items()
    )
    settings = {"nozzle_temperature": ["220", "230", "250"], "nozzle_diameter": ["0.4"]}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", f"<?xml version='1.0'?><config>{plate_xml}</config>")
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        zf.writestr("3D/3dmodel.model", "<model/>")
    return path


def _nozzle_temperature(path: Path, plate_number: int | None = None) -> int | None:
    return ThreeMFParser(path, plate_number=plate_number).parse().get("nozzle_temperature")


def test_a_plate_printing_only_filament_3_has_its_temperature(tmp_path):
    assert _nozzle_temperature(_3mf(tmp_path / "a.3mf", {1: [3]})) == 250


def test_the_printed_plate_of_several_decides(tmp_path):
    path = _3mf(tmp_path / "a.3mf", {1: [1], 2: [2]})

    assert _nozzle_temperature(path, plate_number=2) == 230
    assert _nozzle_temperature(path, plate_number=1) == 220


def test_a_plate_of_several_filaments_takes_the_first_it_uses(tmp_path):
    assert _nozzle_temperature(_3mf(tmp_path / "a.3mf", {1: [3, 2]})) == 230


def test_a_filament_listed_but_not_used_does_not_count(tmp_path):
    assert _nozzle_temperature(_3mf(tmp_path / "a.3mf", {1: [3]}, unused=(1,))) == 250
