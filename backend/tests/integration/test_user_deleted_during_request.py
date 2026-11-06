"""A request whose user is deleted while it runs saves nothing under their id.

An import downloads for seconds, a ZIP extract or a combine runs for minutes,
and each holds the user it started with. Saved afterwards, that id points at
nobody, and on SQLite, which gives a freed id to the next user created, at
someone else: they would own the files and private folders. These requests stop
with 401 instead. The tests hand each route the user as the request holds it,
already deleted, with bob created on the freed id.
"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
import trimesh
from httpx import AsyncClient
from sqlalchemy import delete, select

from backend.app.core.auth import ApiKeyActor, actor_still_exists, get_request_actor
from backend.app.main import app
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.user import User

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _user(db, name: str, user_id: int | None = None) -> User:
    user = User(id=user_id, username=name, password_hash="x", role="user")
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
async def deleted_alice(db_session):
    """Alice as a running request holds her: deleted since, and bob has her id."""
    alice = await _user(db_session, "alice")
    # An admin, so no ownership check turns the request away before the write.
    held = User(id=alice.id, username="alice", password_hash="x", role="admin")
    await db_session.execute(delete(User).where(User.id == alice.id))
    await db_session.commit()
    # AUTOINCREMENT now stops SQLite reusing ids; bob is put on it by hand, as
    # databases from before did, to keep the check covered.
    assert (await _user(db_session, "bob", alice.id)).id == alice.id
    try:
        yield lambda: app.dependency_overrides.__setitem__(get_request_actor, lambda: held)
    finally:
        app.dependency_overrides.pop(get_request_actor, None)


async def _library(db) -> tuple[list[int], list[int]]:
    files = (await db.execute(select(LibraryFile.created_by_id))).scalars().all()
    folders = (await db.execute(select(LibraryFolder.created_by_id))).scalars().all()
    return list(files), list(folders)


async def test_the_check_knows_who_is_still_there(db_session):
    alice = await _user(db_session, "alice")
    assert await actor_still_exists(db_session, alice)
    assert await actor_still_exists(db_session, None)  # auth off: nobody to lose
    keyless = ApiKeyActor.__new__(ApiKeyActor)
    keyless.id, keyless.username = None, None
    assert await actor_still_exists(db_session, keyless)  # a key without an owner

    held = User(id=alice.id, username="alice")
    await db_session.execute(delete(User).where(User.id == alice.id))
    await db_session.commit()
    assert not await actor_still_exists(db_session, held)
    await _user(db_session, "bob", held.id)  # on alice's id, as before AUTOINCREMENT
    assert not await actor_still_exists(db_session, held)


async def test_a_zip_extract_stops(async_client: AsyncClient, db_session, deleted_alice):
    deleted_alice()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("sub/a.3mf", b"PK\x03\x04 a")
        zf.writestr("sub/b.3mf", b"PK\x03\x04 b")

    response = await async_client.post(
        "/api/v1/library/files/extract-zip",
        files={"file": ("models.zip", archive.getvalue(), "application/zip")},
        params={"generate_stl_thumbnails": "false"},
    )

    assert response.status_code == 401, response.text
    assert await _library(db_session) == ([], [])


async def test_a_combine_stops(async_client: AsyncClient, db_session, deleted_alice):
    stl = trimesh.creation.box((10, 10, 10)).export(file_type="stl")
    uploaded = await async_client.post(
        "/api/v1/library/files",
        files={"file": ("box.stl", stl, "application/octet-stream")},
        params={"generate_stl_thumbnails": "false"},
    )
    assert uploaded.status_code == 200, uploaded.text

    deleted_alice()
    response = await async_client.post(
        "/api/v1/library/files/combine", json={"items": [{"file_id": uploaded.json()["id"]}], "filename": "Plate"}
    )

    assert response.status_code == 401, response.text
    assert (await _library(db_session))[0] == [None]  # only the source


async def test_a_makerworld_import_stops(async_client: AsyncClient, db_session, deleted_alice):
    from backend.app.services.model_providers.base import ProviderDownload
    from backend.tests.integration.test_makerworld_apikey_auth import _download_info, _fake_service

    deleted_alice()
    service = _fake_service(
        get_download=_download_info(),
        download=ProviderDownload(file_bytes=b"PK\x03\x04fake-3mf-bytes", filename="cube.3mf"),
    )
    with patch("backend.app.api.routes.makerworld._build_service", AsyncMock(return_value=service)):
        response = await async_client.post("/api/v1/makerworld/import", json={"model_id": 1400373})

    assert response.status_code == 401, response.text
    assert (await _library(db_session))[0] == []


async def test_a_manyfold_import_stops(async_client: AsyncClient, db_session, deleted_alice, monkeypatch):
    from backend.app.api.routes import manyfold as routes
    from backend.app.services.model_providers.manyfold import service as svc
    from backend.tests._fixtures.manyfold import BASE, STL, FakeFile, FakeManyfold

    fake = FakeManyfold(client_secret="app-secret")
    fake.add_model("cube01", "Calibration Cube", {"f1": FakeFile("cube.stl", "model/stl", STL)})
    real = routes.ManyfoldService
    monkeypatch.setattr(routes, "ManyfoldService", lambda config: real(config, client=fake.client()))
    svc.clear_token_cache()
    try:
        connected = await async_client.put(
            "/api/v1/manyfold/config",
            json={"url": f"{BASE}/", "client_id": fake.client_id, "client_secret": "app-secret"},
        )
        assert connected.status_code == 200, connected.text
        deleted_alice()
        response = await async_client.post("/api/v1/manyfold/import", json={"model_id": "cube01", "file_id": "f1"})
    finally:
        svc.clear_token_cache()

    assert response.status_code == 401, response.text
    assert await _library(db_session) == ([], [])
