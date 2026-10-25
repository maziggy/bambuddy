"""Pipelines and the bed-type names no slicer knows (#3359).

The Slice modal used to save "Cool Plate (SuperTack)" and "Smooth PEI Plate",
which the slicer CLI slices as Cool Plate. Saved pipelines keep those names.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.library import LibraryFile
from backend.app.models.pipeline_run import PipelineRun
from backend.app.models.slicer_pipeline import SlicerPipeline

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _payload(bed_type: str) -> dict:
    return {
        "name": "SuperTack PLA",
        "printer_preset": {"source": "local", "id": "1"},
        "process_preset": {"source": "local", "id": "2"},
        "filament_presets": [{"source": "local", "id": "3"}],
        "bed_type": bed_type,
    }


async def _saved_pipeline(db_session, bed_type: str) -> SlicerPipeline:
    pipeline = SlicerPipeline(
        name="saved before #3359",
        printer_preset_source="local",
        printer_preset_id="1",
        process_preset_source="local",
        process_preset_id="2",
        filament_presets_json='[{"source": "local", "id": "3"}]',
        bed_type=bed_type,
    )
    db_session.add(pipeline)
    await db_session.commit()
    return pipeline


async def test_saving_the_old_supertack_name_stores_the_slicers_name(async_client: AsyncClient):
    resp = await async_client.post("/api/v1/slicer-pipelines/", json=_payload("Cool Plate (SuperTack)"))

    assert resp.status_code == 201, resp.text
    assert resp.json()["bed_type"] == "Supertack Plate"


async def test_an_unknown_name_is_refused(async_client: AsyncClient):
    created = await async_client.post("/api/v1/slicer-pipelines/", json=_payload("Glass Plate"))
    assert created.status_code == 422

    ok = (await async_client.post("/api/v1/slicer-pipelines/", json=_payload("Cool Plate"))).json()
    updated = await async_client.put(f"/api/v1/slicer-pipelines/{ok['id']}", json={"bed_type": "Glass Plate"})
    assert updated.status_code == 422


async def test_a_pipeline_saved_before_reads_back_with_the_slicers_name(async_client: AsyncClient, db_session):
    pipeline = await _saved_pipeline(db_session, "Smooth PEI Plate")

    resp = await async_client.get("/api/v1/slicer-pipelines/")

    assert resp.status_code == 200
    (listed,) = [p for p in resp.json()["pipelines"] if p["id"] == pipeline.id]
    assert listed["bed_type"] == "High Temp Plate"


async def test_a_run_of_a_pipeline_with_an_unknown_name_fails(db_session, test_engine, tmp_path):
    """Before, building the request sat outside the try and the run stayed in "slicing"."""
    from backend.app.api.routes.pipeline_runs import _make_orchestration_callable

    pipeline = await _saved_pipeline(db_session, "Glass Plate")
    source = LibraryFile(filename="cube.stl", file_path="library/cube.stl", file_type="stl", file_size=1)
    db_session.add(source)
    await db_session.commit()
    run = PipelineRun(pipeline_id=pipeline.id, source_library_file_id=source.id, copies=1)
    db_session.add(run)
    await db_session.commit()
    model = tmp_path / "cube.stl"
    model.write_bytes(b"model")
    sliced = AsyncMock()

    orchestrate = _make_orchestration_callable(
        run_id=run.id,
        pipeline_id=pipeline.id,
        src_kind="library_file",
        src_id=source.id,
        src_filename="cube.stl",
        src_path=Path(model),
        creator_user_id=None,
        copies=1,
    )
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.api.routes.pipeline_runs.async_session", maker),
        patch("backend.app.api.routes.library.slice_and_persist", new=sliced),
        patch("backend.app.api.routes.pipeline_runs._publish_run_event", new=AsyncMock()),
        pytest.raises(HTTPException),
    ):
        await orchestrate(1)

    sliced.assert_not_awaited()
    async with maker() as fresh:
        stored = await fresh.get(PipelineRun, run.id)
    assert stored.status == "failed"
    assert stored.error_message == (
        "Slice failed: The pipeline's build plate 'Glass Plate' is not one the slicers know. "
        "Save the pipeline again from the Slice dialog with another plate."
    )
