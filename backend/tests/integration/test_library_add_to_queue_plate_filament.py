"""A multi-plate file queued without a plate is matched on the plate that prints (PL-C6).

Queued without a plate, the file prints its default plate. The scheduler was
nevertheless told every plate's filament, so an X1C with only plate 1's PLA
loaded never qualified because plate 2 needs PETG-CF; and with the type list
narrowed alone, the AMS mapping and the unmatched-filament check still read
every plate. The plate that prints is now stored on the job.
"""

import json
import zipfile

import pytest
from sqlalchemy import select

from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _two_plate_3mf(path):
    plate = (
        "<plate><metadata key='index' value='{n}'/><filament id='{n}' type='{t}' color='#FFFFFF' used_g='5'/></plate>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            "<?xml version='1.0'?><config>" + plate.format(n=1, t="PLA") + plate.format(n=2, t="PETG-CF") + "</config>",
        )
        zf.writestr("Metadata/plate_1.gcode", "; plate 1")
        zf.writestr("Metadata/plate_2.gcode", "; plate 2")


async def _library_file(db_session, tmp_path, monkeypatch) -> LibraryFile:
    from backend.app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    _two_plate_3mf(tmp_path / "two.gcode.3mf")
    lib_file = LibraryFile(
        filename="two.gcode.3mf",
        file_path="two.gcode.3mf",
        file_type="3mf",
        file_size=1,
        file_hash="two-plates",
        source_type="uploaded",
        file_metadata={"sliced_for_model": "X1C"},
    )
    db_session.add(lib_file)
    await db_session.commit()
    return lib_file


async def _requirements(db_session, item) -> list[str]:
    from backend.app.services.print_scheduler import PrintScheduler

    return [r["type"] for r in await PrintScheduler()._get_filament_requirements(db_session, item)]


@pytest.mark.parametrize("model", ["X1C", "x1c"])
async def test_a_bulk_add_from_the_library(async_client, db_session, printer_factory, tmp_path, monkeypatch, model):
    """In lower case too, as the scheduler matches a model (Q-U8)."""
    await printer_factory(model="X1C")
    lib_file = await _library_file(db_session, tmp_path, monkeypatch)

    response = await async_client.post(
        "/api/v1/library/files/add-to-queue", json={"file_ids": [lib_file.id], "target_model": model}
    )

    assert response.status_code == 200, response.text
    item = await db_session.scalar(select(PrintQueueItem).where(PrintQueueItem.library_file_id == lib_file.id))
    assert (item.plate_id, item.target_model, json.loads(item.required_filament_types)) == (1, "X1C", ["PLA"])
    assert await _requirements(db_session, item) == ["PLA"]


async def test_post_queue(async_client, db_session, printer_factory, tmp_path, monkeypatch):
    await printer_factory(model="X1C")
    lib_file = await _library_file(db_session, tmp_path, monkeypatch)

    response = await async_client.post("/api/v1/queue/", json={"library_file_id": lib_file.id, "target_model": "X1C"})

    assert response.status_code == 200, response.text
    item = await db_session.get(PrintQueueItem, response.json()["id"])
    assert (item.plate_id, json.loads(item.required_filament_types)) == (1, ["PLA"])
    assert await _requirements(db_session, item) == ["PLA"]


async def test_a_plate_named_by_the_caller_stands(async_client, db_session, printer_factory, tmp_path, monkeypatch):
    await printer_factory(model="X1C")
    lib_file = await _library_file(db_session, tmp_path, monkeypatch)

    response = await async_client.post(
        "/api/v1/queue/", json={"library_file_id": lib_file.id, "target_model": "X1C", "plate_id": 2}
    )

    item = await db_session.get(PrintQueueItem, response.json()["id"])
    assert (item.plate_id, json.loads(item.required_filament_types)) == (2, ["PETG-CF"])


async def test_a_single_plate_file_keeps_no_plate(async_client, db_session, printer_factory, tmp_path, monkeypatch):
    """Nothing to choose between, so the job stays as it always was."""
    await printer_factory(model="X1C")
    lib_file = await _library_file(db_session, tmp_path, monkeypatch)
    with zipfile.ZipFile(tmp_path / "two.gcode.3mf", "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            "<?xml version='1.0'?><config><plate><metadata key='index' value='1'/>"
            "<filament id='1' type='PLA' color='#FFFFFF' used_g='5'/></plate></config>",
        )
        zf.writestr("Metadata/plate_1.gcode", "; plate 1")

    response = await async_client.post("/api/v1/queue/", json={"library_file_id": lib_file.id, "target_model": "X1C"})

    item = await db_session.get(PrintQueueItem, response.json()["id"])
    assert item.plate_id is None
