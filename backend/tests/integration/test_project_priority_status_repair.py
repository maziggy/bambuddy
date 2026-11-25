"""Projects stored with an unknown priority or status get a valid one at start.

Older builds stored a file-imported project without a priority as ``0`` and
took any priority on create and any status on import. The project editor sends
both back on every save and refused them, so such a project could not be
edited at all, not even its name.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import _migrate_project_priority_status
from backend.app.models.project import Project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

ROWS = [
    # (priority, status) as stored -> as repaired
    (("0", "active"), ("normal", "active")),
    (("whenever", "done"), ("normal", "active")),
    (("urgent", "archived"), ("urgent", "archived")),
    (("low", "completed"), ("low", "completed")),
]


async def _seed(session: AsyncSession) -> list[int]:
    ids = []
    for (priority, status), _ in ROWS:
        project = Project(name=f"{priority}/{status}")
        session.add(project)
        await session.flush()
        # Raw UPDATE: the API no longer stores such values.
        await session.execute(
            text("UPDATE projects SET priority = :p, status = :s WHERE id = :id"),
            {"p": priority, "s": status, "id": project.id},
        )
        ids.append(project.id)
    await session.commit()
    return ids


async def _repair(session: AsyncSession) -> None:
    await _migrate_project_priority_status(await session.connection())
    await session.commit()


async def _stored(session: AsyncSession, ids: list[int]) -> list[tuple[str, str]]:
    session.expire_all()
    rows = (
        await session.execute(select(Project.id, Project.priority, Project.status).where(Project.id.in_(ids)))
    ).all()
    by_id = {row[0]: (row[1], row[2]) for row in rows}
    return [by_id[i] for i in ids]


async def test_unknown_values_are_repaired_and_valid_ones_kept(db_session: AsyncSession):
    ids = await _seed(db_session)

    await _repair(db_session)

    assert await _stored(db_session, ids) == [expected for _, expected in ROWS]


async def test_a_second_run_changes_nothing(db_session: AsyncSession):
    ids = await _seed(db_session)
    await _repair(db_session)

    await _repair(db_session)

    assert await _stored(db_session, ids) == [expected for _, expected in ROWS]


async def test_a_repaired_project_can_be_edited_again(async_client: AsyncClient, db_session: AsyncSession):
    project_id = (await _seed(db_session))[0]
    project = (await async_client.get(f"/api/v1/projects/{project_id}")).json()
    # What the editor sends back on save: the stored priority and status.
    edit = {"name": "Renamed", "priority": project["priority"], "status": project["status"]}
    assert (await async_client.patch(f"/api/v1/projects/{project_id}", json=edit)).status_code == 400

    await _repair(db_session)
    project = (await async_client.get(f"/api/v1/projects/{project_id}")).json()
    edit = {"name": "Renamed", "priority": project["priority"], "status": project["status"]}
    response = await async_client.patch(f"/api/v1/projects/{project_id}", json=edit)

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed"
