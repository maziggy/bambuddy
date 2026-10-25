"""Printer locations (groups) API (#2962).

Locations live on printers (``printers.location``) and, for appearance and
empty locations, in ``printer_locations``. Every write is one server-side
transaction: the page used to send one PATCH per printer from its cached list,
so a failure halfway split a location, and another user's move was undone.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import inspect, select

from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.printer_location import PrinterLocation

BASE = "/api/v1/printer-locations/"


async def _locations(client: AsyncClient) -> dict[str, dict]:
    response = await client.get(BASE)
    assert response.status_code == 200
    return {loc["name"]: loc for loc in response.json()}


async def _printer_location(db_session, printer) -> str | None:
    # A column select reads the row, not the identity map, so nothing needs
    # expiring; the id comes from the identity, which never lazy-loads.
    printer_id = inspect(printer).identity[0]
    return (await db_session.execute(select(Printer.location).where(Printer.id == printer_id))).scalar_one()


@pytest.mark.asyncio
@pytest.mark.integration
class TestList:
    async def test_union_of_rows_and_printer_locations(self, async_client, printer_factory, db_session):
        await printer_factory(name="A", location="Workshop")
        await printer_factory(name="B", location="Workshop")
        await printer_factory(name="C", location="")
        await printer_factory(name="D", location=None)
        db_session.add(PrinterLocation(name="Future rack", icon="home", color="#3b82f6"))
        await db_session.commit()

        locations = await _locations(async_client)

        assert set(locations) == {"Workshop", "Future rack"}
        assert locations["Workshop"]["printer_count"] == 2
        assert locations["Workshop"]["id"] is None
        assert locations["Future rack"] == {
            "id": locations["Future rack"]["id"],
            "name": "Future rack",
            "icon": "home",
            "color": "#3b82f6",
            "printer_count": 0,
        }

    async def test_natural_order(self, async_client, db_session):
        for name in ("Rack 10", "Rack 2", "Office"):
            db_session.add(PrinterLocation(name=name))
        await db_session.commit()

        response = await async_client.get(BASE)

        assert [loc["name"] for loc in response.json()] == ["Office", "Rack 2", "Rack 10"]


@pytest.mark.asyncio
@pytest.mark.integration
class TestCreate:
    async def test_an_empty_location_persists(self, async_client):
        response = await async_client.post(BASE, json={"name": "  Basement  ", "icon": "home", "color": "#ef4444"})

        assert response.status_code == 201
        assert response.json()["name"] == "Basement"
        assert "Basement" in await _locations(async_client)

    async def test_a_location_printers_already_use_gets_a_row(self, async_client, printer_factory):
        await printer_factory(location="Workshop")

        response = await async_client.post(BASE, json={"name": "Workshop", "color": "#22c55e"})

        assert response.status_code == 201
        assert response.json()["printer_count"] == 1
        assert response.json()["color"] == "#22c55e"

    @pytest.mark.parametrize("name", ["Basement", "basement", "BASEMENT"])
    async def test_a_duplicate_is_refused_ignoring_case(self, async_client, name):
        await async_client.post(BASE, json={"name": "Basement"})

        response = await async_client.post(BASE, json={"name": name})

        assert response.status_code == 409

    @pytest.mark.parametrize(
        "body",
        [
            {"name": ""},
            {"name": "   "},
            {"name": "x" * 101},
            {"name": "Ok", "color": "red"},
            {"name": "Ok", "icon": "<script>"},
        ],
    )
    async def test_invalid_input_is_a_422_not_a_500(self, async_client, body):
        response = await async_client.post(BASE, json=body)

        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
class TestUpdate:
    async def test_rename_moves_printers_and_queue_items_together(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        inside = await printer_factory(location="Workshop")
        outside = await printer_factory(location="Office")
        archive = await archive_factory(inside.id)
        pending = PrintQueueItem(
            archive_id=archive.id, target_model="X1C", target_location="Workshop", status="pending"
        )
        done = PrintQueueItem(archive_id=archive.id, target_model="X1C", target_location="Workshop", status="completed")
        db_session.add_all([pending, done])
        await db_session.commit()

        response = await async_client.patch(BASE, json={"name": "Workshop", "new_name": "Garage"})

        assert response.status_code == 200
        assert response.json()["printer_count"] == 1
        assert await _printer_location(db_session, inside) == "Garage"
        assert await _printer_location(db_session, outside) == "Office"
        await db_session.refresh(pending)
        await db_session.refresh(done)
        assert pending.target_location == "Garage"
        # Finished rows too: a batch clones its next run from its newest row,
        # whatever its status.
        assert done.target_location == "Garage"
        assert set(await _locations(async_client)) == {"Garage", "Office"}

    async def test_rename_onto_another_location_is_refused(self, async_client, printer_factory, db_session):
        a = await printer_factory(location="Workshop")
        await printer_factory(location="Office")

        response = await async_client.patch(BASE, json={"name": "Workshop", "new_name": "office"})

        assert response.status_code == 409
        assert await _printer_location(db_session, a) == "Workshop"

    async def test_a_case_only_rename_is_allowed(self, async_client, printer_factory, db_session):
        a = await printer_factory(location="workshop")

        response = await async_client.patch(BASE, json={"name": "workshop", "new_name": "Workshop"})

        assert response.status_code == 200
        assert await _printer_location(db_session, a) == "Workshop"

    async def test_restyle_only_sent_fields(self, async_client):
        await async_client.post(BASE, json={"name": "Shop", "icon": "home", "color": "#ef4444"})

        await async_client.patch(BASE, json={"name": "Shop", "color": "#22c55e"})
        after_color = (await _locations(async_client))["Shop"]
        await async_client.patch(BASE, json={"name": "Shop", "icon": ""})
        after_clear = (await _locations(async_client))["Shop"]

        assert (after_color["icon"], after_color["color"]) == ("home", "#22c55e")
        assert (after_clear["icon"], after_clear["color"]) == (None, "#22c55e")

    async def test_unknown_location_is_a_404(self, async_client):
        response = await async_client.patch(BASE, json={"name": "Nowhere", "color": "#22c55e"})

        assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
class TestDelete:
    async def test_printers_end_up_ungrouped_and_rows_go(self, async_client, printer_factory, db_session):
        a = await printer_factory(location="Workshop")
        b = await printer_factory(location="Office")
        await async_client.post(BASE, json={"name": "Empty"})

        response = await async_client.post(f"{BASE}delete", json={"names": ["Workshop", "Empty", "Nowhere"]})

        assert response.status_code == 200
        assert response.json() == {"deleted": 2, "printers_ungrouped": 1}
        assert await _printer_location(db_session, a) is None
        assert await _printer_location(db_session, b) == "Office"
        assert set(await _locations(async_client)) == {"Office"}

    async def test_pending_jobs_keep_their_target(self, async_client, printer_factory, archive_factory, db_session):
        """Turning them into "any location" would start them where they were
        meant not to run."""
        printer = await printer_factory(location="Workshop")
        archive = await archive_factory(printer.id)
        item = PrintQueueItem(archive_id=archive.id, target_model="X1C", target_location="Workshop", status="pending")
        db_session.add(item)
        await db_session.commit()

        await async_client.post(f"{BASE}delete", json={"names": ["Workshop"]})

        await db_session.refresh(item)
        assert item.target_location == "Workshop"


@pytest.mark.asyncio
@pytest.mark.integration
class TestAssign:
    async def test_moves_only_the_listed_printers(self, async_client, printer_factory, db_session):
        a = await printer_factory(location="Workshop")
        b = await printer_factory(location="Workshop")
        c = await printer_factory(location="Office")

        response = await async_client.post(f"{BASE}assign", json={"printer_ids": [a.id, c.id], "location": "Garage"})

        assert response.status_code == 200
        assert response.json() == {"moved": 2}
        assert await _printer_location(db_session, a) == "Garage"
        assert await _printer_location(db_session, b) == "Workshop"
        assert await _printer_location(db_session, c) == "Garage"

    async def test_null_and_blank_ungroup(self, async_client, printer_factory, db_session):
        a = await printer_factory(location="Workshop")
        b = await printer_factory(location="Workshop")

        await async_client.post(f"{BASE}assign", json={"printer_ids": [a.id], "location": None})
        await async_client.post(f"{BASE}assign", json={"printer_ids": [b.id], "location": "  "})

        assert await _printer_location(db_session, a) is None
        assert await _printer_location(db_session, b) is None

    async def test_an_unknown_printer_moves_nothing(self, async_client, printer_factory, db_session):
        """All or nothing, so a bad id cannot leave half the selection moved."""
        a = await printer_factory(location="Workshop")

        response = await async_client.post(f"{BASE}assign", json={"printer_ids": [a.id, 99999], "location": "Garage"})

        assert response.status_code == 404
        assert await _printer_location(db_session, a) == "Workshop"

    async def test_a_case_variant_of_a_location_is_refused(self, async_client, printer_factory, db_session):
        a = await printer_factory(location="Workshop")
        b = await printer_factory(location=None)

        response = await async_client.post(f"{BASE}assign", json={"printer_ids": [b.id], "location": "workshop"})

        assert response.status_code == 409
        assert await _printer_location(db_session, a) == "Workshop"
        assert await _printer_location(db_session, b) is None


@pytest.mark.asyncio
@pytest.mark.integration
class TestPrinterLocationInput:
    """The printer dialog writes the same column, with the same rules."""

    async def test_too_long_is_a_422(self, async_client, printer_factory):
        printer = await printer_factory()

        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"location": "x" * 101})

        assert response.status_code == 422

    async def test_blank_is_stored_as_no_location(self, async_client, printer_factory, db_session):
        printer = await printer_factory(location="Workshop")

        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"location": "  "})

        assert response.status_code == 200
        assert await _printer_location(db_session, printer) is None


@pytest.mark.asyncio
@pytest.mark.integration
class TestExistingData:
    """Locations stored before the page existed: untrimmed, blank, case
    variants, and on SQLite longer than the column width."""

    async def test_migration_trims_and_blanks(self, test_engine, printer_factory, archive_factory, db_session):
        from backend.app.core.database import _migrate_normalize_printer_locations

        a = await printer_factory(location="Workshop ")
        b = await printer_factory(location="   ")
        c = await printer_factory(location="Office")
        archive = await archive_factory(a.id)
        item = PrintQueueItem(archive_id=archive.id, target_model="X1C", target_location=" Workshop", status="pending")
        db_session.add(item)
        await db_session.commit()

        async with test_engine.begin() as conn:
            await _migrate_normalize_printer_locations(conn)
            # Idempotent: a second run changes nothing.
            await _migrate_normalize_printer_locations(conn)

        assert await _printer_location(db_session, a) == "Workshop"
        assert await _printer_location(db_session, b) is None
        assert await _printer_location(db_session, c) == "Office"
        await db_session.refresh(item)
        assert item.target_location == "Workshop"

    async def test_case_variants_from_before_can_still_be_used(self, async_client, printer_factory, db_session):
        """Refusing would leave no way to move a printer into a location that is
        plainly there, or to style it."""
        await printer_factory(location="Workshop")
        await printer_factory(location="workshop")
        newcomer = await printer_factory(location=None)

        moved = await async_client.post(f"{BASE}assign", json={"printer_ids": [newcomer.id], "location": "Workshop"})
        styled = await async_client.post(BASE, json={"name": "workshop", "color": "#22c55e"})
        third = await async_client.post(BASE, json={"name": "WORKSHOP"})

        assert moved.status_code == 200
        assert await _printer_location(db_session, newcomer) == "Workshop"
        assert styled.status_code == 201
        assert third.status_code == 409

    @pytest.mark.sqlite_only  # an over-length value only SQLite stores
    async def test_an_overlong_stored_name_can_be_renamed_and_deleted(self, async_client, printer_factory, db_session):
        """SQLite never enforced VARCHAR(100)."""
        long_name = "L" * 150
        a = await printer_factory(location=long_name)
        b = await printer_factory(location=long_name + "2")

        renamed = await async_client.patch(BASE, json={"name": long_name, "new_name": "Short"})
        deleted = await async_client.post(f"{BASE}delete", json={"names": [long_name + "2"]})

        assert renamed.status_code == 200
        assert await _printer_location(db_session, a) == "Short"
        assert deleted.json() == {"deleted": 1, "printers_ungrouped": 1}
        assert await _printer_location(db_session, b) is None


class TestPrinterCreateLocation:
    def test_create_trims_blanks_and_limits(self):
        from pydantic import ValidationError

        from backend.app.schemas.printer import PrinterCreate

        base = {"name": "P", "serial_number": "00M09A000000001", "ip_address": "10.0.0.1", "access_code": "1234"}
        assert PrinterCreate(**base, location="  Shop ").location == "Shop"
        assert PrinterCreate(**base, location=" ").location is None
        with pytest.raises(ValidationError):
            PrinterCreate(**base, location="x" * 101)
