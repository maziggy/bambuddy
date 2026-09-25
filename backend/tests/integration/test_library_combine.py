"""Integration tests for POST /library/files/combine (STLs -> one multi-object 3MF)."""

import io
import re
import zipfile

import pytest
import trimesh
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.library import LibraryFile


def _stl(mesh) -> bytes:
    return mesh.export(file_type="stl")


async def _upload(client: AsyncClient, name: str, content: bytes, folder_id: int | None = None) -> int:
    params = {"generate_stl_thumbnails": "false"}
    if folder_id is not None:
        params["folder_id"] = str(folder_id)
    resp = await client.post(
        "/api/v1/library/files",
        files={"file": (name, content, "application/octet-stream")},
        params=params,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _download(client: AsyncClient, file_id: int) -> bytes:
    resp = await client.get(f"/api/v1/library/files/{file_id}/download")
    assert resp.status_code == 200, resp.text
    return resp.content


class TestCombineFiles:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_combines_stls_into_new_3mf(self, async_client: AsyncClient, db_session):
        box_id = await _upload(async_client, "box.stl", _stl(trimesh.creation.box((30, 20, 10))))
        cyl_id = await _upload(async_client, "cyl.stl", _stl(trimesh.creation.cylinder(radius=12, height=25)))

        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": box_id, "copies": 2}, {"file_id": cyl_id}], "filename": "Plate"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["filename"] == "Plate.3mf"
        assert body["file_type"] == "3mf"
        assert body["thumbnail_path"]

        row = (await db_session.execute(select(LibraryFile).where(LibraryFile.id == body["id"]))).scalar_one()
        assert row.source_type == "combined"

        content = await _download(async_client, body["id"])
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            xml = zf.read("3D/3dmodel.model").decode()
        assert re.findall(r'<item objectid="(\d+)"', xml) == ["1", "1", "2"]

        # Sources are left alone.
        for src in (box_id, cyl_id):
            assert (await async_client.get(f"/api/v1/library/files/{src}")).status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_lands_in_requested_folder(self, async_client: AsyncClient, db_session):
        folder = (await async_client.post("/api/v1/library/folders", json={"name": "Combos"})).json()
        box_id = await _upload(async_client, "box.stl", _stl(trimesh.creation.box((10, 10, 10))))
        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": box_id, "copies": 4}], "filename": "four.3mf", "folder_id": folder["id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["filename"] == "four.3mf"
        row = (await db_session.execute(select(LibraryFile).where(LibraryFile.id == resp.json()["id"]))).scalar_one()
        assert row.folder_id == folder["id"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_non_stl_source(self, async_client: AsyncClient):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
        three_mf_id = await _upload(async_client, "project.3mf", buf.getvalue())
        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": three_mf_id}], "filename": "x"},
        )
        assert resp.status_code == 400
        assert "Only STL" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_unknown_file(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": 999999}], "filename": "x"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_unknown_folder(self, async_client: AsyncClient):
        box_id = await _upload(async_client, "box.stl", _stl(trimesh.creation.box((10, 10, 10))))
        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": box_id}], "filename": "x", "folder_id": 999999},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Folder not found"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_invalid_filename(self, async_client: AsyncClient):
        box_id = await _upload(async_client, "box.stl", _stl(trimesh.creation.box((10, 10, 10))))
        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": box_id}], "filename": "a/b"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_too_many_objects(self, async_client: AsyncClient):
        box_id = await _upload(async_client, "box.stl", _stl(trimesh.creation.box((10, 10, 10))))
        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": box_id, "copies": 60}, {"file_id": box_id, "copies": 60}], "filename": "x"},
        )
        assert resp.status_code == 400
        assert "Too many objects" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_empty_mesh(self, async_client: AsyncClient):
        stub_id = await _upload(async_client, "stub.stl", b"solid stub\nendsolid stub\n")
        resp = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": stub_id}], "filename": "x"},
        )
        assert resp.status_code == 400
        assert "stub.stl" in resp.json()["detail"]
