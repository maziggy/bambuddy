"""A new project gets a priority and status the project editor accepts.

A file without a priority used to get the number 0: refused by PostgreSQL (a
text column) and stored as a priority the editor itself refuses on SQLite.
Creating a project stored any priority, and importing one any status (an
over-long value failed on PostgreSQL, any other one the editor refuses).
"""

from __future__ import annotations

import io
import json

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _import_file(client: AsyncClient, data: dict):
    files = {"file": ("project.json", io.BytesIO(json.dumps(data).encode()), "application/json")}
    return await client.post("/api/v1/projects/import/file", files=files)


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, "normal"), ("high", "high"), ("urgent", "urgent"), (0, "normal"), ("whenever", "normal")],
)
async def test_a_file_import_gets_a_valid_priority(async_client: AsyncClient, given, expected):
    data = {"name": "Imported"} if given is None else {"name": "Imported", "priority": given}

    response = await _import_file(async_client, data)

    assert response.status_code == 200, response.text
    assert response.json()["priority"] == expected


@pytest.mark.parametrize(("given", "expected"), [("low", "low"), ("whenever", "normal")])
async def test_a_json_import_gets_a_valid_priority(async_client: AsyncClient, given, expected):
    response = await async_client.post("/api/v1/projects/import", json={"name": "Imported", "priority": given})

    assert response.status_code == 200, response.text
    assert response.json()["priority"] == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, "active"), ("completed", "completed"), ("archived", "archived"), ("done", "active"), (0, "active")],
)
async def test_a_file_import_gets_a_valid_status(async_client: AsyncClient, given, expected):
    response = await _import_file(async_client, {"name": "Imported", "status": given})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == expected


async def test_a_file_import_without_a_status_is_active(async_client: AsyncClient):
    response = await _import_file(async_client, {"name": "Imported"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


@pytest.mark.parametrize(("given", "expected"), [("archived", "archived"), ("x" * 40, "active")])
async def test_a_json_import_gets_a_valid_status(async_client: AsyncClient, given, expected):
    response = await async_client.post("/api/v1/projects/import", json={"name": "Imported", "status": given})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == expected


@pytest.mark.parametrize("priority", ["whenever", "x" * 40, ""])
async def test_creating_a_project_refuses_an_unknown_priority(async_client: AsyncClient, priority):
    response = await async_client.post("/api/v1/projects/", json={"name": "New", "priority": priority})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Invalid priority"
    listed = await async_client.get("/api/v1/projects/")
    assert [p["name"] for p in listed.json()] == []


@pytest.mark.parametrize("priority", ["low", "normal", "high", "urgent"])
async def test_creating_a_project_keeps_a_known_priority(async_client: AsyncClient, priority):
    response = await async_client.post("/api/v1/projects/", json={"name": "New", "priority": priority})

    assert response.status_code == 200, response.text
    assert response.json()["priority"] == priority
