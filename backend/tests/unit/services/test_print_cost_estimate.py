from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.services import print_cost_estimate


@pytest.fixture
def library_file(tmp_path):
    path = tmp_path / "queued.gcode.3mf"
    path.write_bytes(b"stub")
    return SimpleNamespace(file_path=str(path), file_metadata={})


@pytest.mark.asyncio
async def test_archive_without_stored_cost_uses_server_default(monkeypatch):
    archive = SimpleNamespace(
        id=7,
        file_path="missing.gcode.3mf",
        plate_id=None,
        filament_used_grams=100.0,
        cost=None,
    )
    monkeypatch.setattr(print_cost_estimate, "default_cost_per_kg", AsyncMock(return_value=20.0))

    cost = await print_cost_estimate.estimate_queue_source_cost(SimpleNamespace(), archive=archive)

    assert cost == 2.0


@pytest.mark.asyncio
async def test_library_estimate_uses_server_default_cost(monkeypatch, library_file):
    monkeypatch.setattr(
        print_cost_estimate.threemf_tools,
        "extract_plate_metadata_from_3mf",
        lambda *_args: SimpleNamespace(
            filament_usage=[
                {"slot_id": 1, "used_g": 100.0},
                {"slot_id": 2, "used_g": 50.0},
            ]
        ),
    )
    monkeypatch.setattr(print_cost_estimate, "default_cost_per_kg", AsyncMock(return_value=20.0))

    cost = await print_cost_estimate.estimate_queue_source_cost(
        SimpleNamespace(),
        library_file=library_file,
        plate_id=1,
    )

    assert cost == 3.0


@pytest.mark.asyncio
async def test_library_estimate_uses_server_spool_assignment_costs(monkeypatch, library_file):
    monkeypatch.setattr(
        print_cost_estimate.threemf_tools,
        "extract_plate_metadata_from_3mf",
        lambda *_args: SimpleNamespace(
            filament_usage=[
                {"slot_id": 1, "used_g": 100.0},
                {"slot_id": 2, "used_g": 50.0},
            ]
        ),
    )
    monkeypatch.setattr(print_cost_estimate, "default_cost_per_kg", AsyncMock(return_value=20.0))

    assignments = [
        SimpleNamespace(ams_id=0, tray_id=0, spool=SimpleNamespace(cost_per_kg=10.0)),
        SimpleNamespace(ams_id=0, tray_id=1, spool=SimpleNamespace(cost_per_kg=30.0)),
    ]
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: assignments))
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    cost = await print_cost_estimate.estimate_queue_source_cost(
        db,
        library_file=library_file,
        plate_id=1,
        ams_mapping=[0, 1],
        printer_id=7,
    )

    assert cost == 2.5


@pytest.mark.asyncio
async def test_missing_server_metadata_does_not_fall_back_to_client_hint(monkeypatch, library_file):
    monkeypatch.setattr(
        print_cost_estimate.threemf_tools,
        "extract_plate_metadata_from_3mf",
        lambda *_args: SimpleNamespace(filament_usage=[]),
    )

    cost = await print_cost_estimate.estimate_queue_source_cost(
        SimpleNamespace(),
        library_file=library_file,
        plate_id=1,
    )

    assert cost is None
