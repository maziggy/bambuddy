"""Preheat heats the bed for the plate the queued job prints (PL-C7).

The archive stores one bed temperature, the highest any filament of the
project asks for, and preheat and keep-warm used it for every job: a PLA plate
of a project that also holds PETG was preheated to the PETG temperature. They
now read the job's own plate from the archive's file.
"""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services.archive import ThreeMFParser
from backend.app.services.print_scheduler import _queued_plate_bed_temperature
from backend.app.utils.threemf_tools import plate_bed_temperature

PLA, PETG_PLATE_FILAMENTS = [1], [2, 3]


def _3mf(path: Path, plates: dict[int, list[int]], gcode: tuple[int, ...] = ()) -> Path:
    """Plate N prints the filaments listed for it (1-based ids), PLA at 55,
    PLA at 60 and PETG at 70; ``gcode`` are the plates sliced."""
    plate_xml = "".join(
        f"<plate><metadata key='index' value='{index}'/>"
        + "".join(f"<filament id='{fid}' type='PLA' color='#FFFFFF' used_g='5'/>" for fid in filaments)
        + "</plate>"
        for index, filaments in plates.items()
    )
    settings = {"curr_bed_type": "Textured PEI Plate", "textured_plate_temp": ["55", "60", "70"]}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", f"<?xml version='1.0'?><config>{plate_xml}</config>")
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        zf.writestr("3D/3dmodel.model", "<model/>")
        for index in gcode:
            zf.writestr(f"Metadata/plate_{index}.gcode", "; plate")
    return path


def test_each_plate_has_its_own_filaments_temperature(tmp_path):
    path = _3mf(tmp_path / "a.3mf", {1: PLA, 2: PETG_PLATE_FILAMENTS})

    assert plate_bed_temperature(path, 1) == 55
    assert plate_bed_temperature(path, 2) == 70


def test_without_a_plate_the_one_dispatched_decides(tmp_path):
    """A job without a plate prints the file's default (lowest sliced) plate."""
    path = _3mf(tmp_path / "a.3mf", {1: PLA, 2: PETG_PLATE_FILAMENTS}, gcode=(1, 2))

    assert plate_bed_temperature(path, None) == 55
    assert plate_bed_temperature(path, 0) == 55


def test_a_plate_whose_filaments_are_unknown_takes_the_highest(tmp_path):
    assert plate_bed_temperature(_3mf(tmp_path / "a.3mf", {1: [], 2: []}), None) == 70


def test_an_unreadable_file_is_none(tmp_path):
    (tmp_path / "broken.3mf").write_bytes(b"not a zip")

    assert plate_bed_temperature(tmp_path / "broken.3mf", 1) is None
    assert plate_bed_temperature(tmp_path / "missing.3mf", 1) is None


def test_a_damaged_member_is_none(tmp_path):
    """Corrupt deflate data raises zlib.error, which is no OSError: preheat
    runs this on every dispatch, so nothing may escape it."""
    path = tmp_path / "a.3mf"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps({"bed_temperature": ["60"] * 200}))
    raw = bytearray(path.read_bytes())
    start = raw.index(b"PK\x03\x04") + 30 + len("Metadata/project_settings.config")
    raw[start : start + 8] = b"\xff" * 8
    path.write_bytes(bytes(raw))

    assert plate_bed_temperature(path, 1) is None


def test_an_infinite_temperature_is_none(tmp_path):
    path = tmp_path / "a.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps({"bed_temperature": ["inf"]}))

    assert plate_bed_temperature(path, 1) is None


def test_the_archive_keeps_the_projects_highest(tmp_path):
    """The stored value stays the safe upper bound for any plate of it."""
    path = _3mf(tmp_path / "a.3mf", {1: PLA, 2: PETG_PLATE_FILAMENTS})

    assert ThreeMFParser(path, plate_number=1).parse().get("bed_temperature") == 70


@pytest.fixture
def base_dir(monkeypatch, tmp_path) -> Path:
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize(("plate_id", "expected"), [(1, 55), (2, 70)])
async def test_a_job_is_heated_for_its_plate(base_dir, plate_id, expected):
    _3mf(base_dir / "a.3mf", {1: PLA, 2: PETG_PLATE_FILAMENTS})
    archive = SimpleNamespace(file_path="a.3mf", bed_temperature=70)

    assert await _queued_plate_bed_temperature(SimpleNamespace(plate_id=plate_id), archive) == expected


@pytest.mark.asyncio
async def test_an_unreadable_file_falls_back_to_the_stored_value(base_dir):
    archive = SimpleNamespace(file_path="gone.3mf", bed_temperature=65)

    assert await _queued_plate_bed_temperature(SimpleNamespace(plate_id=2), archive) == 65


@pytest.mark.asyncio
async def test_no_archive_is_zero():
    assert await _queued_plate_bed_temperature(SimpleNamespace(plate_id=None), None) == 0


@pytest.mark.asyncio
async def test_preheat_heats_for_the_job_s_plate(base_dir):
    from unittest.mock import AsyncMock, patch

    from backend.app.services.print_scheduler import PrintScheduler
    from backend.tests.unit.test_scheduler_preheat import _ints, _make_client, _make_printer, _make_state

    _3mf(base_dir / "a.3mf", {1: PLA, 2: PETG_PLATE_FILAMENTS})
    scheduler = PrintScheduler()
    item = SimpleNamespace(id=42, plate_id=1, preheat_override="on", preheat_chamber_target_override=0)
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(55.0, 0.0)
        await scheduler._preheat_and_soak(
            AsyncMock(), item, _make_printer("H2D"), SimpleNamespace(file_path="a.3mf", bed_temperature=70)
        )

    client.set_bed_temperature.assert_called_once_with(55)


@pytest.mark.asyncio
async def test_keep_warm_holds_for_the_next_job_s_plate(base_dir):
    from unittest.mock import patch

    from backend.app.services.print_scheduler import PrintScheduler
    from backend.tests.unit.test_scheduler_keep_warm import _make_client, _make_item, _make_state, _run

    _3mf(base_dir / "a.3mf", {1: PLA, 2: PETG_PLATE_FILAMENTS})
    item = _make_item()
    item.plate_id = 2
    item.archive = SimpleNamespace(file_path="a.3mf", bed_temperature=110)
    client = _make_client()

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(PrintScheduler(), items=[item], hold_temp=40)

    client.set_bed_temperature.assert_called_once_with(70)
