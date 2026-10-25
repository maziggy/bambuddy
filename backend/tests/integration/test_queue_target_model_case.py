"""A target model is matched without regard to case, as the scheduler does (Q-U8).

The scheduler finds printers for "x1c" among the X1Cs, but adding or editing a
queue job refused the same name ("No active printers for model: x1c"). The job
stores the printer's own spelling, so everything comparing it exactly agrees.
"""

import pytest
from sqlalchemy import select

from backend.app.models.print_queue import PrintQueueItem

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_adding_a_job_for_a_model_in_lower_case(async_client, db_session, printer_factory, archive_factory):
    printer = await printer_factory(model="X1C")
    archive = await archive_factory(printer.id)

    response = await async_client.post("/api/v1/queue/", json={"archive_id": archive.id, "target_model": "x1c"})

    assert response.status_code == 200, response.text
    item = await db_session.scalar(select(PrintQueueItem).where(PrintQueueItem.id == response.json()["id"]))
    assert item.target_model == "X1C"


async def test_editing_a_job_onto_a_model_in_lower_case(async_client, db_session, printer_factory, archive_factory):
    printer = await printer_factory(model="X1C")
    archive = await archive_factory(printer.id)
    created = await async_client.post("/api/v1/queue/", json={"archive_id": archive.id, "printer_id": printer.id})

    response = await async_client.patch(
        f"/api/v1/queue/{created.json()['id']}", json={"printer_id": None, "target_model": "x1c"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["target_model"] == "X1C"


async def test_a_model_no_active_printer_has_is_still_refused(async_client, printer_factory, archive_factory):
    printer = await printer_factory(model="X1C")
    archive = await archive_factory(printer.id)

    response = await async_client.post("/api/v1/queue/", json={"archive_id": archive.id, "target_model": "p1s"})

    assert response.status_code == 400


@pytest.mark.parametrize(("strategy", "expected"), [("fill_one_first", "pinned"), ("max_parallel", "X1C")])
async def test_a_pipeline_aimed_at_a_model_in_lower_case(db_session, printer_factory, strategy, expected):
    from backend.app.api.routes.pipeline_runs import _pick_assignments
    from backend.app.models.slicer_pipeline import SlicerPipeline

    printer = await printer_factory(model="X1C")
    pipeline = SlicerPipeline(target_kind="printer_class", target_model_class="x1c", fanout_strategy=strategy)

    assignments = await _pick_assignments(db_session, pipeline, 2)

    if expected == "pinned":
        assert assignments == [(printer.id, None)] * 2
    else:
        assert assignments == [(None, "X1C")] * 2
