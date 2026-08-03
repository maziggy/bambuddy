"""Queueing a job with cross-model alternatives (#671).

One queue item, several sliced files, whichever printer frees up first. The
create endpoint's job is to refuse candidate sets that cannot mean what the user
intends, because after this point the scheduler dispatches to hardware with no
human in the loop.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select


@pytest.fixture
async def sliced_file_factory(db_session):
    _counter = [0]

    async def _create(model: str | None = "H2S", **kwargs):
        from backend.app.models.library import LibraryFile

        _counter[0] += 1
        defaults = {
            "filename": f"job_{_counter[0]}.gcode.3mf",
            "file_path": f"/test/job_{_counter[0]}.gcode.3mf",
            "file_size": 100,
            "file_type": "gcode.3mf",
            "file_metadata": {"sliced_for_model": model} if model else {},
        }
        defaults.update(kwargs)
        f = LibraryFile(**defaults)
        db_session.add(f)
        await db_session.commit()
        await db_session.refresh(f)
        return f

    return _create


async def _queue_variants(client: AsyncClient, *file_ids: int, **extra):
    payload = {"variants": [{"library_file_id": fid} for fid in file_ids]}
    payload.update(extra)
    return await client.post("/api/v1/queue/", json=payload)


async def _variants_of(db_session, item_id: int):
    from backend.app.models.print_queue import PrintQueueVariant

    rows = (
        (
            await db_session.execute(
                select(PrintQueueVariant)
                .where(PrintQueueVariant.queue_item_id == item_id)
                .order_by(PrintQueueVariant.position)
            )
        )
        .scalars()
        .all()
    )
    return rows


class TestQueueWithVariants:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_creates_one_item_with_a_candidate_per_file(
        self, async_client, db_session, sliced_file_factory, printer_factory
    ):
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")

        r = await _queue_variants(async_client, h2s.id, h2c.id)
        assert r.status_code == 200
        item_id = r.json()["id"]

        variants = await _variants_of(db_session, item_id)
        assert [v.target_model for v in variants] == ["H2S", "H2C"]
        assert [v.position for v in variants] == [0, 1]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_item_holds_no_file_of_its_own(
        self, async_client, db_session, sliced_file_factory, printer_factory
    ):
        """library_file_id is ON DELETE CASCADE. Pointing it at one candidate
        would mean deleting that single alternative destroys the whole job."""
        from backend.app.models.print_queue import PrintQueueItem

        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")

        item_id = (await _queue_variants(async_client, h2s.id, h2c.id)).json()["id"]
        item = (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()
        assert item.library_file_id is None
        assert item.archive_id is None
        assert item.target_model == "H2S", "mirrors the first candidate so the card has a label"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_one_candidate_leaves_the_job_and_its_sibling(
        self, async_client, db_session, sliced_file_factory, printer_factory
    ):
        from backend.app.models.print_queue import PrintQueueItem

        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")
        item_id = (await _queue_variants(async_client, h2s.id, h2c.id)).json()["id"]

        # Trash, then permanently delete — the only path that actually removes
        # the row. SQLite has PRAGMA foreign_keys off, so nothing cleans the
        # candidate up on its own.
        assert (await async_client.delete(f"/api/v1/library/files/{h2s.id}")).status_code == 200
        assert (await async_client.delete(f"/api/v1/library/trash/{h2s.id}")).status_code == 200

        item = (
            await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
        ).scalar_one_or_none()
        assert item is not None, "the job survives losing one alternative"
        remaining = await _variants_of(db_session, item_id)
        assert [v.target_model for v in remaining] == ["H2C"], "no row left pointing at a deleted file"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_specific_printer(self, async_client, sliced_file_factory, printer_factory):
        """Naming a printer defeats the entire purpose of offering alternatives."""
        printer = await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")

        r = await _queue_variants(async_client, h2s.id, h2c.id, printer_id=printer.id)
        assert r.status_code == 400
        assert "printer_id" in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_file_alongside_the_variants(self, async_client, sliced_file_factory, printer_factory):
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")
        other = await sliced_file_factory("H2D")

        r = await _queue_variants(async_client, h2s.id, h2c.id, library_file_id=other.id)
        assert r.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_two_candidates_for_the_same_printer(
        self, async_client, sliced_file_factory, printer_factory
    ):
        await printer_factory(model="H2S")
        a = await sliced_file_factory("H2S")
        b = await sliced_file_factory("H2S")

        r = await _queue_variants(async_client, a.id, b.id)
        assert r.status_code == 400
        assert "different printers" in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_the_same_file_twice(self, async_client, sliced_file_factory, printer_factory):
        await printer_factory(model="H2S")
        f = await sliced_file_factory("H2S")

        r = await _queue_variants(async_client, f.id, f.id)
        assert r.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cross_model_gate_applies_to_every_candidate(
        self, async_client, sliced_file_factory, printer_factory
    ):
        """A set is only as safe as its worst member."""
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        good = await sliced_file_factory("H2S")
        # Declares X1C but is offered as an H2C candidate.
        bad = await sliced_file_factory("X1C")

        r = await async_client.post(
            "/api/v1/queue/",
            json={
                "variants": [
                    {"library_file_id": good.id},
                    {"library_file_id": bad.id, "target_model": "H2C"},
                ]
            },
        )
        assert r.status_code == 400
        assert "sliced for X1C" in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_one_candidate_without_a_printer_is_allowed(
        self, async_client, db_session, sliced_file_factory, printer_factory
    ):
        """Slicing for the H2C before the H2C arrives is reasonable. Refusing the
        whole queue action over it would be worse than that candidate simply
        never matching."""
        await printer_factory(model="H2S")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")

        r = await _queue_variants(async_client, h2s.id, h2c.id)
        assert r.status_code == 200
        assert len(await _variants_of(db_session, r.json()["id"])) == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejected_when_no_candidate_has_a_printer(self, async_client, sliced_file_factory):
        """Nothing in the set can ever run — that is a job that waits forever."""
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")

        r = await _queue_variants(async_client, h2s.id, h2c.id)
        assert r.status_code == 400
        assert "No active printers" in r.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_assigning_a_printer_is_refused(self, async_client, db_session, sliced_file_factory, printer_factory):
        """The edit dialog offers a printer picker for every queue item. Taking it
        would leave a row with variants AND a printer_id — and the fixed-printer
        branch of the scheduler wins that race, dispatching a row whose
        library_file_id is still null."""
        printer = await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")
        item_id = (await _queue_variants(async_client, h2s.id, h2c.id)).json()["id"]

        r = await async_client.patch(f"/api/v1/queue/{item_id}", json={"printer_id": printer.id})
        assert r.status_code == 400
        assert "alternatives" in r.json()["detail"]

        assert len(await _variants_of(db_session, item_id)) == 2, "the alternatives survive the refusal"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_narrowing_to_one_model_is_refused(self, async_client, sliced_file_factory, printer_factory):
        """Saving "Any H2C" over a two-candidate job would silently discard the
        H2S alternative the user deliberately queued."""
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")
        item_id = (await _queue_variants(async_client, h2s.id, h2c.id)).json()["id"]

        r = await async_client.patch(f"/api/v1/queue/{item_id}", json={"target_model": "H2C"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resending_the_unchanged_model_is_allowed(self, async_client, sliced_file_factory, printer_factory):
        """The edit dialog re-sends target_model on every save, so an unchanged
        value must not block editing the schedule or print options."""
        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")
        created = (await _queue_variants(async_client, h2s.id, h2c.id)).json()

        r = await async_client.patch(
            f"/api/v1/queue/{created['id']}",
            json={"target_model": created["target_model"], "timelapse": True},
        )
        assert r.status_code == 200
        assert r.json()["timelapse"] is True
        assert len(r.json()["variants"]) == 2, "the response still carries the alternatives"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_quantity_gives_each_copy_its_own_candidates(
        self, async_client, db_session, sliced_file_factory, printer_factory
    ):
        """Attempt counts are per-item, and two copies must be free to land on
        different printers."""
        from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant

        await printer_factory(model="H2S")
        await printer_factory(model="H2C")
        h2s = await sliced_file_factory("H2S")
        h2c = await sliced_file_factory("H2C")

        r = await _queue_variants(async_client, h2s.id, h2c.id, quantity=3)
        assert r.status_code == 200

        item_ids = (await db_session.execute(select(PrintQueueItem.id))).scalars().all()
        assert len(item_ids) == 3
        total = (await db_session.execute(select(PrintQueueVariant))).scalars().all()
        assert len(total) == 6
