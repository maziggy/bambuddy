"""The plates endpoints must surface the designer's changed settings (#2622).

Parsing is covered in ``unit/test_design_settings.py``. What is asserted here is
the wiring: SliceModal reads ``design_overrides`` off the plates response, so a
correct parser that never reaches the payload is a feature that silently does
nothing.
"""

import json
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient


def _designed_3mf(path: Path, *, with_deviations: bool = True) -> None:
    """A Bambu-style project 3MF, optionally carrying designer deviations."""
    config = {
        "print_settings_id": "0.20mm Standard @BBL A1",
        "printer_settings_id": "Bambu Lab A1 0.4 nozzle",
        "filament_settings_id": ["Bambu PLA Basic @BBL A1"],
        "wall_loops": "5",
        "outer_wall_speed": "200",
        "machine_start_gcode": "G28 ; designer printer",
        "different_settings_to_system": (
            ["wall_loops;outer_wall_speed", "", "machine_start_gcode"] if with_deviations else ["", "", ""]
        ),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/plate_1.gcode", "G0\n")
        zf.writestr("Metadata/project_settings.config", json.dumps(config))


@pytest.fixture
def _patch_base_dir(monkeypatch, tmp_path):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    return tmp_path


class TestArchivePlatesDesignOverrides:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_the_process_deviations_with_classification(
        self, async_client: AsyncClient, archive_factory, printer_factory, _patch_base_dir
    ):
        _designed_3mf(_patch_base_dir / "designed.3mf")
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="designed.3mf", file_path="designed.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/plates")

        assert response.status_code == 200
        overrides = response.json()["design_overrides"]
        assert [o["key"] for o in overrides] == ["outer_wall_speed", "wall_loops"]
        by_key = {o["key"]: o for o in overrides}
        assert by_key["wall_loops"] == {"key": "wall_loops", "value": "5", "printer_coupled": False}
        assert by_key["outer_wall_speed"]["printer_coupled"] is True
        # The printer slot must never leak into the process list.
        assert "machine_start_gcode" not in by_key

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_for_a_file_that_changes_nothing(
        self, async_client: AsyncClient, archive_factory, printer_factory, _patch_base_dir
    ):
        _designed_3mf(_patch_base_dir / "stock.3mf", with_deviations=False)
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="stock.3mf", file_path="stock.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/plates")

        assert response.status_code == 200
        assert response.json()["design_overrides"] == []


class TestLibraryPlatesDesignOverrides:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_the_process_deviations(self, async_client: AsyncClient, db_session, tmp_path):
        from backend.app.models.library import LibraryFile

        path = tmp_path / "designed.3mf"
        _designed_3mf(path)
        lib_file = LibraryFile(
            filename="designed.3mf",
            file_path=str(path),
            file_type="3mf",
            file_size=path.stat().st_size,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        response = await async_client.get(f"/api/v1/library/files/{lib_file.id}/plates")

        assert response.status_code == 200
        assert [o["key"] for o in response.json()["design_overrides"]] == ["outer_wall_speed", "wall_loops"]
