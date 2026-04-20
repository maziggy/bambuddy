"""Integration tests for the macro API routes."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.core.config import settings


@pytest.fixture(autouse=True)
def use_temp_macros_dir(tmp_path):
    """Redirect macro file storage to a temp directory for each test."""
    settings.macros_dir = tmp_path / "macros"
    yield
    # Cleanup is handled by tmp_path fixture


@pytest.mark.asyncio
async def test_create_macro(async_client, tmp_path):
    resp = await async_client.post(
        "/api/v1/macros",
        json={
            "name": "test_create",
            "script": "G28",
            "trigger_type": "manual",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test_create"
    assert data["script"] == "G28"
    assert data["trigger_type"] == "manual"
    assert "file_path" in data
    # Verify file was created on disk
    assert (settings.macros_dir / data["file_path"]).exists()


@pytest.mark.asyncio
async def test_get_macro_returns_script(async_client):
    create_resp = await async_client.post(
        "/api/v1/macros",
        json={"name": "test_get", "script": "G0 X10", "trigger_type": "manual"},
    )
    assert create_resp.status_code == 200
    macro_id = create_resp.json()["id"]

    get_resp = await async_client.get(f"/api/v1/macros/{macro_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["script"] == "G0 X10"


@pytest.mark.asyncio
async def test_update_macro_script(async_client):
    create_resp = await async_client.post(
        "/api/v1/macros",
        json={"name": "test_update", "script": "G28", "trigger_type": "manual"},
    )
    macro_id = create_resp.json()["id"]
    file_path = create_resp.json()["file_path"]

    update_resp = await async_client.put(
        f"/api/v1/macros/{macro_id}",
        json={"script": "G0 X50 Y50"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["script"] == "G0 X50 Y50"
    # Same file path (in-place update)
    assert update_resp.json()["file_path"] == file_path


@pytest.mark.asyncio
async def test_delete_macro_removes_file(async_client):
    create_resp = await async_client.post(
        "/api/v1/macros",
        json={"name": "test_delete", "script": "G28", "trigger_type": "manual"},
    )
    macro_id = create_resp.json()["id"]
    file_path = create_resp.json()["file_path"]
    full_path = settings.macros_dir / file_path

    assert full_path.exists()

    del_resp = await async_client.delete(f"/api/v1/macros/{macro_id}")
    assert del_resp.status_code == 200
    assert not full_path.exists()

    get_resp = await async_client.get(f"/api/v1/macros/{macro_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_run_macro_returns_run_id(async_client):
    create_resp = await async_client.post(
        "/api/v1/macros",
        json={"name": "test_run", "script": "G28", "trigger_type": "manual"},
    )
    macro_id = create_resp.json()["id"]

    with patch("backend.app.api.routes.macros.asyncio.create_task"):
        run_resp = await async_client.post(
            f"/api/v1/macros/{macro_id}/run",
            json={},
        )
    assert run_resp.status_code == 200
    data = run_resp.json()
    assert "id" in data
    assert data["macro_id"] == macro_id


@pytest.mark.asyncio
async def test_get_gcode_whitelist(async_client):
    resp = await async_client.get("/api/v1/macros/gcode-whitelist")
    assert resp.status_code == 200
    whitelist = resp.json()
    assert isinstance(whitelist, list)
    assert "G28" in whitelist


@pytest.mark.asyncio
async def test_list_macros(async_client):
    await async_client.post(
        "/api/v1/macros",
        json={"name": "list_test_a", "script": "G28", "trigger_type": "manual"},
    )
    await async_client.post(
        "/api/v1/macros",
        json={"name": "list_test_b", "script": "G0 X0", "trigger_type": "manual"},
    )

    resp = await async_client.get("/api/v1/macros")
    assert resp.status_code == 200
    names = [m["name"] for m in resp.json()]
    assert "list_test_a" in names
    assert "list_test_b" in names


@pytest.mark.asyncio
async def test_schedule_macro_requires_cron(async_client):
    resp = await async_client.post(
        "/api/v1/macros",
        json={"name": "sched_no_cron", "script": "G28", "trigger_type": "schedule"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_schedule_macro_invalid_cron(async_client):
    resp = await async_client.post(
        "/api/v1/macros",
        json={
            "name": "sched_bad_cron",
            "script": "G28",
            "trigger_type": "schedule",
            "cron_expression": "not a cron",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_name_rejected(async_client):
    await async_client.post(
        "/api/v1/macros",
        json={"name": "unique_macro", "script": "G28", "trigger_type": "manual"},
    )
    resp2 = await async_client.post(
        "/api/v1/macros",
        json={"name": "unique_macro", "script": "G0 X0", "trigger_type": "manual"},
    )
    assert resp2.status_code == 409
