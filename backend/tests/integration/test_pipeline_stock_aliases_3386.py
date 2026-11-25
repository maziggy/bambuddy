"""Run with pipeline maps hidden Standard refs for the right user (#3386).

The preset listing hides a Standard preset behind its Bambu Cloud stock twin,
and a pipeline run slices with that twin, as the Slice dialog does. The twin
is resolved for the user who resolves the run's cloud presets, which is not
the run's owner when a key without the cloud scope starts it.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.library import LibraryFile
from backend.app.models.pipeline_run import PipelineRun
from backend.app.models.slicer_pipeline import SlicerPipeline
from backend.app.models.user import User
from backend.app.schemas.slicer import PresetRef

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _Stop(Exception):
    """Ends the run once the slice request has been captured."""


async def _run_pipeline(db_session, test_engine, tmp_path, *, creator_user_id, created_by):
    from backend.app.api.routes.pipeline_runs import _make_orchestration_callable

    pipeline = SlicerPipeline(
        name="stock presets",
        printer_preset_source="standard",
        printer_preset_id="Bambu Lab X1 Carbon 0.4 nozzle",
        process_preset_source="standard",
        process_preset_id="0.20mm Standard @BBL X1C",
        filament_presets_json='[{"source": "standard", "id": "Bambu PLA Basic @BBL X1C"}]',
        bed_type="Textured PEI Plate",
    )
    source = LibraryFile(filename="cube.stl", file_path="library/cube.stl", file_type="stl", file_size=1)
    db_session.add_all([pipeline, source])
    await db_session.commit()
    run = PipelineRun(pipeline_id=pipeline.id, source_library_file_id=source.id, copies=1, created_by=created_by)
    db_session.add(run)
    await db_session.commit()
    model = tmp_path / "cube.stl"
    model.write_bytes(b"model")

    seen_users = []

    async def aliases_for(_db, user):
        # Only a signed-in user has a cloud list, so only they get aliases.
        seen_users.append(user.id if user is not None else None)
        if user is None:
            return {}
        return {
            "standard:Bambu Lab X1 Carbon 0.4 nozzle": PresetRef(source="cloud", id="GM001"),
            "standard:0.20mm Standard @BBL X1C": PresetRef(source="cloud", id="GP004"),
            "standard:Bambu PLA Basic @BBL X1C": PresetRef(source="cloud", id="GFSA00"),
        }

    sliced = AsyncMock(side_effect=_Stop())
    orchestrate = _make_orchestration_callable(
        run_id=run.id,
        pipeline_id=pipeline.id,
        src_kind="library_file",
        src_id=source.id,
        src_filename="cube.stl",
        src_path=Path(model),
        creator_user_id=creator_user_id,
        copies=1,
    )
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.api.routes.pipeline_runs.async_session", maker),
        patch("backend.app.api.routes.pipeline_runs.load_stock_aliases", new=aliases_for),
        patch("backend.app.api.routes.library.slice_and_persist", new=sliced),
        patch("backend.app.api.routes.pipeline_runs._publish_run_event", new=AsyncMock()),
        pytest.raises(_Stop),
    ):
        await orchestrate(1)

    request = sliced.call_args.kwargs["request"]
    return request, seen_users


async def _owner(db_session) -> User:
    owner = User(username="pipeline-owner", password_hash="x", role="user")
    db_session.add(owner)
    await db_session.commit()
    return owner


async def test_a_run_without_a_cloud_user_slices_the_stored_standard_refs(db_session, test_engine, tmp_path):
    """A key without the cloud scope: the owner has a cloud list, but no one
    resolves cloud presets for this run, so mapping to the cloud twins would
    get the slice refused."""
    owner = await _owner(db_session)
    request, seen_users = await _run_pipeline(
        db_session, test_engine, tmp_path, creator_user_id=None, created_by=owner.id
    )

    assert seen_users == [None]
    assert request.printer_preset == PresetRef(source="standard", id="Bambu Lab X1 Carbon 0.4 nozzle")
    assert request.process_preset == PresetRef(source="standard", id="0.20mm Standard @BBL X1C")
    assert request.filament_presets == [PresetRef(source="standard", id="Bambu PLA Basic @BBL X1C")]


async def test_a_run_with_a_cloud_user_slices_the_cloud_twins(db_session, test_engine, tmp_path):
    owner = await _owner(db_session)
    request, seen_users = await _run_pipeline(
        db_session, test_engine, tmp_path, creator_user_id=owner.id, created_by=owner.id
    )

    assert seen_users == [owner.id]
    assert request.printer_preset == PresetRef(source="cloud", id="GM001")
    assert request.process_preset == PresetRef(source="cloud", id="GP004")
    assert request.filament_presets == [PresetRef(source="cloud", id="GFSA00")]
