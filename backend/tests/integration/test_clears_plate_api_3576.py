"""API and dispatch plumbing for ``clears_plate`` (#3576).

The flag lives on library files, queue items and archives. A queue item takes
it from its source when nobody says otherwise, the dispatch copies it onto the
archive so a reprint inherits it, and the scheduler's completion handling (see
test_clears_plate_print_complete_3576) reads it from the item.
"""

import io
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.config import settings as app_settings
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_batch import CLONED_SETTING_COLUMNS
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests._fixtures.background_tasks import discarding_spawn_patch

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
async def file_factory(db_session):
    _counter = [0]

    async def _create(model: str | None = "X1C", **kwargs):
        _counter[0] += 1
        defaults = {
            "filename": f"clears_{_counter[0]}_{uuid4().hex[:6]}.gcode.3mf",
            "file_path": f"/test/clears_{_counter[0]}.gcode.3mf",
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
async def archive_row(db_session, printer_factory, archive_factory):
    async def _create(**kwargs):
        printer = await printer_factory()
        return await archive_factory(printer.id, **kwargs)

    return _create


async def _queue(client: AsyncClient, **payload):
    response = await client.post("/api/v1/queue/", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _stored_item(test_engine, item_id: int) -> PrintQueueItem:
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        return (await fresh.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


# --------------------------------------------------------------------------- queue create


class TestQueueCreateFallbackOrder:
    async def test_default_is_false_for_a_plain_file(self, async_client, printer_factory, file_factory):
        printer = await printer_factory()
        lib = await file_factory()

        body = await _queue(async_client, printer_id=printer.id, library_file_id=lib.id)

        assert body["clears_plate"] is False

    async def test_none_takes_the_library_files_flag(self, async_client, printer_factory, file_factory):
        printer = await printer_factory()
        lib = await file_factory(clears_plate=True)

        body = await _queue(async_client, printer_id=printer.id, library_file_id=lib.id)

        assert body["clears_plate"] is True

    async def test_explicit_false_beats_a_flagged_file(self, async_client, printer_factory, file_factory):
        printer = await printer_factory()
        lib = await file_factory(clears_plate=True)

        body = await _queue(async_client, printer_id=printer.id, library_file_id=lib.id, clears_plate=False)

        assert body["clears_plate"] is False

    async def test_explicit_true_beats_a_plain_file(self, async_client, printer_factory, file_factory):
        printer = await printer_factory()
        lib = await file_factory(clears_plate=False)

        body = await _queue(async_client, printer_id=printer.id, library_file_id=lib.id, clears_plate=True)

        assert body["clears_plate"] is True

    async def test_stored_on_the_row(self, async_client, test_engine, printer_factory, file_factory):
        printer = await printer_factory()
        lib = await file_factory(clears_plate=True)

        body = await _queue(async_client, printer_id=printer.id, library_file_id=lib.id)

        assert (await _stored_item(test_engine, body["id"])).clears_plate is True

    async def test_the_archives_flag_is_inherited(self, async_client, printer_factory, archive_row):
        printer = await printer_factory()
        archive = await archive_row(clears_plate=True)

        body = await _queue(async_client, printer_id=printer.id, archive_id=archive.id)

        assert body["clears_plate"] is True

    async def test_an_archive_without_the_flag_stays_false(self, async_client, printer_factory, archive_row):
        printer = await printer_factory()
        archive = await archive_row()

        assert (await _queue(async_client, printer_id=printer.id, archive_id=archive.id))["clears_plate"] is False

    async def test_explicit_false_beats_a_flagged_archive(self, async_client, printer_factory, archive_row):
        printer = await printer_factory()
        archive = await archive_row(clears_plate=True)

        body = await _queue(async_client, printer_id=printer.id, archive_id=archive.id, clears_plate=False)

        assert body["clears_plate"] is False

    async def test_the_library_file_wins_over_the_archive(
        self, async_client, printer_factory, file_factory, archive_row
    ):
        """Both given: the file is the source of truth, the archive is only its past run."""
        printer = await printer_factory()
        lib = await file_factory(clears_plate=False)
        archive = await archive_row(clears_plate=True, library_file_id=lib.id)

        body = await _queue(async_client, printer_id=printer.id, library_file_id=lib.id, archive_id=archive.id)

        assert body["clears_plate"] is False

    async def test_quantity_copies_all_carry_the_flag(self, async_client, printer_factory, file_factory, test_engine):
        printer = await printer_factory()
        lib = await file_factory(clears_plate=True)

        first = await _queue(async_client, printer_id=printer.id, library_file_id=lib.id, quantity=3)

        # The response is the first copy; each stored row must carry the value.
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as fresh:
            rows = (await fresh.execute(select(PrintQueueItem))).scalars().all()
        assert first["clears_plate"] is True
        assert len(rows) == 3
        assert all(r.clears_plate for r in rows)


class TestQueueCreateVariants:
    async def _variants(self, client, *files, **extra):
        return await client.post(
            "/api/v1/queue/", json={"variants": [{"library_file_id": f.id} for f in files], **extra}
        )

    async def test_all_flagged_variants_flag_the_item(self, async_client, printer_factory, file_factory):
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        a = await file_factory("H2S", clears_plate=True)
        b = await file_factory("H2C", clears_plate=True)

        response = await self._variants(async_client, a, b)

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True

    async def test_one_unflagged_variant_leaves_it_off(self, async_client, printer_factory, file_factory):
        """A job that may land on either printer clears its plate only if every file does."""
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        a = await file_factory("H2S", clears_plate=True)
        b = await file_factory("H2C", clears_plate=False)

        response = await self._variants(async_client, a, b)

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is False

    async def test_explicit_value_beats_the_variants(self, async_client, printer_factory, file_factory):
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        a = await file_factory("H2S", clears_plate=True)
        b = await file_factory("H2C", clears_plate=True)

        response = await self._variants(async_client, a, b, clears_plate=False)

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is False


# --------------------------------------------------------------------------- queue update


class TestQueueUpdate:
    async def _item(self, async_client, printer_factory, file_factory, **extra):
        printer = await printer_factory()
        lib = await file_factory()
        return await _queue(async_client, printer_id=printer.id, library_file_id=lib.id, **extra)

    async def test_patch_sets_and_clears_the_flag(self, async_client, test_engine, printer_factory, file_factory):
        item = await self._item(async_client, printer_factory, file_factory)

        response = await async_client.patch(f"/api/v1/queue/{item['id']}", json={"clears_plate": True})
        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True
        assert (await _stored_item(test_engine, item["id"])).clears_plate is True

        response = await async_client.patch(f"/api/v1/queue/{item['id']}", json={"clears_plate": False})
        assert response.json()["clears_plate"] is False
        assert (await _stored_item(test_engine, item["id"])).clears_plate is False

    async def test_patch_without_the_field_leaves_it(self, async_client, test_engine, printer_factory, file_factory):
        item = await self._item(async_client, printer_factory, file_factory, clears_plate=True)

        response = await async_client.patch(f"/api/v1/queue/{item['id']}", json={"bed_levelling": "off"})

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True
        assert (await _stored_item(test_engine, item["id"])).clears_plate is True

    async def test_patch_null_leaves_it(self, async_client, test_engine, printer_factory, file_factory):
        item = await self._item(async_client, printer_factory, file_factory, clears_plate=True)

        response = await async_client.patch(f"/api/v1/queue/{item['id']}", json={"clears_plate": None})

        assert response.status_code == 200, response.text
        assert (await _stored_item(test_engine, item["id"])).clears_plate is True

    async def test_get_returns_the_field(self, async_client, printer_factory, file_factory):
        item = await self._item(async_client, printer_factory, file_factory, clears_plate=True)

        listed = (await async_client.get("/api/v1/queue/")).json()
        assert [i["clears_plate"] for i in listed if i["id"] == item["id"]] == [True]
        single = await async_client.get(f"/api/v1/queue/{item['id']}")
        assert single.status_code == 200
        assert single.json()["clears_plate"] is True

    async def test_bulk_update(self, async_client, test_engine, printer_factory, file_factory):
        printer = await printer_factory()
        a = await _queue(async_client, printer_id=printer.id, library_file_id=(await file_factory()).id)
        b = await _queue(async_client, printer_id=printer.id, library_file_id=(await file_factory()).id)

        response = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [a["id"], b["id"]], "clears_plate": True}
        )

        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 2
        assert (await _stored_item(test_engine, a["id"])).clears_plate is True
        assert (await _stored_item(test_engine, b["id"])).clears_plate is True

        response = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [a["id"], b["id"]], "clears_plate": False}
        )
        assert response.json()["updated_count"] == 2
        assert (await _stored_item(test_engine, a["id"])).clears_plate is False

    async def test_bulk_update_of_another_field_leaves_it(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        printer = await printer_factory()
        a = await _queue(
            async_client, printer_id=printer.id, library_file_id=(await file_factory()).id, clears_plate=True
        )

        response = await async_client.patch("/api/v1/queue/bulk", json={"item_ids": [a["id"]], "bed_levelling": "off"})

        assert response.status_code == 200, response.text
        assert (await _stored_item(test_engine, a["id"])).clears_plate is True


# --------------------------------------------------------------------------- stale item column (variants)


class TestVariantJobDerivedFlag:
    """A trial resolve can leave TRUE in the item column; the alternatives are the truth."""

    async def _job(self, async_client, test_engine, printer_factory, file_factory, *, column=True):
        from sqlalchemy import update

        await printer_factory(model="A1")
        await printer_factory(model="P1S")
        a = await file_factory("A1", clears_plate=True)
        b = await file_factory("P1S", clears_plate=False)
        job = await _queue(async_client, variants=[{"library_file_id": a.id}, {"library_file_id": b.id}])
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            await s.execute(update(PrintQueueItem).where(PrintQueueItem.id == job["id"]).values(clears_plate=column))
            await s.commit()
        return job

    async def _flags(self, test_engine, item_id):
        from backend.app.models.print_queue import PrintQueueVariant

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            rows = (
                (
                    await s.execute(
                        select(PrintQueueVariant)
                        .where(PrintQueueVariant.queue_item_id == item_id)
                        .order_by(PrintQueueVariant.position)
                    )
                )
                .scalars()
                .all()
            )
            return [bool(v.clears_plate) for v in rows]

    async def test_get_and_list_report_the_derived_value(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        job = await self._job(async_client, test_engine, printer_factory, file_factory)

        assert (await async_client.get(f"/api/v1/queue/{job['id']}")).json()["clears_plate"] is False
        listed = (await async_client.get("/api/v1/queue/")).json()
        assert [i["clears_plate"] for i in listed if i["id"] == job["id"]] == [False]

    async def _set_flags(self, test_engine, item_id, value):
        from sqlalchemy import update

        from backend.app.models.print_queue import PrintQueueVariant

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            await s.execute(
                update(PrintQueueVariant).where(PrintQueueVariant.queue_item_id == item_id).values(clears_plate=value)
            )
            await s.commit()

    async def test_patch_explicit_false_on_mixed_job_sets_all_false(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        job = await self._job(async_client, test_engine, printer_factory, file_factory)

        echoed = (await async_client.get(f"/api/v1/queue/{job['id']}")).json()["clears_plate"]
        assert echoed is False
        response = await async_client.patch(
            f"/api/v1/queue/{job['id']}", json={"bed_levelling": "off", "clears_plate": echoed}
        )

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is False
        assert await self._flags(test_engine, job["id"]) == [False, False]

    async def test_bulk_explicit_false_on_mixed_job_sets_all_false(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        job = await self._job(async_client, test_engine, printer_factory, file_factory)

        response = await async_client.patch(
            "/api/v1/queue/bulk", json={"item_ids": [job["id"]], "bed_levelling": "off", "clears_plate": False}
        )

        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 1
        assert await self._flags(test_engine, job["id"]) == [False, False]

    async def test_patch_true_on_all_true_job_is_noop(self, async_client, test_engine, printer_factory, file_factory):
        job = await self._job(async_client, test_engine, printer_factory, file_factory)
        await self._set_flags(test_engine, job["id"], True)

        response = await async_client.patch(f"/api/v1/queue/{job['id']}", json={"clears_plate": True})

        assert response.status_code == 200, response.text
        assert await self._flags(test_engine, job["id"]) == [True, True]

    async def test_bulk_true_on_all_true_job_is_noop(self, async_client, test_engine, printer_factory, file_factory):
        job = await self._job(async_client, test_engine, printer_factory, file_factory)
        await self._set_flags(test_engine, job["id"], True)

        response = await async_client.patch("/api/v1/queue/bulk", json={"item_ids": [job["id"]], "clears_plate": True})

        assert response.json()["updated_count"] == 1
        assert await self._flags(test_engine, job["id"]) == [True, True]

    async def test_true_on_mixed_job_sets_all_true(self, async_client, test_engine, printer_factory, file_factory):
        job = await self._job(async_client, test_engine, printer_factory, file_factory)

        response = await async_client.patch(f"/api/v1/queue/{job['id']}", json={"clears_plate": True})
        assert response.status_code == 200, response.text
        assert await self._flags(test_engine, job["id"]) == [True, True]

    async def test_bulk_true_on_mixed_job_sets_all_true(self, async_client, test_engine, printer_factory, file_factory):
        job = await self._job(async_client, test_engine, printer_factory, file_factory)

        await async_client.patch("/api/v1/queue/bulk", json={"item_ids": [job["id"]], "clears_plate": True})
        assert await self._flags(test_engine, job["id"]) == [True, True]

    async def test_explicit_change_sets_every_alternative(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        job = await self._job(async_client, test_engine, printer_factory, file_factory, column=False)

        response = await async_client.patch(f"/api/v1/queue/{job['id']}", json={"clears_plate": True})

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True
        assert await self._flags(test_engine, job["id"]) == [True, True]

    async def test_explicit_bulk_change_sets_every_alternative(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        job = await self._job(async_client, test_engine, printer_factory, file_factory, column=False)

        response = await async_client.patch("/api/v1/queue/bulk", json={"item_ids": [job["id"]], "clears_plate": True})

        assert response.json()["updated_count"] == 1
        assert await self._flags(test_engine, job["id"]) == [True, True]

    async def test_printing_item_reports_the_column(self, async_client, test_engine, printer_factory, file_factory):
        from sqlalchemy import update

        job = await self._job(async_client, test_engine, printer_factory, file_factory)
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            await s.execute(update(PrintQueueItem).where(PrintQueueItem.id == job["id"]).values(status="printing"))
            await s.commit()

        assert (await async_client.get(f"/api/v1/queue/{job['id']}")).json()["clears_plate"] is True


# --------------------------------------------------------------------------- batch clone


class TestBatchClone:
    async def test_the_column_is_cloned(self):
        assert "clears_plate" in CLONED_SETTING_COLUMNS

    async def test_a_clone_carries_the_flag(self, async_client, db_session, printer_factory, archive_row):
        printer = await printer_factory()
        archive = await archive_row(clears_plate=False)
        order = await async_client.post(
            "/api/v1/queue/batches",
            json={"name": "O", "archive_id": archive.id, "plates": [{"plate_id": 1, "quantity_target": 2}]},
        )
        assert order.status_code == 200, order.text
        order_id = order.json()["id"]
        source = await _queue(
            async_client,
            printer_id=printer.id,
            archive_id=archive.id,
            batch_id=order_id,
            plate_id=1,
            clears_plate=True,
        )

        dispatched = await async_client.post(f"/api/v1/queue/batches/{order_id}/dispatch", json={})
        assert dispatched.status_code == 200, dispatched.text

        db_session.expire_all()
        rows = (
            (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.batch_id == order_id)))
            .scalars()
            .all()
        )
        clones = [r for r in rows if r.id != source["id"]]
        assert len(clones) == 1
        assert clones[0].clears_plate is True

    async def test_a_clone_of_an_unflagged_item_stays_unflagged(
        self, async_client, db_session, printer_factory, archive_row
    ):
        printer = await printer_factory()
        archive = await archive_row()
        order = await async_client.post(
            "/api/v1/queue/batches",
            json={"name": "O", "archive_id": archive.id, "plates": [{"plate_id": 1, "quantity_target": 2}]},
        )
        order_id = order.json()["id"]
        source = await _queue(async_client, printer_id=printer.id, archive_id=archive.id, batch_id=order_id, plate_id=1)

        await async_client.post(f"/api/v1/queue/batches/{order_id}/dispatch", json={})

        db_session.expire_all()
        rows = (
            (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.batch_id == order_id)))
            .scalars()
            .all()
        )
        assert [r.clears_plate for r in rows if r.id != source["id"]] == [False]


# --------------------------------------------------------------------------- library


def _zip_bytes(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, f"content of {name}")
    return buf.getvalue()


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    return tmp_path


class TestLibrary:
    async def _upload(self, client, name="part.stl", params=None):
        files = {"file": (name, b"solid t\nendsolid t", "application/octet-stream")}
        return await client.post(
            "/api/v1/library/files", files=files, params={"generate_stl_thumbnails": "false", **(params or {})}
        )

    async def _flag_of(self, client, response) -> bool:
        assert response.status_code == 200, response.text
        detail = await client.get(f"/api/v1/library/files/{response.json()['id']}")
        assert detail.status_code == 200, detail.text
        return detail.json()["clears_plate"]

    async def test_upload_defaults_to_false(self, async_client, isolated_storage):
        assert await self._flag_of(async_client, await self._upload(async_client)) is False

    async def test_upload_with_the_flag(self, async_client, db_session, isolated_storage):
        response = await self._upload(async_client, params={"clears_plate": "true"})

        assert await self._flag_of(async_client, response) is True
        row = (
            await db_session.execute(select(LibraryFile).where(LibraryFile.id == response.json()["id"]))
        ).scalar_one()
        assert row.clears_plate is True

    async def test_upload_with_the_flag_explicitly_false(self, async_client, isolated_storage):
        response = await self._upload(async_client, params={"clears_plate": "false"})
        assert await self._flag_of(async_client, response) is False

    async def test_extract_zip_flags_every_file(self, async_client, db_session, isolated_storage):
        files = {"file": ("bundle.zip", _zip_bytes("a.txt", "b.txt"), "application/zip")}

        response = await async_client.post(
            "/api/v1/library/files/extract-zip",
            files=files,
            params={"clears_plate": "true", "generate_stl_thumbnails": "false"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["extracted"] == 2
        rows = (await db_session.execute(select(LibraryFile))).scalars().all()
        assert len(rows) == 2
        assert all(r.clears_plate is True for r in rows)

    async def test_extract_zip_defaults_to_false(self, async_client, db_session, isolated_storage):
        files = {"file": ("bundle.zip", _zip_bytes("a.txt", "b.txt"), "application/zip")}

        response = await async_client.post("/api/v1/library/files/extract-zip", files=files)

        assert response.status_code == 200, response.text
        rows = (await db_session.execute(select(LibraryFile))).scalars().all()
        assert len(rows) == 2
        assert all(r.clears_plate is False for r in rows)

    async def test_put_round_trip(self, async_client, db_session, file_factory):
        lib = await file_factory()

        response = await async_client.put(f"/api/v1/library/files/{lib.id}", json={"clears_plate": True})
        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True

        response = await async_client.put(f"/api/v1/library/files/{lib.id}", json={"clears_plate": False})
        assert response.json()["clears_plate"] is False
        await db_session.refresh(lib)
        assert lib.clears_plate is False

    async def test_put_without_the_field_leaves_it(self, async_client, db_session, file_factory):
        lib = await file_factory(clears_plate=True)

        response = await async_client.put(f"/api/v1/library/files/{lib.id}", json={"notes": "hello"})

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True
        assert response.json()["notes"] == "hello"

    async def test_put_null_leaves_it(self, async_client, file_factory):
        lib = await file_factory(clears_plate=True)

        response = await async_client.put(f"/api/v1/library/files/{lib.id}", json={"clears_plate": None})

        assert response.status_code == 200, response.text
        assert response.json()["clears_plate"] is True

    async def test_detail_and_list_carry_the_field(self, async_client, file_factory):
        flagged = await file_factory(clears_plate=True)
        plain = await file_factory()

        assert (await async_client.get(f"/api/v1/library/files/{flagged.id}")).json()["clears_plate"] is True
        assert (await async_client.get(f"/api/v1/library/files/{plain.id}")).json()["clears_plate"] is False
        listed = {f["id"]: f for f in (await async_client.get("/api/v1/library/files")).json()}
        assert listed[flagged.id]["clears_plate"] is True
        assert listed[plain.id]["clears_plate"] is False

    @pytest.fixture
    async def on_disk_file(self, db_session):
        created: list[Path] = []

        async def _create(**kwargs):
            name = f"clears_plate_probe_{uuid4().hex}.gcode.3mf"
            rel = f"archive/library/files/{name}"
            path = Path(app_settings.base_dir) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"probe")
            created.append(path)
            row = LibraryFile(filename=name, file_path=rel, file_size=5, file_type="3mf", **kwargs)
            db_session.add(row)
            await db_session.commit()
            await db_session.refresh(row)
            return row

        yield _create
        for path in created:
            path.unlink(missing_ok=True)

    @pytest.mark.parametrize("flag", [True, False])
    async def test_add_to_queue_copies_the_files_flag(self, async_client, test_engine, on_disk_file, flag):
        lib = await on_disk_file(clears_plate=flag)

        response = await async_client.post("/api/v1/library/files/add-to-queue", json={"file_ids": [lib.id]})

        assert response.status_code == 200, response.text
        added = response.json()["added"]
        assert len(added) == 1
        assert (await _stored_item(test_engine, added[0]["queue_item_id"])).clears_plate is flag


# --------------------------------------------------------------------------- archive response


class TestArchiveResponse:
    @pytest.mark.parametrize("flag", [True, False])
    async def test_the_archive_reports_its_flag(self, async_client, archive_row, flag):
        archive = await archive_row(clears_plate=flag)

        detail = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["clears_plate"] is flag

        listed = (await async_client.get("/api/v1/archives/")).json()
        rows = listed["items"] if isinstance(listed, dict) else listed
        assert [a["clears_plate"] for a in rows if a["id"] == archive.id] == [flag]


# --------------------------------------------------------------------------- dispatch copy


@pytest.fixture
async def dispatch_case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    base_dir = tmp_path / "case"
    base_dir.mkdir()
    archive_rel = Path("archives") / "job.3mf"
    (base_dir / archive_rel).parent.mkdir(parents=True)
    (base_dir / archive_rel).write_bytes(b"archive payload")
    library_abs = base_dir / "library" / "lib.gcode.3mf"
    library_abs.parent.mkdir(parents=True)
    library_abs.write_bytes(b"library payload")

    async with session_maker() as db:
        printer = Printer(
            name="Printer", serial_number="SERIAL", ip_address="127.0.0.1", access_code="access-code", model="X1C"
        )
        db.add(printer)
        await db.flush()
        archive = PrintArchive(
            printer_id=printer.id,
            filename="job.3mf",
            file_path=str(archive_rel),
            file_size=15,
            status="completed",
        )
        library_file = LibraryFile(
            filename="lib.gcode.3mf", file_path=str(library_abs), file_type="gcode.3mf", file_size=15
        )
        db.add_all([archive, library_file])
        await db.commit()
        ids = SimpleNamespace(printer_id=printer.id, archive_id=archive.id, library_file_id=library_file.id)

    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, ids=ids)
    finally:
        await engine.dispose()


async def _dispatch(case, item_fields: dict, archive_fields: dict | None = None, *, via_library: bool = False) -> bool:
    """Run ``_start_print`` for one item and return the archive's stored flag afterwards."""
    scheduler = PrintScheduler()
    async with case.session_maker() as db:
        if archive_fields:
            row = await db.get(PrintArchive, case.ids.archive_id)
            for key, value in archive_fields.items():
                setattr(row, key, value)
        fields = {"printer_id": case.ids.printer_id, "status": "pending"}
        if via_library:
            fields["library_file_id"] = case.ids.library_file_id
        else:
            fields["archive_id"] = case.ids.archive_id
        item = PrintQueueItem(**(fields | item_fields))
        db.add(item)
        await db.commit()

        patches = [
            patch.object(scheduler_module.settings, "base_dir", case.base_dir),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
            patch("backend.app.services.print_scheduler.printer_manager.start_print", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
            patch(
                "backend.app.services.print_scheduler.get_ftp_retry_settings",
                AsyncMock(return_value=(False, 0, 0, 1.0)),
            ),
            patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
            patch("backend.app.services.print_scheduler.upload_file_async", AsyncMock(return_value=True)),
            patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
            discarding_spawn_patch(),
            patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
            patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
            patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        ]
        if via_library:
            # Hand the dispatch the existing archive row as the freshly made one.
            async def make_archive(self, **_kwargs):
                return await db.get(PrintArchive, case.ids.archive_id)

            from backend.app.services.archive import ArchiveService

            patches.append(patch.object(ArchiveService, "archive_print", make_archive))
            patches.append(patch.object(scheduler, "_claim_reusable_archive", AsyncMock(return_value=None)))
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await scheduler._start_print(db, item)

    async with case.session_maker() as fresh:
        return (await fresh.get(PrintArchive, case.ids.archive_id)).clears_plate


class TestDispatchCopiesTheFlag:
    @pytest.mark.parametrize(
        ("item_flag", "archive_flag"), [(True, False), (False, True), (True, True), (False, False)]
    )
    async def test_archive_branch_assigns_the_items_value(self, dispatch_case, item_flag, archive_flag):
        stored = await _dispatch(dispatch_case, {"clears_plate": item_flag}, {"clears_plate": archive_flag})
        assert stored is item_flag

    @pytest.mark.parametrize(
        ("item_flag", "archive_flag"), [(True, False), (False, True), (True, True), (False, False)]
    )
    async def test_library_branch_assigns_the_items_value(self, dispatch_case, item_flag, archive_flag):
        stored = await _dispatch(
            dispatch_case, {"clears_plate": item_flag}, {"clears_plate": archive_flag}, via_library=True
        )
        assert stored is item_flag


@pytest.mark.parametrize("flag", [True, False])
async def test_webhook_reprint_inherits_clears_plate_from_the_archive(
    async_client, db_session, printer_factory, archive_factory, flag
):
    from sqlalchemy import select

    from backend.app.models.print_queue import PrintQueueItem
    from backend.tests.integration.test_queue_creation_paths_c4 import _webhook_key

    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    archive.clears_plate = flag
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/webhook/queue/add",
        json={"printer_id": printer.id, "archive_id": archive.id},
        headers=await _webhook_key(db_session),
    )

    assert response.status_code == 200, response.text
    item = await db_session.scalar(select(PrintQueueItem).where(PrintQueueItem.id == response.json()["id"]))
    assert item.clears_plate is flag


# --------------------------------------------------------------------------- per printer alternative


async def _stored_variants(test_engine, item_id: int):
    from backend.app.models.print_queue import PrintQueueVariant

    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        rows = await fresh.execute(
            select(PrintQueueVariant)
            .where(PrintQueueVariant.queue_item_id == item_id)
            .order_by(PrintQueueVariant.position)
        )
        return list(rows.scalars().all())


async def _summaries(client: AsyncClient, item_id: int) -> list[dict]:
    """The create response leaves ``variants`` unloaded, so read them back."""
    return (await client.get(f"/api/v1/queue/{item_id}")).json()["variants"]


class TestVariantAlternatives:
    """Each printer alternative of a cross-model job clears (or not) on its own."""

    async def _files(self, printer_factory, file_factory, a1_flag=True, p1s_flag=False):
        await printer_factory(model="A1")
        await printer_factory(model="P1S")
        a1 = await file_factory("A1", clears_plate=a1_flag)
        p1s = await file_factory("P1S", clears_plate=p1s_flag)
        return a1, p1s

    async def test_each_variant_takes_its_files_value(self, async_client, test_engine, printer_factory, file_factory):
        a1, p1s = await self._files(printer_factory, file_factory)

        item = await _queue(async_client, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}])

        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [True, False]
        # The item row is display only: all alternatives clear, or it does not.
        assert item["clears_plate"] is False
        assert [(v["target_model"], v["clears_plate"]) for v in await _summaries(async_client, item["id"])] == [
            ("A1", True),
            ("P1S", False),
        ]

    async def test_all_flagged_files_flag_the_item(self, async_client, printer_factory, file_factory):
        a1, p1s = await self._files(printer_factory, file_factory, p1s_flag=True)

        item = await _queue(async_client, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}])

        assert item["clears_plate"] is True
        assert [v["clears_plate"] for v in await _summaries(async_client, item["id"])] == [True, True]

    async def test_explicit_variant_value_wins_over_the_file(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        a1, p1s = await self._files(printer_factory, file_factory)

        item = await _queue(
            async_client,
            variants=[
                {"library_file_id": a1.id, "clears_plate": False},
                {"library_file_id": p1s.id, "clears_plate": True},
            ],
        )

        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [False, True]
        assert [v["clears_plate"] for v in await _summaries(async_client, item["id"])] == [False, True]
        assert item["clears_plate"] is False

    async def test_explicit_null_variant_value_takes_the_file(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        a1, p1s = await self._files(printer_factory, file_factory)

        item = await _queue(
            async_client,
            variants=[{"library_file_id": a1.id, "clears_plate": None}, {"library_file_id": p1s.id}],
        )

        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [True, False]

    @pytest.mark.parametrize("value", [True, False])
    async def test_explicit_item_value_overrides_every_variant(
        self, async_client, test_engine, printer_factory, file_factory, value
    ):
        a1, p1s = await self._files(printer_factory, file_factory)

        item = await _queue(
            async_client,
            clears_plate=value,
            variants=[
                {"library_file_id": a1.id, "clears_plate": not value},
                {"library_file_id": p1s.id},
            ],
        )

        assert item["clears_plate"] is value
        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [value, value]
        assert [v["clears_plate"] for v in await _summaries(async_client, item["id"])] == [value, value]

    async def test_quantity_copies_each_carry_the_values(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        a1, p1s = await self._files(printer_factory, file_factory)

        item = await _queue(
            async_client, quantity=2, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}]
        )

        listed = [i for i in (await async_client.get("/api/v1/queue/")).json() if i["variants"]]
        assert len(listed) == 2
        for entry in listed:
            assert [v["clears_plate"] for v in entry["variants"]] == [True, False]
        assert item["id"] in [e["id"] for e in listed]

    async def test_get_and_list_carry_the_summary_field(self, async_client, printer_factory, file_factory):
        a1, p1s = await self._files(printer_factory, file_factory)
        item = await _queue(async_client, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}])

        single = (await async_client.get(f"/api/v1/queue/{item['id']}")).json()
        assert [v["clears_plate"] for v in single["variants"]] == [True, False]

    async def test_patch_sets_every_variant(self, async_client, test_engine, printer_factory, file_factory):
        a1, p1s = await self._files(printer_factory, file_factory)
        item = await _queue(async_client, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}])

        response = await async_client.patch(f"/api/v1/queue/{item['id']}", json={"clears_plate": True})
        assert response.status_code == 200, response.text
        assert [v["clears_plate"] for v in response.json()["variants"]] == [True, True]
        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [True, True]

        response = await async_client.patch(f"/api/v1/queue/{item['id']}", json={"clears_plate": False})
        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [False, False]

    async def test_patch_without_the_field_or_null_leaves_the_variants(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        a1, p1s = await self._files(printer_factory, file_factory)
        item = await _queue(async_client, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}])

        await async_client.patch(f"/api/v1/queue/{item['id']}", json={"bed_levelling": "off"})
        await async_client.patch(f"/api/v1/queue/{item['id']}", json={"clears_plate": None})

        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [True, False]

    async def test_bulk_update_sets_every_variant(self, async_client, test_engine, printer_factory, file_factory):
        a1, p1s = await self._files(printer_factory, file_factory)
        item = await _queue(async_client, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}])

        response = await async_client.patch("/api/v1/queue/bulk", json={"item_ids": [item["id"]], "clears_plate": True})

        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 1
        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [True, True]

    async def test_bulk_update_of_another_field_leaves_the_variants(
        self, async_client, test_engine, printer_factory, file_factory
    ):
        a1, p1s = await self._files(printer_factory, file_factory)
        item = await _queue(async_client, variants=[{"library_file_id": a1.id}, {"library_file_id": p1s.id}])

        await async_client.patch("/api/v1/queue/bulk", json={"item_ids": [item["id"]], "bed_levelling": "off"})

        assert [v.clears_plate for v in await _stored_variants(test_engine, item["id"])] == [True, False]


class TestVariantsResolveAndClone:
    async def test_the_column_is_cloned(self):
        from backend.app.services.print_batch import CLONED_VARIANT_COLUMNS

        assert "clears_plate" in CLONED_VARIANT_COLUMNS

    @pytest.mark.parametrize(("winner", "expected"), [(0, True), (1, False)])
    async def test_resolve_variant_sets_the_items_flag(self, db_session, file_factory, winner, expected):
        """The chosen printer's slice decides, in both directions."""
        from backend.app.models.print_queue import PrintQueueVariant

        a1 = await file_factory("A1", clears_plate=True)
        p1s = await file_factory("P1S", clears_plate=False)
        # The item starts with the opposite of what the winner will say.
        item = PrintQueueItem(status="pending", position=1, clears_plate=not expected)
        item.variants.extend(
            [
                PrintQueueVariant(position=0, library_file_id=a1.id, target_model="A1", clears_plate=True),
                PrintQueueVariant(position=1, library_file_id=p1s.id, target_model="P1S", clears_plate=False),
            ]
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item, ["variants"])

        PrintScheduler()._resolve_variant(item, SimpleNamespace(variant=item.variants[winner]))

        assert item.clears_plate is expected

    async def test_a_non_variant_candidate_leaves_the_flag(self, db_session):
        item = PrintQueueItem(status="pending", position=1, clears_plate=True)

        PrintScheduler()._resolve_variant(item, SimpleNamespace(variant=None))

        assert item.clears_plate is True
