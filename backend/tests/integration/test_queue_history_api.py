"""Queue history, paged on the server (GET /queue/history, POST /queue/history/clear).

The Queue page used to load the whole queue every five seconds and split off
the history in the browser. On a print farm that was thousands of rows and
stalled the server for over a minute. These tests pin that the server-side
version shows the same rows, in the same order, under the same filters, and
that clearing it keeps the rule the one-by-one DELETEs followed (#2960).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

URL = "/api/v1/queue/history"
T0 = datetime(2026, 10, 1, 12, 0)


@pytest.fixture
def queue_item_factory(db_session):
    position = [0]

    async def _create(archive_id: int | None, printer_id: int | None = None, **kwargs):
        from backend.app.models.print_queue import PrintQueueItem

        position[0] += 1
        defaults = {
            "archive_id": archive_id,
            "printer_id": printer_id,
            "status": "completed",
            "position": position[0],
            "created_at": T0,
        }
        defaults.update(kwargs)
        item = PrintQueueItem(**defaults)
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    return _create


async def _ids(async_client: AsyncClient, **params) -> list[int]:
    response = await async_client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["items"]]


class TestListing:
    async def test_only_history_statuses(self, async_client, printer_factory, archive_factory, queue_item_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        history = [
            await queue_item_factory(archive.id, printer.id, status=status)
            for status in ("completed", "failed", "skipped", "cancelled")
        ]
        await queue_item_factory(archive.id, printer.id, status="pending")
        await queue_item_factory(archive.id, printer.id, status="printing")

        response = await async_client.get(URL)

        assert response.status_code == 200, response.text
        assert sorted(row["id"] for row in response.json()["items"]) == sorted(i.id for i in history)
        assert response.json()["total"] == 4

    async def test_newest_first_by_completion_then_creation(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        old = await queue_item_factory(archive.id, printer.id, completed_at=T0)
        never_finished = await queue_item_factory(
            archive.id, printer.id, status="cancelled", created_at=T0 + timedelta(hours=2)
        )
        new = await queue_item_factory(archive.id, printer.id, completed_at=T0 + timedelta(hours=3))

        assert await _ids(async_client) == [new.id, never_finished.id, old.id]
        assert await _ids(async_client, reverse=True) == [old.id, never_finished.id, new.id]

    async def test_sort_by_name_is_a_to_z_and_ignores_case(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        printer = await printer_factory()
        names = {}
        for name in ("bracket", "Anchor", "Clip"):
            archive = await archive_factory(printer.id, print_name=name)
            names[name] = (await queue_item_factory(archive.id, printer.id)).id

        assert await _ids(async_client, sort_by="name") == [names["Anchor"], names["bracket"], names["Clip"]]
        assert await _ids(async_client, sort_by="name", reverse=True) == [
            names["Clip"],
            names["bracket"],
            names["Anchor"],
        ]

    async def test_sort_by_name_skips_a_deleted_archive(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        # A deleted archive's name isn't shown on the row, so it can't order it
        printer = await printer_factory()
        gone = await archive_factory(printer.id, print_name="Aaa", filename="zzz.3mf", deleted_at=T0)
        live = await archive_factory(printer.id, print_name="Mmm")
        gone_row = await queue_item_factory(gone.id, printer.id)
        live_row = await queue_item_factory(live.id, printer.id)

        # No archive and no library file: sorts as an empty name, first
        assert await _ids(async_client, sort_by="name") == [gone_row.id, live_row.id]

    async def test_sort_by_printer(self, async_client, printer_factory, archive_factory, queue_item_factory):
        zeta, alpha = await printer_factory(name="Zeta"), await printer_factory(name="alpha")
        archive = await archive_factory(zeta.id)
        on_zeta = await queue_item_factory(archive.id, zeta.id)
        on_alpha = await queue_item_factory(archive.id, alpha.id)

        assert await _ids(async_client, sort_by="printer") == [on_alpha.id, on_zeta.id]

    async def test_pages_with_total(self, async_client, printer_factory, archive_factory, queue_item_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        rows = [
            await queue_item_factory(archive.id, printer.id, completed_at=T0 + timedelta(minutes=i)) for i in range(5)
        ]
        newest_first = [r.id for r in reversed(rows)]

        response = await async_client.get(URL, params={"limit": 2, "offset": 2})

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["items"]] == newest_first[2:4]
        assert response.json()["total"] == 5

    async def test_page_brings_the_other_runs_of_its_batches(
        self, async_client, db_session, printer_factory, archive_factory, queue_item_factory
    ):
        from backend.app.models.print_batch import PrintBatch

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        batch = PrintBatch(name="Order", status="active")
        db_session.add(batch)
        await db_session.commit()
        older_run = await queue_item_factory(archive.id, printer.id, batch_id=batch.id, completed_at=T0)
        await queue_item_factory(archive.id, printer.id, completed_at=T0 + timedelta(hours=1))
        newer_run = await queue_item_factory(
            archive.id, printer.id, batch_id=batch.id, completed_at=T0 + timedelta(hours=2)
        )

        response = await async_client.get(URL, params={"limit": 1})

        assert response.status_code == 200, response.text
        # The page first, then the rest of its batch; the unbatched row stays out
        assert [row["id"] for row in response.json()["items"]] == [newer_run.id, older_run.id]
        assert response.json()["total"] == 3


class TestFilters:
    async def test_printer_and_unassigned(self, async_client, printer_factory, archive_factory, queue_item_factory):
        a, b = await printer_factory(), await printer_factory()
        archive = await archive_factory(a.id)
        on_a = await queue_item_factory(archive.id, a.id)
        await queue_item_factory(archive.id, b.id)
        unassigned = await queue_item_factory(archive.id, None, status="cancelled", target_model="P2S")

        assert await _ids(async_client, printer_id=a.id) == [on_a.id]
        assert await _ids(async_client, printer_id=-1) == [unassigned.id]

    async def test_status(self, async_client, printer_factory, archive_factory, queue_item_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        failed = await queue_item_factory(archive.id, printer.id, status="failed")
        await queue_item_factory(archive.id, printer.id, status="completed")
        await queue_item_factory(archive.id, printer.id, status="pending")

        assert await _ids(async_client, status="failed") == [failed.id]
        # An active status selected on the page leaves no history to show
        assert await _ids(async_client, status="pending") == []

    async def test_location_by_target_location_or_printer(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        hall, lab = await printer_factory(location="Hall"), await printer_factory(location="Lab")
        archive = await archive_factory(hall.id)
        in_hall = await queue_item_factory(archive.id, hall.id)
        await queue_item_factory(archive.id, lab.id)
        model_based_hall = await queue_item_factory(archive.id, None, target_model="P2S", target_location="Hall")
        # A target location wins over the printer's own
        await queue_item_factory(archive.id, hall.id, target_location="Lab")

        assert sorted(await _ids(async_client, location="Hall")) == sorted([in_hall.id, model_based_hall.id])

    async def test_location_filter_with_printer_sort(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        # The printer sort joins printers; the location check must not get tangled in it
        hall, lab = await printer_factory(name="B", location="Hall"), await printer_factory(name="A", location="Lab")
        archive = await archive_factory(hall.id)
        in_hall = await queue_item_factory(archive.id, hall.id)
        await queue_item_factory(archive.id, lab.id)

        assert await _ids(async_client, location="Hall", sort_by="printer") == [in_hall.id]

    async def test_locations_lists_every_target_location(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        await queue_item_factory(archive.id, None, target_model="P2S", target_location="Hall")
        await queue_item_factory(archive.id, None, target_model="P2S", target_location="Annex")
        await queue_item_factory(archive.id, None, target_model="P2S", target_location="")
        await queue_item_factory(archive.id, printer.id)

        response = await async_client.get(URL, params={"status": "failed", "limit": 1})

        assert response.status_code == 200, response.text
        # Independent of the filters and the page
        assert response.json()["locations"] == ["Annex", "Hall"]


class TestActiveListing:
    async def test_list_takes_several_statuses(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        pending = await queue_item_factory(archive.id, printer.id, status="pending")
        printing = await queue_item_factory(archive.id, printer.id, status="printing")
        await queue_item_factory(archive.id, printer.id, status="completed")

        response = await async_client.get("/api/v1/queue/", params={"status": "pending,printing"})

        assert response.status_code == 200, response.text
        assert sorted(row["id"] for row in response.json()) == sorted([pending.id, printing.id])

    async def test_list_single_status_unchanged(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        pending = await queue_item_factory(archive.id, printer.id, status="pending")
        await queue_item_factory(archive.id, printer.id, status="completed")

        response = await async_client.get("/api/v1/queue/", params={"status": "pending"})

        assert [row["id"] for row in response.json()] == [pending.id]


class TestClear:
    async def test_clears_only_history_under_the_filters(
        self, async_client, db_session, printer_factory, archive_factory, queue_item_factory
    ):
        from backend.app.models.print_queue import PrintQueueItem

        a, b = await printer_factory(), await printer_factory()
        archive = await archive_factory(a.id)
        done_on_a = await queue_item_factory(archive.id, a.id)
        failed_on_a = await queue_item_factory(archive.id, a.id, status="failed")
        done_on_b = await queue_item_factory(archive.id, b.id)
        pending_on_a = await queue_item_factory(archive.id, a.id, status="pending")

        ids = {r: r.id for r in (done_on_a, failed_on_a, done_on_b, pending_on_a)}

        response = await async_client.post(f"{URL}/clear", params={"printer_id": a.id})

        assert response.status_code == 200, response.text
        assert response.json() == {"cleared": 2, "kept": 0}
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, ids[done_on_a]) is None
        assert await db_session.get(PrintQueueItem, ids[failed_on_a]) is None
        assert await db_session.get(PrintQueueItem, ids[done_on_b]) is not None
        assert await db_session.get(PrintQueueItem, ids[pending_on_a]) is not None

    async def test_keeps_an_orders_last_run_as_cancelled(
        self, async_client, db_session, printer_factory, archive_factory
    ):
        """The same rule as deleting the run on its own (#2960)."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        order = await async_client.post(
            "/api/v1/queue/batches",
            json={"name": "Order", "archive_id": archive.id, "plates": [{"plate_id": 1, "quantity_target": 3}]},
        )
        assert order.status_code == 200, order.text
        runs = []
        for _ in range(2):
            created = await async_client.post(
                "/api/v1/queue/",
                json={
                    "printer_id": printer.id,
                    "archive_id": archive.id,
                    "batch_id": order.json()["id"],
                    "plate_id": 1,
                },
            )
            assert created.status_code == 200, created.text
            runs.append(created.json()["id"])
        for run_id in runs:
            row = await db_session.get(PrintQueueItem, run_id)
            row.status = "failed"
        await db_session.commit()

        response = await async_client.post(f"{URL}/clear")

        assert response.status_code == 200, response.text
        assert response.json() == {"cleared": 1, "kept": 1}
        db_session.expire_all()
        # The later of the two, in queue order, is the one left to clone
        assert await db_session.get(PrintQueueItem, runs[0]) is None
        survivor = await db_session.get(PrintQueueItem, runs[1])
        assert survivor is not None
        assert survivor.status == "cancelled"


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "histadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "histadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _user(
    async_client: AsyncClient, admin_jwt: str, username: str, permissions: list[str], printer_ids=None
) -> tuple[str, int]:
    groups = []
    perms = await async_client.post(
        "/api/v1/groups/", headers=_auth(admin_jwt), json={"name": f"perms_{username}", "permissions": permissions}
    )
    assert perms.status_code == 201, perms.text
    groups.append(perms.json()["id"])
    if printer_ids is not None:
        team = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin_jwt),
            json={"name": f"team_{username}", "permissions": [], "restrict_printers": True, "printer_ids": printer_ids},
        )
        assert team.status_code == 201, team.text
        groups.append(team.json()["id"])
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin_jwt),
        json={"username": username, "password": "UserPass1!", "group_ids": groups},
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"], created.json()["id"]


class TestAccess:
    async def test_read_own_sees_only_own_history(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        admin = await _admin_token(async_client)
        jwt, user_id = await _user(async_client, admin, "owner", ["queue:read_own"])
        mine = await queue_item_factory(archive.id, printer.id, created_by_id=user_id)
        await queue_item_factory(archive.id, printer.id)

        response = await async_client.get(URL, headers=_auth(jwt))

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["items"]] == [mine.id]
        assert response.json()["total"] == 1

    async def test_team_member_sees_only_team_printers(
        self, async_client, printer_factory, archive_factory, queue_item_factory
    ):
        a, b = await printer_factory(), await printer_factory()
        archive = await archive_factory(a.id)
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "member", ["queue:read_all"], printer_ids=[a.id])
        on_a = await queue_item_factory(archive.id, a.id)
        await queue_item_factory(archive.id, b.id)

        response = await async_client.get(URL, headers=_auth(jwt))

        assert [row["id"] for row in response.json()["items"]] == [on_a.id]

    async def test_needs_queue_read(self, async_client):
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "nobody", ["printers:read"])

        assert (await async_client.get(URL, headers=_auth(jwt))).status_code == 403
        assert (await async_client.post(f"{URL}/clear", headers=_auth(jwt))).status_code == 403

    async def test_read_own_clears_only_own_history_even_with_delete_all(
        self, async_client, db_session, printer_factory, archive_factory, queue_item_factory
    ):
        # The page only ever listed, and so deleted, the rows its user could see
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        admin = await _admin_token(async_client)
        jwt, user_id = await _user(async_client, admin, "reader", ["queue:read_own", "queue:delete_all"])
        mine = await queue_item_factory(archive.id, printer.id, created_by_id=user_id)
        theirs = await queue_item_factory(archive.id, printer.id)
        mine_id, theirs_id = mine.id, theirs.id

        response = await async_client.post(f"{URL}/clear", headers=_auth(jwt))

        assert response.status_code == 200, response.text
        assert response.json() == {"cleared": 1, "kept": 0}
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, mine_id) is None
        assert await db_session.get(PrintQueueItem, theirs_id) is not None

    async def test_clear_needs_queue_read(self, async_client):
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "deleter", ["queue:delete_all"])

        assert (await async_client.post(f"{URL}/clear", headers=_auth(jwt))).status_code == 403

    async def test_delete_own_clears_only_own_history(
        self, async_client, db_session, printer_factory, archive_factory, queue_item_factory
    ):
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        admin = await _admin_token(async_client)
        jwt, user_id = await _user(async_client, admin, "owner", ["queue:read_all", "queue:delete_own"])
        mine = await queue_item_factory(archive.id, printer.id, created_by_id=user_id)
        theirs = await queue_item_factory(archive.id, printer.id)

        mine_id, theirs_id = mine.id, theirs.id

        response = await async_client.post(f"{URL}/clear", headers=_auth(jwt))

        assert response.status_code == 200, response.text
        assert response.json() == {"cleared": 1, "kept": 0}
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, mine_id) is None
        assert await db_session.get(PrintQueueItem, theirs_id) is not None
