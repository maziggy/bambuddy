"""The /manyfold routes (#1471), against a fake Manyfold install."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.api.routes import manyfold as routes
from backend.app.core import database as _database_module
from backend.app.core.database import seed_default_groups
from backend.app.models.group import Group
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.settings import Settings
from backend.app.services.model_providers.manyfold import service as svc
from backend.tests._fixtures.manyfold import BASE, PNG, STL, THREE_MF, FakeFile, FakeManyfold

API = "/api/v1/manyfold"
SECRET = "app-secret"


@pytest.fixture
def manyfold(monkeypatch) -> FakeManyfold:
    fake = FakeManyfold(client_secret=SECRET)
    fake.add_model(
        "cube01",
        "Calibration Cube",
        {
            "f1": FakeFile("cube.stl", "model/stl", STL, render=PNG),
            "f2": FakeFile("cube.3mf", "model/3mf", THREE_MF),
            "f4": FakeFile("notes.pdf", "application/pdf", b"%PDF-1.7"),
            "f5": FakeFile("broken.3mf", "model/3mf", b"not a zip at all"),
            "f6": FakeFile("bad:name?.stl", "model/stl", STL),
        },
        preview="f1",
    )
    fake.add_model("boat02", "Benchy", {"f9": FakeFile("benchy.stl", "model/stl", STL)})
    real = routes.ManyfoldService
    monkeypatch.setattr(routes, "ManyfoldService", lambda config: real(config, client=fake.client()))
    svc.clear_token_cache()
    yield fake
    svc.clear_token_cache()


async def _connect(client: AsyncClient, fake: FakeManyfold, headers: dict | None = None) -> None:
    resp = await client.put(
        f"{API}/config",
        json={"url": f"{BASE}/", "client_id": fake.client_id, "client_secret": SECRET},
        headers=headers or {},
    )
    assert resp.status_code == 200, resp.text


class TestConnection:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_store_read_and_disconnect_without_ever_returning_the_secret(self, async_client, manyfold):
        empty = (await async_client.get(f"{API}/config")).json()
        assert empty == {"url": "", "client_id": "", "has_client_secret": False, "configured": False}

        await _connect(async_client, manyfold)
        stored = await async_client.get(f"{API}/config")
        assert stored.json() == {
            "url": BASE,
            "client_id": "app-id",
            "has_client_secret": True,
            "configured": True,
        }
        for response in (stored, await async_client.get("/api/v1/settings/")):
            # The stored rows must not break the general settings response either.
            assert response.status_code == 200, response.text
            assert SECRET not in response.text

        assert (await async_client.delete(f"{API}/config")).status_code == 204
        assert (await async_client.get(f"{API}/config")).json()["configured"] is False
        assert (await async_client.get(f"{API}/status")).json() == {"configured": False, "url": ""}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_empty_secret_keeps_the_stored_one(self, async_client, manyfold, db_session):
        await _connect(async_client, manyfold)
        resp = await async_client.put(f"{API}/config", json={"url": BASE, "client_id": "app-id", "client_secret": ""})
        assert resp.status_code == 200
        row = (await db_session.execute(select(Settings).where(Settings.key == "manyfold_client_secret"))).scalar_one()
        assert row.value == SECRET

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_first_save_needs_a_secret(self, async_client, manyfold):
        resp = await async_client.put(f"{API}/config", json={"url": BASE, "client_id": "app-id"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "manyfold_secret_required"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("url", ["docker:3214", "http://169.254.169.254/"])
    async def test_bad_url(self, async_client, manyfold, url):
        for path, method in ((f"{API}/config", "put"), (f"{API}/config/test", "post")):
            resp = await getattr(async_client, method)(path, json={"url": url, "client_id": "a", "client_secret": "b"})
            assert resp.status_code == 400
            assert resp.json()["detail"]["code"] == "manyfold_bad_url"
        assert manyfold.requests == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_test_counts_models_and_stores_nothing(self, async_client, manyfold):
        resp = await async_client.post(
            f"{API}/config/test", json={"url": BASE, "client_id": "app-id", "client_secret": SECRET}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"model_count": 2}
        assert (await async_client.get(f"{API}/config")).json()["configured"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_refused_secret_is_not_answered_with_401(self, async_client, manyfold):
        # A 401 would read as "your Bambuddy session ended" to the frontend.
        resp = await async_client.post(
            f"{API}/config/test", json={"url": BASE, "client_id": "app-id", "client_secret": "wrong"}
        )
        assert resp.status_code == 502
        assert resp.json()["detail"]["code"] == "manyfold_credentials"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_test_with_an_empty_secret_uses_the_stored_one(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        resp = await async_client.post(f"{API}/config/test", json={"url": BASE, "client_id": "app-id"})
        assert resp.status_code == 200, resp.text


class TestBrowsing:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_not_configured_is_409(self, async_client, manyfold):
        resp = await async_client.get(f"{API}/models")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "manyfold_not_configured"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_search_and_details(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        assert (await async_client.get(f"{API}/status")).json() == {"configured": True, "url": BASE}
        listing = (await async_client.get(f"{API}/models")).json()
        assert [m["name"] for m in listing["models"]] == ["Calibration Cube", "Benchy"]
        found = (await async_client.get(f"{API}/models", params={"q": "bench"})).json()
        assert found["total"] == 1

        model = (await async_client.get(f"{API}/models/cube01")).json()
        assert model["has_preview"] is True
        assert model["url"] == f"{BASE}/models/cube01"
        assert {f["id"]: f["importable"] for f in model["files"]} == {
            "f1": True,
            "f2": True,
            "f4": False,
            "f5": True,
            "f6": True,
        }
        assert all(f["library_file"] is None for f in model["files"])

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_missing_model_and_malformed_id(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        assert (await async_client.get(f"{API}/models/gone99")).status_code == 404
        assert (await async_client.get(f"{API}/models/a.b")).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preview(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        resp = await async_client.get(f"{API}/models/cube01/preview")
        assert resp.status_code == 200
        assert resp.content == PNG
        assert resp.headers["content-type"] == "image/png"
        assert (await async_client.get(f"{API}/models/boat02/preview")).status_code == 404


class TestImport:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_makerworld_import_route_does_not_serve_manyfold(self, async_client, manyfold):
        # Manyfold's own route checks file types and names; the pasted-URL route must not bypass that.
        await _connect(async_client, manyfold)
        resp = await async_client.post(
            "/api/v1/makerworld/import", json={"model_id": 1, "profile_id": 2, "source_type": "manyfold"}
        )
        assert resp.status_code == 400
        assert manyfold.requests == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_underscore_in_an_id_matches_only_itself(self, async_client, manyfold):
        # "_" is LIKE's single-character wildcard: model a_c must not see abc's import.
        manyfold.add_model("abc", "ABC", {"f1": FakeFile("abc.stl", "model/stl", STL)})
        manyfold.add_model("a_c", "A C", {"f1": FakeFile("ac.stl", "model/stl", STL)})
        await _connect(async_client, manyfold)
        assert (await async_client.post(f"{API}/import", json={"model_id": "abc", "file_id": "f1"})).status_code == 200
        other = (await async_client.get(f"{API}/models/a_c")).json()
        assert other["files"][0]["library_file"] is None
        mine = (await async_client.get(f"{API}/models/abc")).json()
        assert mine["files"][0]["library_file"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_lands_in_the_manyfold_folder_once(self, async_client, manyfold, db_session):
        await _connect(async_client, manyfold)
        resp = await async_client.post(f"{API}/import", json={"model_id": "cube01", "file_id": "f1"})
        assert resp.status_code == 200, resp.text
        first = resp.json()
        assert first["filename"] == "cube.stl" and first["was_existing"] is False

        row = await db_session.get(LibraryFile, first["library_file_id"])
        folder = await db_session.get(LibraryFolder, row.folder_id)
        assert folder.name == "Manyfold" and folder.parent_id is None
        assert row.source_type == "manyfold"
        assert row.source_url == "manyfold:cube01/f1"
        assert row.file_type == "stl"

        downloads = sum(1 for r in manyfold.requests if "/raw/" in r.url.path)
        again = (await async_client.post(f"{API}/import", json={"model_id": "cube01", "file_id": "f1"})).json()
        assert again == {**first, "was_existing": True}
        assert sum(1 for r in manyfold.requests if "/raw/" in r.url.path) == downloads

        model = (await async_client.get(f"{API}/models/cube01")).json()
        assert {f["id"]: f["library_file"] for f in model["files"]}["f1"] == {
            "id": first["library_file_id"],
            "filename": "cube.stl",
            "folder_id": first["folder_id"],
        }

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_deleted_import_can_be_imported_again(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        first = (await async_client.post(f"{API}/import", json={"model_id": "cube01", "file_id": "f2"})).json()
        assert (await async_client.delete(f"/api/v1/library/files/{first['library_file_id']}")).status_code in (
            200,
            204,
        )
        again = (await async_client.post(f"{API}/import", json={"model_id": "cube01", "file_id": "f2"})).json()
        assert again["was_existing"] is False
        assert again["library_file_id"] != first["library_file_id"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_only_printable_files(self, async_client, manyfold, db_session):
        await _connect(async_client, manyfold)
        resp = await async_client.post(f"{API}/import", json={"model_id": "cube01", "file_id": "f4"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "manyfold_not_importable"
        # Refused before anything was created.
        folders = (await db_session.execute(select(LibraryFolder).where(LibraryFolder.name == "Manyfold"))).all()
        assert folders == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_3mf_that_is_not_a_zip_is_refused(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        resp = await async_client.post(f"{API}/import", json={"model_id": "cube01", "file_id": "f5"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_names_the_printer_cannot_store_are_cleaned(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        resp = await async_client.post(f"{API}/import", json={"model_id": "cube01", "file_id": "f6"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["filename"] == "bad_name_.stl"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_into_a_chosen_folder(self, async_client, manyfold):
        await _connect(async_client, manyfold)
        folder = (await async_client.post("/api/v1/library/folders", json={"name": "Calibration"})).json()
        resp = await async_client.post(
            f"{API}/import", json={"model_id": "boat02", "file_id": "f9", "folder_id": folder["id"]}
        )
        assert resp.json()["folder_id"] == folder["id"]


# ---- permissions -------------------------------------------------------


async def _admin(client: AsyncClient) -> dict:
    await client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "mfadmin", "admin_password": "AdminPass1!"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "mfadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user(client: AsyncClient, admin: dict, username: str, permissions: list[str]) -> dict:
    group = await client.post(
        "/api/v1/groups/", headers=admin, json={"name": f"g_{username}", "permissions": permissions}
    )
    assert group.status_code in (200, 201), group.text
    created = await client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": username, "password": "UserPass1!", "group_ids": [group.json()["id"]]},
    )
    assert created.status_code in (200, 201), created.text
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


class TestPermissions:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_view_only_can_browse_but_not_import_or_configure(self, async_client, manyfold):
        admin = await _admin(async_client)
        await _connect(async_client, manyfold, admin)
        viewer = await _user(async_client, admin, "mfviewer", ["manyfold:view", "settings:read"])

        assert (await async_client.get(f"{API}/models", headers=viewer)).status_code == 200
        resp = await async_client.post(f"{API}/import", headers=viewer, json={"model_id": "cube01", "file_id": "f1"})
        assert resp.status_code == 403
        resp = await async_client.put(
            f"{API}/config", headers=viewer, json={"url": BASE, "client_id": "x", "client_secret": "y"}
        )
        assert resp.status_code == 403
        assert (await async_client.delete(f"{API}/config", headers=viewer)).status_code == 403
        assert SECRET not in (await async_client.get(f"{API}/config", headers=viewer)).text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_makerworld_access_alone_does_not_open_manyfold(self, async_client, manyfold):
        admin = await _admin(async_client)
        await _connect(async_client, manyfold, admin)
        mw = await _user(async_client, admin, "mwonly", ["makerworld:view", "makerworld:import"])
        assert (await async_client.get(f"{API}/models", headers=mw)).status_code == 403
        assert (await async_client.get(f"{API}/status", headers=mw)).status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_another_users_import_is_not_theirs(self, async_client, manyfold):
        """A user who sees only their own files neither sees a colleague's import
        nor gets it back for their own: they import a copy of their own."""
        admin = await _admin(async_client)
        await _connect(async_client, manyfold, admin)
        perms = ["manyfold:view", "manyfold:import", "library:read_own", "library:upload"]
        first = await _user(async_client, admin, "mffirst", perms)
        second = await _user(async_client, admin, "mfsecond", perms)
        body = {"model_id": "cube01", "file_id": "f1"}

        theirs = (await async_client.post(f"{API}/import", headers=first, json=body)).json()
        seen = (await async_client.get(f"{API}/models/cube01", headers=second)).json()
        mine = (await async_client.post(f"{API}/import", headers=second, json=body)).json()
        seen_after = (await async_client.get(f"{API}/models/cube01", headers=second)).json()

        assert {f["id"]: f["library_file"] for f in seen["files"]}["f1"] is None
        assert mine["was_existing"] is False and mine["library_file_id"] != theirs["library_file_id"]
        assert {f["id"]: f["library_file"] for f in seen_after["files"]}["f1"]["id"] == mine["library_file_id"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_anonymous_preview_is_refused(self, async_client, manyfold):
        admin = await _admin(async_client)
        await _connect(async_client, manyfold, admin)
        assert (await async_client.get(f"{API}/models/cube01/preview")).status_code == 401


class TestPermissionBackfill:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_groups_get_what_they_have_on_makerworld_once(self, async_client):
        async with _database_module.async_session() as session:
            await session.execute(
                Settings.__table__.delete().where(Settings.key == "_backfill_1471_manyfold_permissions_done")
            )
            for name, perms in (
                ("mf_importers", ["library:read_own", "makerworld:view", "makerworld:import"]),
                ("mf_browsers", ["makerworld:view"]),
                ("mf_nothing", ["library:read_own"]),
            ):
                session.add(Group(name=name, permissions=perms))
            await session.commit()

        await seed_default_groups()

        async with _database_module.async_session() as session:
            groups = {g.name: set(g.permissions) for g in (await session.execute(select(Group))).scalars().all()}
        assert {"manyfold:view", "manyfold:import"} <= groups["mf_importers"]
        assert "manyfold:view" in groups["mf_browsers"] and "manyfold:import" not in groups["mf_browsers"]
        assert not {"manyfold:view", "manyfold:import"} & groups["mf_nothing"]

        # An admin takes it away again; the next start must not hand it back.
        async with _database_module.async_session() as session:
            grp = (await session.execute(select(Group).where(Group.name == "mf_browsers"))).scalar_one()
            grp.permissions = ["makerworld:view"]
            await session.commit()
        await seed_default_groups()
        async with _database_module.async_session() as session:
            grp = (await session.execute(select(Group).where(Group.name == "mf_browsers"))).scalar_one()
            assert grp.permissions == ["makerworld:view"]
