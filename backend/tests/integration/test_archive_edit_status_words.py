"""The statuses an archive can be given by hand.

The finished outcomes only: completed, failed, aborted and cancelled. An
archive set to 'printing' by hand is closed again at the next reconnect as a
missed completion, with notifications and a Print History entry of its own.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.print_log import PrintLogEntry


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("status", ["completed", "failed", "aborted", "cancelled"])
async def test_a_finished_outcome_is_set_and_mirrored(
    async_client: AsyncClient, archive_factory, printer_factory, db_session, status
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, status="completed" if status != "completed" else "failed")

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"status": status})

    assert response.status_code == 200
    assert response.json()["status"] == status
    run = await db_session.scalar(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
    await db_session.refresh(run)
    assert run.status == status


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("status", ["printing", "archived", "stopped", "bogus"])
async def test_any_other_status_is_refused(
    async_client: AsyncClient, archive_factory, printer_factory, db_session, status
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, status="completed")

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"status": status})

    assert response.status_code == 422
    run = await db_session.scalar(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
    await db_session.refresh(run)
    assert run.status == "completed"
