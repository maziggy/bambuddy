"""The rack-position pick has to survive the round trip (#1784).

It did not, first time out: the field was declared on ``PrintQueueItemUpdate``
and the response model but not on ``PrintQueueItemCreate``, so Pydantic dropped
it from every POST without complaint. The queued item then carried no pick, the
dispatcher assigned positions itself, and the print ran from hotends the
operator had not chosen -- with nothing in the logs but ``chosen auto``.

A silently-dropped field is invisible at every layer above it, so it is pinned
here at the layer it crosses: HTTP in, database out.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.fixture
async def printer(db_session):
    from backend.app.models.printer import Printer

    printer = Printer(
        name="H2C-1",
        ip_address="192.168.1.210",
        serial_number="RACKCHOICE0001",
        access_code="12345678",
        model="H2C",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    return printer


@pytest.fixture
async def archive(db_session, printer):
    from backend.app.models.archive import PrintArchive

    archive = PrintArchive(
        printer_id=printer.id,
        filename="benchy.gcode.3mf",
        file_path="archives/benchy.gcode.3mf",
        file_size=1024,
        status="completed",
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)
    return archive


async def _stored_choice(db_session, item_id):
    """What actually landed in the column, not what the response echoed."""
    from backend.app.models.print_queue import PrintQueueItem

    db_session.expire_all()
    item = await db_session.get(PrintQueueItem, item_id)
    return item.nozzle_rack_choice


@pytest.mark.asyncio
class TestCreate:
    async def test_a_pick_posted_on_create_reaches_the_column(
        self, async_client: AsyncClient, printer, archive, db_session
    ):
        response = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": archive.id,
                # Group 2 to rack position 1, group 1 to position 3 -- the pick
                # BambuStudio dispatched as [16, 1, 18] on 2026-08-13.
                "nozzle_rack_choice": {"2": 1, "1": 3},
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["nozzle_rack_choice"] == {"2": 1, "1": 3}
        assert await _stored_choice(db_session, result["id"]) is not None

    async def test_creating_without_one_leaves_the_column_null(
        self, async_client: AsyncClient, printer, archive, db_session
    ):
        """Null is the signal to assign positions at dispatch."""
        response = await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id})

        assert response.status_code == 200
        assert response.json()["nozzle_rack_choice"] is None
        assert await _stored_choice(db_session, response.json()["id"]) is None


@pytest.mark.asyncio
class TestUpdate:
    async def test_editing_an_item_replaces_its_pick(self, async_client: AsyncClient, printer, archive, db_session):
        created = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": archive.id,
                "nozzle_rack_choice": {"2": 1, "1": 3},
            },
        )
        item_id = created.json()["id"]

        response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"nozzle_rack_choice": {"2": 1, "1": 2}})

        assert response.status_code == 200
        assert response.json()["nozzle_rack_choice"] == {"2": 1, "1": 2}

    async def test_clearing_the_pick_hands_the_choice_back_to_the_dispatcher(
        self, async_client: AsyncClient, printer, archive, db_session
    ):
        created = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": archive.id,
                "nozzle_rack_choice": {"2": 1, "1": 3},
            },
        )
        item_id = created.json()["id"]

        response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"nozzle_rack_choice": None})

        assert response.status_code == 200
        assert response.json()["nozzle_rack_choice"] is None
        assert await _stored_choice(db_session, item_id) is None

    async def test_an_unrelated_edit_does_not_disturb_the_pick(
        self, async_client: AsyncClient, printer, archive, db_session
    ):
        created = await async_client.post(
            "/api/v1/queue/",
            json={
                "printer_id": printer.id,
                "archive_id": archive.id,
                "nozzle_rack_choice": {"2": 1, "1": 3},
            },
        )
        item_id = created.json()["id"]

        response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"manual_start": True})

        assert response.status_code == 200
        assert response.json()["nozzle_rack_choice"] == {"2": 1, "1": 3}


class TestSchemaCoverage:
    def test_create_update_and_response_all_declare_the_field(self):
        """The original bug in one assertion: it was on two of the three.

        A field missing from a request schema is dropped in silence, so there is
        no error anywhere to catch it -- only a print that runs from the wrong
        hotend.
        """
        from backend.app.schemas.print_queue import (
            PrintQueueItemCreate,
            PrintQueueItemResponse,
            PrintQueueItemUpdate,
            QueueVariantCreate,
        )

        for schema in (PrintQueueItemCreate, PrintQueueItemUpdate, PrintQueueItemResponse, QueueVariantCreate):
            assert "nozzle_rack_choice" in schema.model_fields, schema.__name__

    def test_the_create_schema_actually_keeps_a_posted_pick(self):
        from backend.app.schemas.print_queue import PrintQueueItemCreate

        parsed = PrintQueueItemCreate(printer_id=1, archive_id=1, nozzle_rack_choice={"2": 1, "1": 3})
        assert parsed.nozzle_rack_choice == {2: 1, 1: 3}
