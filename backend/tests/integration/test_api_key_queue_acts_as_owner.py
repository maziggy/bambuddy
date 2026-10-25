"""An API key acts as its owner (#3256).

A route's own checks on cost centers, archives, library files and folders run
against the key's owner, with the owner's permissions narrowed to the key's
scope flags. Where it applies, each case goes through the key and through the
owner's session, which must agree. A key made before keys had owners owns
nothing and may use no cost center.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.auth import generate_api_key, get_password_hash
from backend.app.core.config import settings as app_settings
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import CostCenter, CostCenterMember
from backend.app.models.group import Group
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.print_batch import PrintBatch
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.user import User

PASSWORD = "Ownerpass1!"

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _set(db_session, key: str, value: str) -> None:
    row = await db_session.scalar(select(Settings).where(Settings.key == key))
    if row is None:
        db_session.add(Settings(key=key, value=value))
    else:
        row.value = value


@pytest.fixture
async def world(db_session):
    """Auth and billing on; a key owner who may queue and reprint only their
    own archives, another user, and one printer."""
    await _set(db_session, "auth_enabled", "true")
    await _set(db_session, "advanced_auth_enabled", "false")
    await _set(db_session, "billing_enabled", "true")
    group = Group(
        name="own-queuers",
        description="t",
        permissions=[
            "queue:create",
            "queue:read_own",
            "queue:update_own",
            "archives:read_own",
            "archives:reprint_own",
            "library:read_own",
        ],
        is_system=False,
    )
    db_session.add(group)
    await db_session.flush()
    owner = User(username="keyowner", password_hash=get_password_hash(PASSWORD), is_active=True, groups=[group])
    other = User(username="otheruser", password_hash=get_password_hash(PASSWORD), is_active=True)
    admin = User(username="adminowner", password_hash=get_password_hash(PASSWORD), role="admin", is_active=True)
    printer = Printer(
        name="P", ip_address="192.168.9.9", serial_number="00M00A3256000001", access_code="12345678", model="X1C"
    )
    db_session.add_all([owner, other, admin, printer])
    await db_session.commit()
    return {"owner": owner, "other": other, "admin": admin, "printer": printer}


async def _archive(db_session, created_by_id: int | None, n: int) -> PrintArchive:
    archive = PrintArchive(
        filename=f"a{n}.3mf",
        print_name=f"a{n}",
        file_path=f"/tmp/a3256_{n}.3mf",  # nosec B108
        file_size=1,
        content_hash=f"hash3256_{n}",
        status="completed",
        cost=1.25,
        filament_used_grams=50.0,
        created_by_id=created_by_id,
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)
    return archive


async def _center(db_session, *, owner_user_id: int | None = None, member_id: int | None = None) -> CostCenter:
    center = CostCenter(
        name=f"cc-{owner_user_id}-{member_id}",
        is_active=True,
        is_private=owner_user_id is not None,
        owner_user_id=owner_user_id,
    )
    db_session.add(center)
    await db_session.flush()
    if member_id is not None:
        db_session.add(CostCenterMember(cost_center_id=center.id, user_id=member_id, can_print=True))
    await db_session.commit()
    await db_session.refresh(center)
    return center


async def _key(db_session, owner_id: int | None, *, can_queue: bool = True) -> dict[str, str]:
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(
            name="probe",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=owner_id,
            can_queue=can_queue,
            can_read_status=True,
        )
    )
    await db_session.commit()
    return {"X-API-Key": full_key}


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _queue(client: AsyncClient, headers, printer: Printer, archive: PrintArchive, cost_center_id: int | None):
    return await client.post(
        "/api/v1/queue/",
        json={"printer_id": printer.id, "archive_id": archive.id, "cost_center_id": cost_center_id},
        headers=headers,
    )


class TestCostCenters:
    async def test_another_users_private_cost_center_is_refused(self, async_client, db_session, world):
        archive = await _archive(db_session, world["owner"].id, 1)
        center = await _center(db_session, owner_user_id=world["other"].id)
        key = await _key(db_session, world["owner"].id)

        by_key = await _queue(async_client, key, world["printer"], archive, center.id)
        by_session = await _queue(
            async_client, await _login(async_client, "keyowner"), world["printer"], archive, center.id
        )

        assert by_key.status_code == by_session.status_code == 403
        assert await db_session.scalar(select(PrintQueueItem)) is None

    async def test_a_shared_cost_center_needs_membership(self, async_client, db_session, world):
        archive = await _archive(db_session, world["owner"].id, 1)
        center = await _center(db_session, member_id=world["other"].id)
        key = await _key(db_session, world["owner"].id)

        assert (await _queue(async_client, key, world["printer"], archive, center.id)).status_code == 403

    async def test_the_owners_own_cost_center_works_and_the_item_is_the_owners(self, async_client, db_session, world):
        archive = await _archive(db_session, world["owner"].id, 1)
        center = await _center(db_session, owner_user_id=world["owner"].id)
        key = await _key(db_session, world["owner"].id)

        response = await _queue(async_client, key, world["printer"], archive, center.id)

        assert response.status_code == 200, response.text
        item = await db_session.scalar(select(PrintQueueItem).where(PrintQueueItem.id == response.json()["id"]))
        assert item.cost_center_id == center.id
        # Credited to the owner, so the scheduler's check at print start has
        # someone to check, and the owner sees it under queue:read_own.
        assert item.created_by_id == world["owner"].id

    async def test_a_cost_center_the_owner_is_a_member_of_works(self, async_client, db_session, world):
        archive = await _archive(db_session, world["owner"].id, 1)
        center = await _center(db_session, member_id=world["owner"].id)
        key = await _key(db_session, world["owner"].id)

        assert (await _queue(async_client, key, world["printer"], archive, center.id)).status_code == 200

    async def test_an_admins_key_may_use_any_cost_center_like_the_admin(self, async_client, db_session, world):
        archive = await _archive(db_session, world["admin"].id, 1)
        center = await _center(db_session, owner_user_id=world["other"].id)
        key = await _key(db_session, world["admin"].id)

        assert (await _queue(async_client, key, world["printer"], archive, center.id)).status_code == 200

    async def test_a_key_without_an_owner_may_use_no_cost_center(self, async_client, db_session, world):
        archive = await _archive(db_session, None, 1)
        center = await _center(db_session, member_id=world["owner"].id)
        key = await _key(db_session, None)

        assert (await _queue(async_client, key, world["printer"], archive, center.id)).status_code == 403

    async def test_moving_an_item_to_a_forbidden_cost_center_is_refused(self, async_client, db_session, world):
        # PATCH through a key needs the owner to hold queue:update_all.
        group = await db_session.scalar(select(Group).where(Group.name == "own-queuers"))
        group.permissions = [*group.permissions, "queue:update_all"]
        await db_session.commit()
        archive = await _archive(db_session, world["owner"].id, 1)
        allowed = await _center(db_session, owner_user_id=world["owner"].id)
        forbidden = await _center(db_session, owner_user_id=world["other"].id)
        key = await _key(db_session, world["owner"].id)
        created = await _queue(async_client, key, world["printer"], archive, allowed.id)
        assert created.status_code == 200, created.text

        response = await async_client.patch(
            f"/api/v1/queue/{created.json()['id']}", json={"cost_center_id": forbidden.id}, headers=key
        )

        assert response.status_code == 403
        item = await db_session.scalar(select(PrintQueueItem).where(PrintQueueItem.id == created.json()["id"]))
        await db_session.refresh(item)
        assert item.cost_center_id == allowed.id


class TestListingCostCenters:
    async def test_a_key_lists_its_owners_cost_centers(self, async_client, db_session, world):
        mine = await _center(db_session, owner_user_id=world["owner"].id)
        shared = await _center(db_session, member_id=world["owner"].id)
        await _center(db_session, owner_user_id=world["other"].id)
        key = await _key(db_session, world["owner"].id)

        by_key = await async_client.get("/api/v1/finance/cost-centers/mine", headers=key)
        by_session = await async_client.get(
            "/api/v1/finance/cost-centers/mine", headers=await _login(async_client, "keyowner")
        )

        assert by_key.status_code == 200, by_key.text
        assert {c["id"] for c in by_key.json()} == {mine.id, shared.id}
        assert by_key.json() == by_session.json()

    async def test_a_key_that_cannot_queue_gets_none(self, async_client, db_session, world):
        await _center(db_session, owner_user_id=world["owner"].id)
        key = await _key(db_session, world["owner"].id, can_queue=False)

        assert (await async_client.get("/api/v1/finance/cost-centers/mine", headers=key)).status_code == 403

    async def test_a_key_without_an_owner_has_none(self, async_client, db_session, world):
        await _center(db_session, owner_user_id=world["owner"].id)
        key = await _key(db_session, None)

        response = await async_client.get("/api/v1/finance/cost-centers/mine", headers=key)

        assert response.status_code == 200
        assert response.json() == []


class TestSources:
    async def test_another_users_archive_is_not_found(self, async_client, db_session, world):
        await _set(db_session, "billing_enabled", "false")
        archive = await _archive(db_session, world["other"].id, 1)
        key = await _key(db_session, world["owner"].id)

        by_key = await _queue(async_client, key, world["printer"], archive, None)
        by_session = await _queue(async_client, await _login(async_client, "keyowner"), world["printer"], archive, None)

        assert by_key.status_code == by_session.status_code == 404

    async def test_another_users_library_file_is_not_found(self, async_client, db_session, world):
        await _set(db_session, "billing_enabled", "false")
        # Unique: the checkout's library folder is shared by every xdist worker.
        rel_path = f"archive/library/files/probe_3256_{uuid4().hex}.gcode.3mf"
        abs_path = Path(app_settings.base_dir) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"probe")
        library_file = LibraryFile(
            filename="probe_3256.gcode.3mf",
            file_path=rel_path,
            file_size=5,
            file_type="3mf",
            created_by_id=world["other"].id,
        )
        db_session.add(library_file)
        await db_session.commit()
        key = await _key(db_session, world["owner"].id)
        try:
            response = await async_client.post(
                "/api/v1/queue/",
                json={"printer_id": world["printer"].id, "library_file_id": library_file.id},
                headers=key,
            )
        finally:
            abs_path.unlink(missing_ok=True)

        assert response.status_code == 404

    async def test_the_owners_own_archive_still_queues(self, async_client, db_session, world):
        await _set(db_session, "billing_enabled", "false")
        archive = await _archive(db_session, world["owner"].id, 1)
        key = await _key(db_session, world["owner"].id)

        assert (await _queue(async_client, key, world["printer"], archive, None)).status_code == 200


class TestBatches:
    async def test_another_users_batch_cannot_be_changed(self, async_client, db_session, world):
        await _set(db_session, "billing_enabled", "false")
        batch = PrintBatch(name="theirs", created_by_id=world["other"].id)
        db_session.add(batch)
        await db_session.commit()
        key = await _key(db_session, world["owner"].id)

        update = await async_client.patch(f"/api/v1/queue/batches/{batch.id}", json={"name": "mine"}, headers=key)
        ungroup = await async_client.post(f"/api/v1/queue/batches/{batch.id}/ungroup", headers=key)

        assert update.status_code == 404
        assert ungroup.status_code in (403, 404)
        await db_session.refresh(batch)
        assert batch.name == "theirs"


class TestAnOwnOnlyOwner:
    """An owner who may change only their own queue items.

    Their key changes their items and no other user's item (see
    test_api_key_own_rows.py), and an owner's deactivation stops it.
    """

    async def _items(self, db_session, world) -> tuple[PrintQueueItem, PrintQueueItem]:
        await _set(db_session, "billing_enabled", "false")
        mine = await _archive(db_session, world["owner"].id, 1)
        theirs = await _archive(db_session, world["other"].id, 2)
        own = PrintQueueItem(
            printer_id=world["printer"].id, archive_id=mine.id, status="pending", created_by_id=world["owner"].id
        )
        other = PrintQueueItem(
            printer_id=world["printer"].id, archive_id=theirs.id, status="pending", created_by_id=world["other"].id
        )
        db_session.add_all([own, other])
        await db_session.commit()
        return own, other

    async def test_another_users_item_is_left_alone(self, async_client, db_session, world):
        own, other = await self._items(db_session, world)
        key = await _key(db_session, world["owner"].id)

        patched = await async_client.patch(f"/api/v1/queue/{other.id}", json={"manual_start": True}, headers=key)
        bulk = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [own.id, other.id], "manual_start": True}, headers=key
        )
        deleted = await async_client.delete(f"/api/v1/queue/{other.id}", headers=key)

        assert patched.status_code in (403, 404)
        assert bulk.status_code == 200, bulk.text
        assert bulk.json()["skipped_count"] == 1
        assert deleted.status_code in (403, 404)
        await db_session.refresh(other)
        assert other.manual_start is False
        assert await db_session.get(PrintQueueItem, other.id) is not None

    async def test_a_deactivated_owners_key_changes_nothing(self, async_client, db_session, world):
        """An admin's key, so the deactivation alone is what refuses it."""
        own, _other = await self._items(db_session, world)
        key = await _key(db_session, world["admin"].id)
        world["admin"].is_active = False
        await db_session.commit()

        patched = await async_client.patch(f"/api/v1/queue/{own.id}", json={"manual_start": True}, headers=key)
        deleted = await async_client.delete(f"/api/v1/queue/{own.id}", headers=key)

        assert patched.status_code in (401, 403)
        assert deleted.status_code in (401, 403)
        await db_session.refresh(own)
        assert own.manual_start is False
        assert await db_session.get(PrintQueueItem, own.id) is not None


@pytest.fixture
async def library_world(db_session, world):
    """The same owner, who may also upload, read stats and pipelines, and a
    key with the library and status scopes as well."""
    await _set(db_session, "billing_enabled", "false")
    group = await db_session.scalar(select(Group).where(Group.name == "own-queuers"))
    group.permissions = [*group.permissions, "library:upload", "stats:read", "pipelines:read"]
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(
            name="library probe",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=world["owner"].id,
            can_queue=True,
            can_read_status=True,
            can_manage_library=True,
        )
    )
    theirs = LibraryFolder(name="theirs", created_by_id=world["other"].id, shared=False)
    db_session.add(theirs)
    await db_session.commit()
    return {**world, "key": {"X-API-Key": full_key}, "their_folder": theirs}


async def _their_file(db_session, world, filename: str = "theirs.stl") -> LibraryFile:
    row = LibraryFile(
        filename=filename,
        file_path=f"library/files/{filename}",
        file_type=filename.rsplit(".", 1)[-1],
        file_size=1024,
        created_by_id=world["other"].id,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


_SLICE_BODY = {"printer_preset_id": 1, "process_preset_id": 2, "filament_preset_id": 3}


class TestLibrary:
    async def test_no_folder_inside_another_users_private_folder(self, async_client, db_session, library_world):
        body = {"name": "sneaky", "parent_id": library_world["their_folder"].id}

        by_key = await async_client.post("/api/v1/library/folders", json=body, headers=library_world["key"])
        by_session = await async_client.post(
            "/api/v1/library/folders", json=body, headers=await _login(async_client, "keyowner")
        )

        assert by_key.status_code == by_session.status_code == 404

    async def test_a_keys_folder_is_its_owners_and_not_shared(self, async_client, db_session, library_world):
        response = await async_client.post(
            "/api/v1/library/folders", json={"name": "mine"}, headers=library_world["key"]
        )

        assert response.status_code == 200, response.text
        folder = await db_session.get(LibraryFolder, response.json()["id"])
        assert folder.created_by_id == library_world["owner"].id
        assert folder.shared is False

    async def test_no_upload_into_another_users_private_folder(self, async_client, db_session, library_world):
        response = await async_client.post(
            "/api/v1/library/files",
            params={"folder_id": library_world["their_folder"].id},
            files={"file": ("cube.stl", b"solid cube\nendsolid cube\n", "application/octet-stream")},
            headers=library_world["key"],
        )

        assert response.status_code == 404

    async def test_no_combining_another_users_file(self, async_client, db_session, library_world):
        theirs = await _their_file(db_session, library_world)

        response = await async_client.post(
            "/api/v1/library/files/combine",
            json={"items": [{"file_id": theirs.id}], "filename": "mix"},
            headers=library_world["key"],
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "File not found"

    async def test_no_slicing_another_users_file(self, async_client, db_session, library_world):
        theirs = await _their_file(db_session, library_world)

        response = await async_client.post(
            f"/api/v1/library/files/{theirs.id}/slice", json=_SLICE_BODY, headers=library_world["key"]
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "File not found"

    async def test_no_queueing_another_users_file_from_the_library(self, async_client, db_session, library_world):
        theirs = await _their_file(db_session, library_world, "theirs.gcode.3mf")

        response = await async_client.post(
            "/api/v1/library/files/add-to-queue",
            json={"file_ids": [theirs.id], "printer_id": library_world["printer"].id},
            headers=library_world["key"],
        )

        # The same answer an unknown id gets
        assert response.status_code == 400
        assert response.json()["detail"]["errors"][0]["error"] == "File not found"
        assert await db_session.scalar(select(PrintQueueItem)) is None


class TestArchivesAndPipelines:
    async def test_no_slicing_another_users_archive(self, async_client, db_session, library_world):
        archive = await _archive(db_session, library_world["other"].id, 1)

        response = await async_client.post(
            f"/api/v1/archives/{archive.id}/slice", json=_SLICE_BODY, headers=library_world["key"]
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Archive not found"

    async def test_no_per_user_stats_without_the_owners_permission(self, async_client, db_session, library_world):
        by_key = await async_client.get(
            "/api/v1/archives/stats",
            params={"created_by_id": library_world["other"].id},
            headers=library_world["key"],
        )
        by_session = await async_client.get(
            "/api/v1/archives/stats",
            params={"created_by_id": library_world["other"].id},
            headers=await _login(async_client, "keyowner"),
        )

        assert by_key.status_code == by_session.status_code == 403

    async def test_no_pipeline_on_another_users_file(self, async_client, db_session, library_world):
        theirs = await _their_file(db_session, library_world)
        created = await async_client.post(
            "/api/v1/slicer-pipelines/",
            json={
                "name": "Batch",
                "description": None,
                "printer_preset": {"source": "local", "id": "1"},
                "process_preset": {"source": "local", "id": "2"},
                "filament_presets": [{"source": "local", "id": "3"}],
                "bed_type": None,
            },
            headers=await _login(async_client, "adminowner"),
        )
        assert created.status_code == 201, created.text

        response = await async_client.post(
            f"/api/v1/slicer-pipelines/{created.json()['id']}/check-eligibility",
            json={"source_library_file_id": theirs.id},
            headers=library_world["key"],
        )

        # Refused by the ownership check, not later for the missing file
        assert response.status_code == 404
        assert response.json()["detail"] == "File not found"


class TestSlicedThroughAKeyIsTheOwners:
    """A key without the cloud scope resolves no cloud presets, but what it
    slices is still its owner's, as a pipeline run it starts is."""

    async def _slice(self, async_client, url, key):
        from dataclasses import dataclass

        @dataclass
        class _Job:
            id: int = 1
            status: str = "pending"

        with patch(
            "backend.app.services.slice_dispatch.slice_dispatch.enqueue", new=AsyncMock(return_value=_Job())
        ) as enqueue:
            response = await async_client.post(url, json=_SLICE_BODY, headers=key)
        assert response.status_code == 202, response.text
        return enqueue.await_args.kwargs["owner_id"]

    async def test_a_library_file(self, async_client, db_session, library_world, tmp_path, monkeypatch):
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)
        (tmp_path / "mine.3mf").write_bytes(b"PK")
        mine = LibraryFile(
            filename="mine.3mf",
            file_path="mine.3mf",
            file_type="3mf",
            file_size=2,
            created_by_id=library_world["owner"].id,
        )
        db_session.add(mine)
        await db_session.commit()

        owner = await self._slice(async_client, f"/api/v1/library/files/{mine.id}/slice", library_world["key"])

        assert owner == library_world["owner"].id

    async def test_an_archive(self, async_client, db_session, library_world, tmp_path, monkeypatch):
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)
        (tmp_path / "mine_archive.3mf").write_bytes(b"PK")
        archive = await _archive(db_session, library_world["owner"].id, 77)
        archive.file_path = "mine_archive.3mf"
        await db_session.commit()

        owner = await self._slice(async_client, f"/api/v1/archives/{archive.id}/slice", library_world["key"])

        assert owner == library_world["owner"].id
