"""Project import and export: linked folders and their files."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

STL = b"solid probe\nendsolid probe\n"


def _zip(folder: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("project.json", json.dumps({"name": "P", "linked_folders": [{"name": folder}]}))
        for name, content in files.items():
            zf.writestr(f"files/{folder}/{name}", content)
    return buffer.getvalue()


async def _import_zip(client: AsyncClient, data: bytes, headers=None):
    files = {"file": ("p.zip", io.BytesIO(data), "application/zip")}
    return await client.post("/api/v1/projects/import/file", files=files, headers=headers or {})


async def _folder_files(client: AsyncClient, folder_id: int, headers=None) -> list[dict]:
    listed = await client.get("/api/v1/library/files", params={"folder_id": folder_id}, headers=headers or {})
    return listed.json()


async def test_an_imported_file_can_be_downloaded(async_client: AsyncClient):
    imported = await _import_zip(async_client, _zip("Models", {"part.stl": STL}))
    assert imported.status_code == 200, imported.text
    folders = (await async_client.get("/api/v1/library/folders/by-project/" + str(imported.json()["id"]))).json()

    files = await _folder_files(async_client, folders[0]["id"])
    downloaded = await async_client.get(f"/api/v1/library/files/{files[0]['id']}/download")

    assert [f["filename"] for f in files] == ["part.stl"]
    assert (downloaded.status_code, downloaded.content) == (200, STL)


async def test_an_export_carries_the_folder_files(async_client: AsyncClient):
    project = (await async_client.post("/api/v1/projects/", json={"name": "P"})).json()
    folder = (await async_client.post("/api/v1/library/folders/", json={"name": "Parts"})).json()
    await async_client.put(f"/api/v1/library/folders/{folder['id']}", json={"project_id": project["id"]})
    uploaded = await async_client.post(
        "/api/v1/library/files",
        params={"folder_id": folder["id"], "generate_stl_thumbnails": False},
        files={"file": ("bracket.stl", io.BytesIO(STL), "model/stl")},
    )
    assert uploaded.status_code == 200, uploaded.text

    exported = await async_client.get(f"/api/v1/projects/{project['id']}/export")

    with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
        assert zf.read("files/Parts/bracket.stl") == STL


PERMS = ["projects:read", "projects:create", "library:read_own", "library:upload", "library:update_own"]


async def _users(client: AsyncClient):
    from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

    token = await _admin_token(client)
    group = await _group(client, token, "makers", permissions=PERMS)
    a = _auth((await _user(client, token, "anna", [group]))[0])
    b = _auth((await _user(client, token, "ben", [group]))[0])
    return _auth(token), a, b


async def test_an_import_leaves_another_user_folder_of_the_same_name_alone(async_client: AsyncClient):
    admin, a, b = await _users(async_client)
    bens = (await async_client.post("/api/v1/library/folders/", headers=b, json={"name": "Models"})).json()

    from_zip = await _import_zip(async_client, _zip("Models", {"part.stl": STL}), headers=a)
    from_json = await async_client.post(
        "/api/v1/projects/import", headers=a, json={"name": "J", "linked_folders": [{"name": "Models"}]}
    )

    assert (from_zip.status_code, from_json.status_code) == (200, 200)
    kept = (await async_client.get(f"/api/v1/library/folders/{bens['id']}", headers=admin)).json()
    assert kept["project_id"] is None
    assert await _folder_files(async_client, bens["id"], headers=admin) == []
    linked = (
        await async_client.get(f"/api/v1/library/folders/by-project/{from_json.json()['id']}", headers=admin)
    ).json()
    assert [f["name"] for f in linked] == ["Models"]
    assert linked[0]["id"] != bens["id"]


async def test_an_import_links_an_existing_folder_only_with_library_update_all(async_client: AsyncClient):
    admin, a, b = await _users(async_client)
    annas = (await async_client.post("/api/v1/library/folders/", headers=a, json={"name": "Mine"})).json()
    bens = (await async_client.post("/api/v1/library/folders/", headers=b, json={"name": "Shop"})).json()
    await async_client.post("/api/v1/library/folders/", headers=a, json={"name": "Shop"})

    own = await async_client.post(
        "/api/v1/projects/import", headers=a, json={"name": "A", "linked_folders": [{"name": "Mine"}]}
    )
    as_admin = await async_client.post(
        "/api/v1/projects/import", headers=admin, json={"name": "B", "linked_folders": [{"name": "Shop"}]}
    )

    assert (own.status_code, as_admin.status_code) == (200, 200)
    mine = (await async_client.get(f"/api/v1/library/folders/{annas['id']}", headers=admin)).json()
    shop = (await async_client.get(f"/api/v1/library/folders/{bens['id']}", headers=admin)).json()
    # Linking a folder takes library:update_all, her own included.
    assert mine["project_id"] is None
    assert shop["project_id"] == as_admin.json()["id"]


async def test_two_imports_into_same_named_folders_keep_their_own_files(async_client: AsyncClient):
    admin, a, b = await _users(async_client)
    first = await _import_zip(async_client, _zip("Models", {"part.stl": b"solid a\nendsolid a\n"}), headers=a)
    second = await _import_zip(async_client, _zip("Models", {"part.stl": b"solid b\nendsolid b\n"}), headers=b)

    contents = []
    for imported, who in ((first, a), (second, b)):
        folder = (
            await async_client.get(f"/api/v1/library/folders/by-project/{imported.json()['id']}", headers=admin)
        ).json()[0]
        file = (await _folder_files(async_client, folder["id"], headers=who))[0]
        contents.append((await async_client.get(f"/api/v1/library/files/{file['id']}/download", headers=who)).content)

    assert contents == [b"solid a\nendsolid a\n", b"solid b\nendsolid b\n"]


async def test_an_export_still_carries_files_imported_by_older_versions(async_client: AsyncClient, db_session):
    from backend.app.api.routes.library import get_library_dir
    from backend.app.models.library import LibraryFile

    project = (await async_client.post("/api/v1/projects/", json={"name": "Old"})).json()
    folder = (await async_client.post("/api/v1/library/folders/", json={"name": "OldImport"})).json()
    await async_client.put(f"/api/v1/library/folders/{folder['id']}", json={"project_id": project["id"]})
    disk = get_library_dir() / "OldImport" / "legacy.stl"
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(STL)
    db_session.add(
        LibraryFile(
            folder_id=folder["id"],
            filename="legacy.stl",
            file_path="OldImport/legacy.stl",
            file_type="model",
            file_size=1,
        )
    )
    await db_session.commit()
    try:
        exported = await async_client.get(f"/api/v1/projects/{project['id']}/export")
        with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
            assert zf.read("files/OldImport/legacy.stl") == STL
    finally:
        disk.unlink()
        disk.parent.rmdir()


async def test_an_export_carries_only_files_the_caller_may_open(async_client: AsyncClient):
    from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

    token = await _admin_token(async_client)
    admin = _auth(token)
    own = await _group(async_client, token, "own", permissions=["projects:read", "library:read_own", "library:upload"])
    none = await _group(async_client, token, "none", permissions=["projects:read"])
    reader = _auth((await _user(async_client, token, "reader", [own]))[0])
    outsider = _auth((await _user(async_client, token, "outsider", [none]))[0])
    project = (await async_client.post("/api/v1/projects/", headers=admin, json={"name": "P"})).json()
    folder = (await async_client.post("/api/v1/library/folders/", headers=admin, json={"name": "Private"})).json()
    await async_client.put(f"/api/v1/library/folders/{folder['id']}", headers=admin, json={"project_id": project["id"]})
    shared = (await async_client.post("/api/v1/library/folders/", headers=admin, json={"name": "Shared"})).json()
    await async_client.put(
        f"/api/v1/library/folders/{shared['id']}", headers=admin, json={"project_id": project["id"], "shared": True}
    )
    for who, folder_id, name in (
        (admin, folder["id"], "secret.stl"),
        (admin, shared["id"], "theirs.stl"),
        (reader, shared["id"], "mine.stl"),
    ):
        uploaded = await async_client.post(
            "/api/v1/library/files",
            headers=who,
            params={"folder_id": folder_id, "generate_stl_thumbnails": False},
            files={"file": (name, io.BytesIO(STL), "model/stl")},
        )
        assert uploaded.status_code == 200, uploaded.text

    def contents(response) -> tuple[list[str], list[str]]:
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            folders = [f["name"] for f in json.loads(zf.read("project.json"))["linked_folders"]]
            return folders, [n for n in zf.namelist() if n.startswith("files/")]

    exported = {
        name: contents(await async_client.get(f"/api/v1/projects/{project['id']}/export", headers=who))
        for name, who in (("admin", admin), ("reader", reader), ("outsider", outsider))
    }

    assert exported["admin"] == (
        ["Private", "Shared"],
        ["files/Private/secret.stl", "files/Shared/mine.stl", "files/Shared/theirs.stl"],
    )
    assert exported["reader"] == (["Shared"], ["files/Shared/mine.stl"])
    as_json = await async_client.get(
        f"/api/v1/projects/{project['id']}/export", headers=reader, params={"format": "json"}
    )
    assert [(f["name"], [x["filename"] for x in f["files"]]) for f in as_json.json()["linked_folders"]] == [
        ("Shared", ["mine.stl"])
    ]
    assert exported["outsider"] == ([], [])
