"""Switching ``clears_plate`` on takes printers:clear_plate (#3576).

A file or job that clears its own plate skips the plate-clear confirmation, so
marking one needs the permission that confirms a cleared plate. Checked on
queue create, update and bulk update, the library file update, the upload and
the zip upload. Switching it off, sending the value that is already stored and
inheriting it from a file or an archive need nothing extra.
"""

import io
import zipfile
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

DETAIL = "Setting clears_plate requires printers:clear_plate"

# What a group needs to queue, edit and upload; printers:clear_plate is the
# only difference between the two users.
BASE_PERMISSIONS = [
    "printers:read",
    "queue:create",
    "queue:read_own",
    "queue:update_own",
    "library:read_all",
    "library:upload",
    "library:update_own",
    "archives:read_all",
    "archives:reprint_all",
]


# --------------------------------------------------------------------------- fixtures


async def _user(client: AsyncClient, admin: dict, name: str, permissions: list[str]) -> dict:
    group = await client.post(
        "/api/v1/groups/", headers=admin, json={"name": f"{name}-group", "permissions": permissions}
    )
    assert group.status_code in (200, 201), group.text
    created = await client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": name, "password": "Testpass1!", "group_ids": [group.json()["id"]]},
    )
    assert created.status_code in (200, 201), created.text
    login = await client.post("/api/v1/auth/login", json={"username": name, "password": "Testpass1!"})
    assert login.status_code == 200, login.text
    return {"headers": {"Authorization": f"Bearer {login.json()['access_token']}"}, "id": login.json()["user"]["id"]}


@pytest.fixture
async def users(async_client: AsyncClient):
    """``without`` lacks printers:clear_plate, ``allowed`` has it; otherwise the same."""
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "clearsadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "clearsadmin", "password": "AdminPass1!"})
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return {
        "admin": admin,
        "without": await _user(async_client, admin, "noclear", BASE_PERMISSIONS),
        "allowed": await _user(async_client, admin, "mayclear", [*BASE_PERMISSIONS, "printers:clear_plate"]),
    }


@pytest.fixture
async def file_factory(db_session):
    async def _create(model: str | None = "X1C", **kwargs):
        name = f"perm_{uuid4().hex[:8]}.gcode.3mf"
        defaults = {
            "filename": name,
            "file_path": f"/test/{name}",
            "file_size": 100,
            "file_type": "gcode.3mf",
            "file_metadata": {"sliced_for_model": model} if model else {},
        }
        defaults.update(kwargs)
        row = LibraryFile(**defaults)
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return _create


@pytest.fixture
async def item_factory(db_session, file_factory):
    async def _create(owner_id: int, **kwargs):
        lib = await file_factory()
        item = PrintQueueItem(library_file_id=lib.id, status="pending", position=0, created_by_id=owner_id, **kwargs)
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    return _create


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    return tmp_path


async def _stored(db_session, model, row_id: int):
    db_session.expire_all()
    return (await db_session.execute(select(model).where(model.id == row_id))).scalar_one()


def _refused(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == DETAIL


# --------------------------------------------------------------------------- queue create


class TestQueueCreate:
    async def test_switching_it_on_needs_the_permission(self, async_client, users, file_factory, db_session):
        lib = await file_factory()

        response = await async_client.post(
            "/api/v1/queue/",
            headers=users["without"]["headers"],
            json={"library_file_id": lib.id, "clears_plate": True},
        )

        _refused(response)
        assert (await db_session.execute(select(PrintQueueItem))).scalars().all() == []

    async def test_allowed_with_the_permission(self, async_client, users, file_factory):
        lib = await file_factory()

        response = await async_client.post(
            "/api/v1/queue/",
            headers=users["allowed"]["headers"],
            json={"library_file_id": lib.id, "clears_plate": True},
        )

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True

    @pytest.mark.parametrize("payload", [{}, {"clears_plate": False}, {"clears_plate": None}])
    async def test_off_or_unset_needs_nothing(self, async_client, users, file_factory, payload):
        lib = await file_factory()

        response = await async_client.post(
            "/api/v1/queue/", headers=users["without"]["headers"], json={"library_file_id": lib.id, **payload}
        )

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is False

    @pytest.mark.parametrize("payload", [{}, {"clears_plate": True}])
    async def test_a_flagged_file_passes_its_flag_on(self, async_client, users, file_factory, payload):
        """Inherited, or sent as the value the job would inherit anyway."""
        lib = await file_factory(clears_plate=True)

        response = await async_client.post(
            "/api/v1/queue/", headers=users["without"]["headers"], json={"library_file_id": lib.id, **payload}
        )

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True

    async def test_a_flagged_archive_passes_its_flag_on(self, async_client, users, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, clears_plate=True)

        response = await async_client.post(
            "/api/v1/queue/", headers=users["without"]["headers"], json={"archive_id": archive.id}
        )

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True

    async def test_an_unflagged_archive_cannot_be_switched_on(
        self, async_client, users, printer_factory, archive_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.post(
            "/api/v1/queue/", headers=users["without"]["headers"], json={"archive_id": archive.id, "clears_plate": True}
        )

        _refused(response)


class TestQueueCreateVariants:
    async def _files(self, printer_factory, file_factory, first=False, second=False):
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        return await file_factory("H2S", clears_plate=first), await file_factory("H2C", clears_plate=second)

    async def test_an_alternative_switched_on_needs_the_permission(
        self, async_client, users, printer_factory, file_factory
    ):
        a, b = await self._files(printer_factory, file_factory)
        payload = {"variants": [{"library_file_id": a.id, "clears_plate": True}, {"library_file_id": b.id}]}

        _refused(await async_client.post("/api/v1/queue/", headers=users["without"]["headers"], json=payload))

        response = await async_client.post("/api/v1/queue/", headers=users["allowed"]["headers"], json=payload)
        assert response.status_code == 200, response.text

    async def test_the_job_value_reaches_an_unflagged_alternative(
        self, async_client, users, printer_factory, file_factory
    ):
        a, b = await self._files(printer_factory, file_factory, first=True)
        payload = {"variants": [{"library_file_id": a.id}, {"library_file_id": b.id}], "clears_plate": True}

        _refused(await async_client.post("/api/v1/queue/", headers=users["without"]["headers"], json=payload))

    async def test_each_file_s_own_flag_sent_back_needs_nothing(
        self, async_client, users, printer_factory, file_factory, db_session
    ):
        """The print dialog sends every alternative's value, changed or not."""
        a, b = await self._files(printer_factory, file_factory, first=True)
        payload = {
            "variants": [
                {"library_file_id": a.id, "clears_plate": True},
                {"library_file_id": b.id, "clears_plate": False},
            ]
        }

        response = await async_client.post("/api/v1/queue/", headers=users["without"]["headers"], json=payload)

        assert response.status_code == 200, response.text
        rows = (
            (await db_session.execute(select(PrintQueueVariant).order_by(PrintQueueVariant.position))).scalars().all()
        )
        assert [v.clears_plate for v in rows] == [True, False]


# --------------------------------------------------------------------------- queue update


class TestQueueUpdate:
    async def test_switching_it_on_needs_the_permission(self, async_client, users, item_factory, db_session):
        item = await item_factory(users["without"]["id"])

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}", headers=users["without"]["headers"], json={"clears_plate": True}
        )

        _refused(response)
        assert (await _stored(db_session, PrintQueueItem, item.id)).clears_plate is False

    async def test_allowed_with_the_permission(self, async_client, users, item_factory, db_session):
        item = await item_factory(users["allowed"]["id"])

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}", headers=users["allowed"]["headers"], json={"clears_plate": True}
        )

        assert response.status_code == 200, response.text
        assert (await _stored(db_session, PrintQueueItem, item.id)).clears_plate is True

    async def test_the_stored_value_sent_again_needs_nothing(self, async_client, users, item_factory, db_session):
        """The edit dialog sends the job's value along with whatever else changed."""
        item = await item_factory(users["without"]["id"], clears_plate=True)

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}",
            headers=users["without"]["headers"],
            json={"clears_plate": True, "manual_start": True},
        )

        assert response.status_code == 200, response.text
        stored = await _stored(db_session, PrintQueueItem, item.id)
        assert stored.clears_plate is True
        assert stored.manual_start is True

    async def test_switching_it_off_needs_nothing(self, async_client, users, item_factory, db_session):
        item = await item_factory(users["without"]["id"], clears_plate=True)

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}", headers=users["without"]["headers"], json={"clears_plate": False}
        )

        assert response.status_code == 200, response.text
        assert (await _stored(db_session, PrintQueueItem, item.id)).clears_plate is False

    async def test_other_fields_need_nothing(self, async_client, users, item_factory):
        item = await item_factory(users["without"]["id"])

        response = await async_client.patch(
            f"/api/v1/queue/{item.id}", headers=users["without"]["headers"], json={"manual_start": True}
        )

        assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- queue bulk update


class TestQueueBulkUpdate:
    async def test_switching_it_on_needs_the_permission(self, async_client, users, item_factory, db_session):
        owner = users["without"]["id"]
        flagged = await item_factory(owner, clears_plate=True)
        plain = await item_factory(owner)

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            headers=users["without"]["headers"],
            json={"item_ids": [flagged.id, plain.id], "clears_plate": True, "manual_start": True},
        )

        _refused(response)
        # Nothing of the request is applied, on either row.
        for row_id in (flagged.id, plain.id):
            assert (await _stored(db_session, PrintQueueItem, row_id)).manual_start is False
        assert (await _stored(db_session, PrintQueueItem, plain.id)).clears_plate is False

    async def test_allowed_with_the_permission(self, async_client, users, item_factory, db_session):
        item = await item_factory(users["allowed"]["id"])

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            headers=users["allowed"]["headers"],
            json={"item_ids": [item.id], "clears_plate": True},
        )

        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 1
        assert (await _stored(db_session, PrintQueueItem, item.id)).clears_plate is True

    async def test_jobs_that_already_have_it_need_nothing(self, async_client, users, item_factory, db_session):
        item = await item_factory(users["without"]["id"], clears_plate=True)

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            headers=users["without"]["headers"],
            json={"item_ids": [item.id], "clears_plate": True, "manual_start": True},
        )

        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 1
        assert (await _stored(db_session, PrintQueueItem, item.id)).manual_start is True

    async def test_switching_it_off_needs_nothing(self, async_client, users, item_factory, db_session):
        item = await item_factory(users["without"]["id"], clears_plate=True)

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            headers=users["without"]["headers"],
            json={"item_ids": [item.id], "clears_plate": False},
        )

        assert response.status_code == 200, response.text
        assert (await _stored(db_session, PrintQueueItem, item.id)).clears_plate is False

    async def test_a_job_the_request_skips_does_not_count(self, async_client, users, item_factory, db_session):
        """Someone else's job is skipped, so it is not switched on either."""
        other = await item_factory(users["allowed"]["id"])

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            headers=users["without"]["headers"],
            json={"item_ids": [other.id], "clears_plate": True},
        )

        assert response.status_code == 200, response.text
        assert response.json()["skipped_count"] == 1
        assert (await _stored(db_session, PrintQueueItem, other.id)).clears_plate is False


# --------------------------------------------------------------------------- library file update


class TestLibraryFileUpdate:
    async def test_switching_it_on_needs_the_permission(self, async_client, users, file_factory, db_session):
        lib = await file_factory(created_by_id=users["without"]["id"])

        response = await async_client.put(
            f"/api/v1/library/files/{lib.id}",
            headers=users["without"]["headers"],
            json={"clears_plate": True, "notes": "kept out"},
        )

        _refused(response)
        stored = await _stored(db_session, LibraryFile, lib.id)
        assert stored.clears_plate is False
        assert stored.notes is None

    async def test_allowed_with_the_permission(self, async_client, users, file_factory, db_session):
        lib = await file_factory(created_by_id=users["allowed"]["id"])

        response = await async_client.put(
            f"/api/v1/library/files/{lib.id}", headers=users["allowed"]["headers"], json={"clears_plate": True}
        )

        assert response.status_code == 200, response.text
        assert (await _stored(db_session, LibraryFile, lib.id)).clears_plate is True

    async def test_the_stored_value_sent_again_needs_nothing(self, async_client, users, file_factory, db_session):
        """The file details send the flag along with the notes."""
        lib = await file_factory(created_by_id=users["without"]["id"], clears_plate=True)

        response = await async_client.put(
            f"/api/v1/library/files/{lib.id}",
            headers=users["without"]["headers"],
            json={"clears_plate": True, "notes": "new note"},
        )

        assert response.status_code == 200, response.text
        stored = await _stored(db_session, LibraryFile, lib.id)
        assert stored.clears_plate is True
        assert stored.notes == "new note"

    async def test_switching_it_off_needs_nothing(self, async_client, users, file_factory, db_session):
        lib = await file_factory(created_by_id=users["without"]["id"], clears_plate=True)

        response = await async_client.put(
            f"/api/v1/library/files/{lib.id}", headers=users["without"]["headers"], json={"clears_plate": False}
        )

        assert response.status_code == 200, response.text
        assert (await _stored(db_session, LibraryFile, lib.id)).clears_plate is False


# --------------------------------------------------------------------------- library upload


def _zip_bytes(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, f"content of {name}")
    return buf.getvalue()


class TestLibraryUpload:
    async def _upload(self, client, headers, params=None):
        files = {"file": ("part.stl", b"solid t\nendsolid t", "application/octet-stream")}
        return await client.post(
            "/api/v1/library/files",
            headers=headers,
            files=files,
            params={"generate_stl_thumbnails": "false", **(params or {})},
        )

    async def _extract(self, client, headers, params=None):
        files = {"file": ("bundle.zip", _zip_bytes("a.txt", "b.txt"), "application/zip")}
        return await client.post(
            "/api/v1/library/files/extract-zip",
            headers=headers,
            files=files,
            params={"generate_stl_thumbnails": "false", **(params or {})},
        )

    async def test_upload_with_the_flag_needs_the_permission(self, async_client, users, db_session, isolated_storage):
        response = await self._upload(async_client, users["without"]["headers"], {"clears_plate": "true"})

        _refused(response)
        assert (await db_session.execute(select(LibraryFile))).scalars().all() == []

    async def test_upload_with_the_flag_allowed_with_the_permission(
        self, async_client, users, db_session, isolated_storage
    ):
        response = await self._upload(async_client, users["allowed"]["headers"], {"clears_plate": "true"})

        assert response.status_code == 200, response.text
        assert (await _stored(db_session, LibraryFile, response.json()["id"])).clears_plate is True

    @pytest.mark.parametrize("params", [None, {"clears_plate": "false"}])
    async def test_upload_without_the_flag_needs_nothing(
        self, async_client, users, db_session, isolated_storage, params
    ):
        response = await self._upload(async_client, users["without"]["headers"], params)

        assert response.status_code == 200, response.text
        assert (await _stored(db_session, LibraryFile, response.json()["id"])).clears_plate is False

    async def test_zip_with_the_flag_needs_the_permission(self, async_client, users, db_session, isolated_storage):
        response = await self._extract(async_client, users["without"]["headers"], {"clears_plate": "true"})

        _refused(response)
        assert (await db_session.execute(select(LibraryFile))).scalars().all() == []

    async def test_zip_with_the_flag_allowed_with_the_permission(
        self, async_client, users, db_session, isolated_storage
    ):
        response = await self._extract(async_client, users["allowed"]["headers"], {"clears_plate": "true"})

        assert response.status_code == 200, response.text
        rows = (await db_session.execute(select(LibraryFile))).scalars().all()
        assert len(rows) == 2
        assert all(r.clears_plate is True for r in rows)

    async def test_zip_without_the_flag_needs_nothing(self, async_client, users, db_session, isolated_storage):
        response = await self._extract(async_client, users["without"]["headers"])

        assert response.status_code == 200, response.text
        rows = (await db_session.execute(select(LibraryFile))).scalars().all()
        assert len(rows) == 2
        assert all(r.clears_plate is False for r in rows)


# --------------------------------------------------------------------------- API keys


class TestApiKey:
    """A key answers with what its scope flags and its owner allow."""

    async def _key(self, client, admin, **flags) -> dict:
        response = await client.post("/api/v1/api-keys/", headers=admin, json={"name": "automation", **flags})
        assert response.status_code == 200, response.text
        return {"X-API-Key": response.json()["key"]}

    async def test_a_queue_key_cannot_switch_it_on(self, async_client, users, file_factory):
        lib = await file_factory()
        key = await self._key(async_client, users["admin"])

        response = await async_client.post(
            "/api/v1/queue/", headers=key, json={"library_file_id": lib.id, "clears_plate": True}
        )
        _refused(response)

        response = await async_client.post("/api/v1/queue/", headers=key, json={"library_file_id": lib.id})
        assert response.status_code == 200, response.text

    async def test_a_key_that_controls_printers_can(self, async_client, users, file_factory):
        lib = await file_factory()
        key = await self._key(async_client, users["admin"], can_control_printer=True)

        response = await async_client.post(
            "/api/v1/queue/", headers=key, json={"library_file_id": lib.id, "clears_plate": True}
        )

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True
