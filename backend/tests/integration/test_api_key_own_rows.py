"""An API key whose owner may act on their own rows only acts on those rows.

The ownership routes (queue, archives, library) used to refuse such a key
outright: they asked the owner for the ``*_all`` permission. The key now gets
what its owner gets, its own rows and nobody else's, still narrowed to the
key's scope flags and printers. A key whose owner holds ``*_all`` and a key
without an owner keep reaching every row.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.group import Group
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.tests.integration import test_api_key_queue_acts_as_owner as owner_tests

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

world = owner_tests.world

_OWN_ONLY = [
    "queue:read_own",
    "queue:delete_own",
    "queue:start_unreviewed",
    "archives:update_own",
    "archives:delete_own",
    "library:update_own",
    "library:delete_own",
]


async def _key(db_session, owner_id: int | None, **scopes) -> dict[str, str]:
    full_key, key_hash, key_prefix = generate_api_key()
    flags = {
        "can_queue": True,
        "can_read_status": True,
        "can_manage_archives": True,
        "can_manage_library": True,
        **scopes,
    }
    db_session.add(APIKey(name="own rows", key_hash=key_hash, key_prefix=key_prefix, user_id=owner_id, **flags))
    await db_session.commit()
    return {"X-API-Key": full_key}


@pytest.fixture
async def rows(db_session, world):
    """The own-only owner's rows and another user's, one of each kind."""
    await owner_tests._set(db_session, "billing_enabled", "false")
    group = await db_session.scalar(select(Group).where(Group.name == "own-queuers"))
    group.permissions = [*group.permissions, *_OWN_ONLY]
    mine = await owner_tests._archive(db_session, world["owner"].id, 11)
    theirs = await owner_tests._archive(db_session, world["other"].id, 12)
    own_item = PrintQueueItem(
        printer_id=world["printer"].id, archive_id=mine.id, status="pending", created_by_id=world["owner"].id
    )
    other_item = PrintQueueItem(
        printer_id=world["printer"].id, archive_id=theirs.id, status="pending", created_by_id=world["other"].id
    )
    own_file = LibraryFile(
        filename="mine.stl",
        file_path="library/files/mine.stl",
        file_type="stl",
        file_size=1,
        created_by_id=world["owner"].id,
    )
    other_file = LibraryFile(
        filename="theirs.stl",
        file_path="library/files/theirs.stl",
        file_type="stl",
        file_size=1,
        created_by_id=world["other"].id,
    )
    db_session.add_all([own_item, other_item, own_file, other_file])
    await db_session.commit()
    return {
        **world,
        "mine": mine,
        "theirs": theirs,
        "own_item": own_item,
        "other_item": other_item,
        "own_file": own_file,
        "other_file": other_file,
    }


class TestQueue:
    async def test_the_owners_own_item_can_be_read_and_changed(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)
        item = rows["own_item"]

        got = await async_client.get(f"/api/v1/queue/{item.id}", headers=key)
        patched = await async_client.patch(f"/api/v1/queue/{item.id}", json={"manual_start": True}, headers=key)

        assert got.status_code == 200, got.text
        assert patched.status_code == 200, patched.text
        await db_session.refresh(item)
        assert item.manual_start is True

    async def test_bulk_changes_the_owners_items_and_skips_the_rest(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)
        own, other = rows["own_item"], rows["other_item"]

        bulk = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [own.id, other.id], "manual_start": True}, headers=key
        )

        assert bulk.status_code == 200, bulk.text
        assert (bulk.json()["updated_count"], bulk.json()["skipped_count"]) == (1, 1)
        await db_session.refresh(own)
        await db_session.refresh(other)
        assert own.manual_start is True
        assert other.manual_start is False

    async def test_the_list_holds_only_the_owners_items(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)

        listed = await async_client.get("/api/v1/queue/", headers=key)

        assert listed.status_code == 200, listed.text
        assert [i["id"] for i in listed.json()] == [rows["own_item"].id]

    async def test_another_users_item_is_neither_seen_nor_changed(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)
        other = rows["other_item"]

        got = await async_client.get(f"/api/v1/queue/{other.id}", headers=key)
        patched = await async_client.patch(f"/api/v1/queue/{other.id}", json={"manual_start": True}, headers=key)
        cancelled = await async_client.post(f"/api/v1/queue/{other.id}/cancel", headers=key)
        deleted = await async_client.delete(f"/api/v1/queue/{other.id}", headers=key)

        assert got.status_code == 404
        assert patched.status_code == 403
        assert cancelled.status_code == 403
        assert deleted.status_code == 403
        await db_session.refresh(other)
        assert (other.status, other.manual_start) == ("pending", False)

    async def test_the_owners_own_item_can_be_cancelled_and_deleted(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)
        item_id = rows["own_item"].id

        cancelled = await async_client.post(f"/api/v1/queue/{item_id}/cancel", headers=key)
        deleted = await async_client.delete(f"/api/v1/queue/{item_id}", headers=key)

        assert cancelled.status_code == 200, cancelled.text
        assert deleted.status_code == 200, deleted.text
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, item_id) is None

    async def test_a_key_without_the_queue_scope_is_still_refused(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id, can_queue=False)

        patched = await async_client.patch(
            f"/api/v1/queue/{rows['own_item'].id}", json={"manual_start": True}, headers=key
        )

        assert patched.status_code == 403

    async def test_the_owner_can_claim_and_start_a_job_without_an_owner(self, async_client, db_session, rows):
        """As the owner's session can: a virtual-printer upload has no owner (#1670)."""
        key = await _key(db_session, rows["owner"].id)
        item = PrintQueueItem(
            printer_id=rows["printer"].id, archive_id=rows["mine"].id, status="pending", manual_start=True
        )
        db_session.add(item)
        await db_session.commit()

        started = await async_client.post(f"/api/v1/queue/{item.id}/start", headers=key)

        assert started.status_code == 200, started.text
        await db_session.refresh(item)
        assert (item.manual_start, item.created_by_id) == (False, rows["owner"].id)


class TestAKeyLimitedToFewerPrinters:
    """The scheduler sends an "any printer of a model" job to any of its
    creator's printers, so a key limited to fewer printers than its owner may
    neither make nor start one."""

    @pytest.fixture
    async def limited(self, db_session, rows):
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(
                name="one printer",
                key_hash=key_hash,
                key_prefix=key_prefix,
                user_id=rows["owner"].id,
                can_queue=True,
                can_read_status=True,
                printer_ids=[rows["printer"].id],
            )
        )
        model_job = PrintQueueItem(
            archive_id=rows["mine"].id,
            target_model="X1C",
            status="pending",
            manual_start=True,
            created_by_id=rows["owner"].id,
        )
        db_session.add(model_job)
        await db_session.commit()
        return {"key": {"X-API-Key": full_key}, "model_job": model_job}

    async def test_it_cannot_start_a_model_job(self, async_client, db_session, rows, limited):
        job = limited["model_job"]

        started = await async_client.post(f"/api/v1/queue/{job.id}/start", headers=limited["key"])
        patched = await async_client.patch(
            f"/api/v1/queue/{job.id}", json={"manual_start": False}, headers=limited["key"]
        )
        bulk = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [job.id], "manual_start": False}, headers=limited["key"]
        )

        assert started.status_code == 400, started.text
        assert "specific printer" in started.json()["detail"]
        assert patched.status_code == 400, patched.text
        assert "specific printer" in patched.json()["detail"]
        assert bulk.status_code == 200, bulk.text
        assert (bulk.json()["updated_count"], bulk.json()["skipped_count"]) == (0, 1)
        await db_session.refresh(job)
        assert job.manual_start is True

    async def test_a_null_does_not_start_a_model_job(self, async_client, db_session, rows, limited):
        """A null for the not-null column is dropped, so nothing starts."""
        job = limited["model_job"]

        await async_client.patch(f"/api/v1/queue/{job.id}", json={"manual_start": None}, headers=limited["key"])
        await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [job.id], "manual_start": None}, headers=limited["key"]
        )

        await db_session.refresh(job)
        assert job.manual_start is True

    async def test_it_cannot_unpin_a_job_that_keeps_its_model(self, async_client, db_session, rows, limited):
        """A job the scheduler held on a printer keeps its model (watchdog, filament hold)."""
        job = limited["model_job"]
        job.printer_id = rows["printer"].id
        job.manual_start = False
        await db_session.commit()

        patched = await async_client.patch(f"/api/v1/queue/{job.id}", json={"printer_id": None}, headers=limited["key"])
        bulk = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [job.id], "printer_id": None}, headers=limited["key"]
        )

        assert patched.status_code == 400, patched.text
        assert bulk.json()["skipped_count"] == 1, bulk.text
        await db_session.refresh(job)
        assert job.printer_id == rows["printer"].id

    async def test_an_unlimited_key_can_start_it(self, async_client, db_session, rows, limited):
        key = await _key(db_session, rows["owner"].id)
        job = limited["model_job"]

        patched = await async_client.patch(f"/api/v1/queue/{job.id}", json={"manual_start": False}, headers=key)

        assert patched.status_code == 200, patched.text
        await db_session.refresh(job)
        assert job.manual_start is False

    async def test_it_cannot_retarget_a_job_to_a_model(self, async_client, db_session, rows, limited):
        patched = await async_client.patch(
            f"/api/v1/queue/{rows['own_item'].id}",
            json={"printer_id": None, "target_model": "X1C"},
            headers=limited["key"],
        )

        assert patched.status_code == 400, patched.text
        assert "specific printer" in patched.json()["detail"]
        await db_session.refresh(rows["own_item"])
        assert rows["own_item"].target_model is None


class TestArchives:
    async def test_the_owners_archives_only(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)

        listed = await async_client.get("/api/v1/archives/", headers=key)
        mine = await async_client.get(f"/api/v1/archives/{rows['mine'].id}", headers=key)
        theirs = await async_client.get(f"/api/v1/archives/{rows['theirs'].id}", headers=key)

        assert listed.status_code == 200, listed.text
        assert [a["id"] for a in listed.json()] == [rows["mine"].id]
        assert mine.status_code == 200, mine.text
        assert theirs.status_code == 404

    async def test_only_the_owners_archive_can_be_changed(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)

        mine = await async_client.patch(f"/api/v1/archives/{rows['mine'].id}", json={"notes": "k"}, headers=key)
        theirs = await async_client.patch(f"/api/v1/archives/{rows['theirs'].id}", json={"notes": "k"}, headers=key)

        assert mine.status_code == 200, mine.text
        assert theirs.status_code == 403
        theirs_row = await db_session.get(PrintArchive, rows["theirs"].id)
        await db_session.refresh(theirs_row)
        assert theirs_row.notes != "k"


class TestLibrary:
    async def test_the_owners_files_only(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)

        listed = await async_client.get("/api/v1/library/files", headers=key)
        mine = await async_client.get(f"/api/v1/library/files/{rows['own_file'].id}", headers=key)
        theirs = await async_client.get(f"/api/v1/library/files/{rows['other_file'].id}", headers=key)

        assert listed.status_code == 200, listed.text
        assert [f["id"] for f in listed.json()] == [rows["own_file"].id]
        assert mine.status_code == 200, mine.text
        assert theirs.status_code == 404

    async def test_the_trash_holds_only_the_owners_files(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)
        for row in (rows["own_file"], rows["other_file"]):
            row.deleted_at = datetime.now(timezone.utc)
        await db_session.commit()

        listed = await async_client.get("/api/v1/library/trash", headers=key)

        assert listed.status_code == 200, listed.text
        assert [f["id"] for f in listed.json()["items"]] == [rows["own_file"].id]

    async def test_another_users_file_is_not_deleted(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)

        deleted = await async_client.delete(f"/api/v1/library/files/{rows['other_file'].id}", headers=key)

        assert deleted.status_code == 403
        await db_session.refresh(rows["other_file"])
        assert rows["other_file"].deleted_at is None


class TestKeysThatReachEveryRow:
    async def test_an_admins_key_still_changes_another_users_item(self, async_client, db_session, rows):
        key = await _key(db_session, rows["admin"].id)

        patched = await async_client.patch(
            f"/api/v1/queue/{rows['other_item'].id}", json={"manual_start": True}, headers=key
        )

        assert patched.status_code == 200, patched.text

    async def test_a_key_without_an_owner_still_changes_any_item(self, async_client, db_session, rows):
        key = await _key(db_session, None)

        patched = await async_client.patch(
            f"/api/v1/queue/{rows['other_item'].id}", json={"manual_start": True}, headers=key
        )

        assert patched.status_code == 200, patched.text

    async def test_a_deactivated_own_only_owners_key_is_refused(self, async_client, db_session, rows):
        key = await _key(db_session, rows["owner"].id)
        rows["owner"].is_active = False
        await db_session.commit()

        patched = await async_client.patch(
            f"/api/v1/queue/{rows['own_item'].id}", json={"manual_start": True}, headers=key
        )

        assert patched.status_code in (401, 403)

    async def test_a_key_without_an_owner_still_needs_the_queue_scope(self, async_client, db_session, rows):
        key = await _key(db_session, None, can_queue=False)

        patched = await async_client.patch(
            f"/api/v1/queue/{rows['other_item'].id}", json={"manual_start": True}, headers=key
        )

        assert patched.status_code == 403


class TestQueueHistoryClear:
    async def test_a_key_that_reads_own_runs_clears_only_those(self, async_client, db_session, rows):
        """Its owner may delete every run but read only their own: the History tab
        it clears is theirs, as for the owner signed in."""
        group = await db_session.scalar(select(Group).where(Group.name == "own-queuers"))
        group.permissions = [*group.permissions, "queue:delete_all"]
        for item in (rows["own_item"], rows["other_item"]):
            item.status = "completed"
        await db_session.commit()
        own_id, other_id = rows["own_item"].id, rows["other_item"].id
        key = await _key(db_session, rows["owner"].id)

        response = await async_client.post("/api/v1/queue/history/clear", headers=key)

        assert response.status_code == 200, response.text
        assert response.json() == {"cleared": 1, "kept": 0}
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, own_id) is None
        assert await db_session.get(PrintQueueItem, other_id) is not None
