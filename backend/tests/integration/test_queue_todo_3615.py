"""GET /queue/todo: what the waiting queue needs a person for (#3615).

Printer state is read live and job state comes from the rows plus the
structured holds the scheduler records, never from waiting-reason text.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import scheduler
from backend.app.services.queue_todo import FilamentHold, TodoFilament

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

from backend.tests.integration import test_api_key_queue_acts_as_owner as owner_tests  # noqa: E402

world = owner_tests.world

URL = "/api/v1/queue/todo"
RED = TodoFilament("PLA", "#FF0000", "Red")
BLUE = TodoFilament("PETG", "#0000FF", "Blue")


@pytest.fixture(autouse=True)
def _quiet_scheduler_state():
    """No holds, no failed power-ons, no sensors, no plate waits unless a test says so."""
    scheduler._filament_holds = {}
    scheduler._wake_failures = {}
    with (
        patch("backend.app.api.routes.print_queue.ha_sensor_manager.blocked_printers", AsyncMock(return_value={})),
        patch(
            "backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", MagicMock(return_value=False)
        ),
    ):
        yield
    scheduler._filament_holds = {}


@pytest.fixture
def make_item(db_session, archive_factory):
    position = [0]

    async def _make(printer_id=None, name="Job", **kwargs):
        from backend.app.models.print_queue import PrintQueueItem

        anchor = printer_id
        if anchor is None:
            from sqlalchemy import select

            from backend.app.models.printer import Printer

            anchor = (await db_session.execute(select(Printer.id).limit(1))).scalar_one()
        archive = await archive_factory(anchor, print_name=name)
        position[0] += 1
        values = {"archive_id": archive.id, "printer_id": printer_id, "status": "pending", "position": position[0]}
        values.update(kwargs)
        item = PrintQueueItem(**values)
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    return _make


def _only_entries(body: dict) -> list[dict]:
    return [entry for group in body["groups"] for entry in group["entries"]]


class TestTodoEntries:
    async def test_empty_queue(self, async_client: AsyncClient, printer_factory):
        await printer_factory()
        body = (await async_client.get(URL)).json()
        assert body == {"count": 0, "groups": []}

    async def test_start_staged_is_grouped_per_printer(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A")
        item = await make_item(a.id, name="Benchy", manual_start=True)
        body = (await async_client.get(URL)).json()
        assert body["count"] == 1
        group = body["groups"][0]
        assert (group["printer_id"], group["printer_name"], group["target_model"]) == (a.id, "A", None)
        assert group["entries"] == [
            {
                "kind": "start_staged",
                "jobs": [{"id": item.id, "name": "Benchy", "can_start": True}],
                "printers": [],
                "filaments": [],
                "detail": None,
            }
        ]

    async def test_staged_model_job_goes_to_the_model_group(self, async_client, printer_factory, make_item):
        await printer_factory(name="A", model="X1C")
        item = await make_item(None, manual_start=True, target_model="X1C")
        group = (await async_client.get(URL)).json()["groups"][0]
        assert (group["printer_id"], group["target_model"]) == (None, "X1C")
        assert group["entries"][0]["jobs"][0]["id"] == item.id

    async def test_only_pending_jobs_count(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        await make_item(a.id, manual_start=True, status="printing")
        assert (await async_client.get(URL)).json()["count"] == 0

    async def test_three_jobs_with_the_same_filament_merge_into_one_entry(
        self, async_client, printer_factory, make_item
    ):
        a = await printer_factory(name="A")
        items = [await make_item(a.id, name=f"J{i}") for i in range(3)]
        scheduler._filament_holds = {i.id: [FilamentHold(i.id, a.id, None, (RED,))] for i in items}
        body = (await async_client.get(URL)).json()
        assert body["count"] == 1
        entry = _only_entries(body)[0]
        assert entry["kind"] == "load_filament"
        assert [j["id"] for j in entry["jobs"]] == [i.id for i in items]
        assert entry["filaments"] == [
            {"type": "PLA", "color": "#FF0000", "color_name": "Red", "no_stock": False, "nozzle": None}
        ]

    async def test_dual_nozzle_filaments_name_their_nozzle(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A", model="H2D")
        item = await make_item(a.id)
        black = ("PETG", "#000000", "Black")
        scheduler._filament_holds = {
            item.id: [
                FilamentHold(item.id, a.id, None, (TodoFilament(*black, nozzle=1), TodoFilament(*black, nozzle=0)))
            ]
        }
        (entry,) = _only_entries((await async_client.get(URL)).json())
        assert [(f["type"], f["color_name"], f["nozzle"]) for f in entry["filaments"]] == [
            ("PETG", "Black", 0),
            ("PETG", "Black", 1),
        ]

    async def test_different_filaments_stay_separate(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A")
        one, two = await make_item(a.id), await make_item(a.id)
        scheduler._filament_holds = {
            one.id: [FilamentHold(one.id, a.id, None, (RED,))],
            two.id: [FilamentHold(two.id, a.id, None, (BLUE,))],
        }
        body = (await async_client.get(URL)).json()
        assert body["count"] == 2
        assert len(body["groups"]) == 1

    async def test_stale_holds_of_jobs_that_left_the_queue_are_ignored(self, async_client, printer_factory):
        a = await printer_factory()
        scheduler._filament_holds = {999: [FilamentHold(999, a.id, None, (RED,))]}
        assert (await async_client.get(URL)).json()["count"] == 0

    async def test_plate_clear_needs_a_waiting_job(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A")
        await printer_factory(name="B")  # nothing waits for it
        item = await make_item(a.id, name="Waiting")
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
        ):
            body = (await async_client.get(URL)).json()
        assert [g["printer_id"] for g in body["groups"]] == [a.id]  # B has nothing waiting
        entry = _only_entries(body)[0]
        assert entry["kind"] == "plate_clear"
        assert entry["jobs"] == [{"id": item.id, "name": "Waiting", "can_start": False}]

    async def test_plate_clear_off_for_the_printer_shows_nothing(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        await make_item(a.id)
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: False),
        ):
            assert (await async_client.get(URL)).json()["count"] == 0

    async def test_model_job_makes_every_matching_printer_a_candidate(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A", model="X1C")
        await printer_factory(name="P", model="P1S")
        item = await make_item(None, target_model="X1C")
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
        ):
            body = (await async_client.get(URL)).json()
        assert [g["printer_id"] for g in body["groups"]] == [a.id]
        assert _only_entries(body)[0]["jobs"][0]["id"] == item.id

    async def test_model_job_with_a_location_only_counts_printers_there(self, async_client, printer_factory, make_item):
        await printer_factory(name="A", model="X1C", location="Lab")
        b = await printer_factory(name="B", model="X1C", location="Shop")
        await make_item(None, target_model="X1C", target_location="Shop")
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
        ):
            body = (await async_client.get(URL)).json()
        assert [g["printer_id"] for g in body["groups"]] == [b.id]

    async def test_sensor_interlock_carries_the_sensor_name(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        await make_item(a.id)
        with patch(
            "backend.app.api.routes.print_queue.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value={a.id: "Enclosure Door"}),
        ):
            body = (await async_client.get(URL)).json()
        entry = _only_entries(body)[0]
        assert (entry["kind"], entry["detail"]) == ("sensor_interlock", "Enclosure Door")

    async def test_power_on_failed_from_the_scheduler(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        await make_item(a.id)
        with patch.object(scheduler, "power_on_failed_printers", return_value={a.id}):
            body = (await async_client.get(URL)).json()
        assert _only_entries(body)[0]["kind"] == "power_on_failed"

    async def test_printer_entries_without_a_waiting_job_are_dropped(self, async_client, printer_factory):
        a = await printer_factory()
        with patch.object(scheduler, "power_on_failed_printers", return_value={a.id}):
            assert (await async_client.get(URL)).json()["count"] == 0

    async def test_entries_inside_a_group_keep_the_documented_order(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A")
        item = await make_item(a.id, manual_start=True)
        scheduler._filament_holds = {item.id: [FilamentHold(item.id, a.id, None, (RED,))]}
        with (
            patch(
                "backend.app.api.routes.print_queue.ha_sensor_manager.blocked_printers",
                AsyncMock(return_value={a.id: "Door"}),
            ),
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
            patch.object(scheduler, "power_on_failed_printers", return_value={a.id}),
        ):
            body = (await async_client.get(URL)).json()
        assert [e["kind"] for e in _only_entries(body)] == [
            "plate_clear",
            "sensor_interlock",
            "power_on_failed",
            "load_filament",
            "start_staged",
        ]
        assert body["count"] == 5

    async def test_groups_follow_the_queue_position_of_their_first_job(self, async_client, printer_factory, make_item):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        await make_item(b.id, manual_start=True)
        await make_item(a.id, manual_start=True)
        body = (await async_client.get(URL)).json()
        assert [g["printer_id"] for g in body["groups"]] == [b.id, a.id]


class TestJobNames:
    """A job is called what its queue row calls it (queueItemDisplayName in the frontend)."""

    async def _names(self, async_client) -> tuple[dict, dict]:
        (row,) = (await async_client.get("/api/v1/queue/?status=pending")).json()
        (job,) = _only_entries((await async_client.get(URL)).json())[0]["jobs"]
        return row, job

    async def test_cross_model_job_is_its_first_file_plus_the_number_of_others(
        self, async_client, db_session, printer_factory
    ):
        from backend.app.models.library import LibraryFile
        from backend.app.models.print_queue import PrintQueueVariant

        await printer_factory(name="A", model="X1C")
        files = [
            LibraryFile(
                filename=f"part_{model}.gcode.3mf",
                file_path=f"/library/part_{model}.gcode.3mf",
                file_type="3mf",
                file_size=10,
                file_metadata={"print_name": "Part", "sliced_for_model": model},
            )
            for model in ("X1C", "P1S", "A1")
        ]
        db_session.add_all(files)
        await db_session.flush()
        item = PrintQueueItem(status="pending", position=1, target_model="X1C", manual_start=True)
        item.variants = [
            PrintQueueVariant(position=i, library_file_id=f.id, target_model=model)
            for i, (f, model) in enumerate(zip(files, ("X1C", "P1S", "A1"), strict=True))
        ]
        db_session.add(item)
        await db_session.commit()

        row, job = await self._names(async_client)

        # The row has no archive or library name and names the first candidate's file.
        assert (row["archive_name"], row["library_file_name"]) == (None, None)
        assert [v["filename"] for v in row["variants"]][0] == "part_X1C.gcode.3mf"
        assert job["name"] == "part_X1C.gcode.3mf +2 more"

    async def test_a_job_whose_archive_was_deleted_is_file_number_of_the_archive(
        self, async_client, db_session, printer_factory, archive_factory, make_item
    ):
        from datetime import datetime, timezone

        from backend.app.models.archive import PrintArchive

        a = await printer_factory(name="A")
        await archive_factory(a.id)  # so the archive id is not the item id
        item = await make_item(a.id, name="Gone", manual_start=True)
        assert item.archive_id != item.id
        archive = await db_session.get(PrintArchive, item.archive_id)
        archive.deleted_at = datetime.now(timezone.utc)
        await db_session.commit()

        row, job = await self._names(async_client)

        assert (row["archive_name"], row["library_file_name"], row["archive_deleted"]) == (None, None, True)
        assert job["name"] == f"File #{item.archive_id}"


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


class TestTodoAccess:
    async def _setup(self, async_client, permissions, printer_ids=None):
        await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "todoadmin", "admin_password": "AdminPass1!"},
        )
        admin = (
            await async_client.post("/api/v1/auth/login", json={"username": "todoadmin", "password": "AdminPass1!"})
        ).json()["access_token"]
        groups = []
        perms = await async_client.post(
            "/api/v1/groups/", headers=_auth(admin), json={"name": "perms", "permissions": permissions}
        )
        groups.append(perms.json()["id"])
        if printer_ids is not None:
            team = await async_client.post(
                "/api/v1/groups/",
                headers=_auth(admin),
                json={"name": "team", "permissions": [], "restrict_printers": True, "printer_ids": printer_ids},
            )
            groups.append(team.json()["id"])
        created = await async_client.post(
            "/api/v1/users/",
            headers=_auth(admin),
            json={"username": "member", "password": "UserPass1!", "group_ids": groups},
        )
        self.member_id = created.json()["id"]
        member = (
            await async_client.post("/api/v1/auth/login", json={"username": "member", "password": "UserPass1!"})
        ).json()["access_token"]
        return admin, member

    @staticmethod
    async def _add_user(async_client, admin, username, group_ids=()) -> int:
        """Another real user (jobs point at their creator by foreign key); returns the id."""
        created = await async_client.post(
            "/api/v1/users/",
            headers=_auth(admin),
            json={"username": username, "password": "UserPass1!", "group_ids": list(group_ids)},
        )
        assert created.status_code in (200, 201), created.text
        return created.json()["id"]

    async def test_403_without_queue_read(self, async_client, printer_factory):
        await printer_factory()
        _, member = await self._setup(async_client, ["printers:read"])
        assert (await async_client.get(URL, headers=_auth(member))).status_code == 403

    async def test_scope_hides_other_printers_and_filters_model_jobs(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A", model="X1C")
        b = await printer_factory(name="B", model="P1S")
        on_a = await make_item(a.id, name="OnA", manual_start=True)
        on_b = await make_item(b.id, name="OnB", manual_start=True)
        model_b = await make_item(None, name="AnyP1S", manual_start=True, target_model="P1S")
        scheduler._filament_holds = {on_b.id: [FilamentHold(on_b.id, b.id, None, (RED,))]}
        admin, member = await self._setup(async_client, ["queue:read_all"], printer_ids=[a.id])
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
        ):
            body = (await async_client.get(URL, headers=_auth(member))).json()
            admin_body = (await async_client.get(URL, headers=_auth(admin))).json()
        # The queue list shows a scoped user the "any model" job, so does this list;
        # printer B never appears and the job on B is invisible.
        assert {g["printer_id"] for g in body["groups"]} <= {a.id, None}
        names = {j["name"] for e in _only_entries(body) for j in e["jobs"]}
        assert "OnA" in names and "OnB" not in names
        assert all(e["kind"] != "load_filament" for e in _only_entries(body))
        assert {g["printer_id"] for g in admin_body["groups"]} == {a.id, b.id, None}
        assert on_a.id != model_b.id


class TestModelJobCreatorScope:
    """A model job's printer entries follow its creator's printers, as the scheduler does (#1727)."""

    async def _printer_entries(self, async_client, token, printer_ids):
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
            patch(
                "backend.app.api.routes.print_queue.ha_sensor_manager.blocked_printers",
                AsyncMock(return_value=dict.fromkeys(printer_ids, "Door")),
            ),
            patch.object(scheduler, "power_on_failed_printers", return_value=set(printer_ids)),
        ):
            body = (await async_client.get(URL, headers=_auth(token))).json()
        return {
            group["printer_id"]: {entry["kind"]: [job["name"] for job in entry["jobs"]] for entry in group["entries"]}
            for group in body["groups"]
        }

    async def test_admin_sees_no_printer_entry_where_the_creator_has_no_access(
        self, async_client, printer_factory, make_item
    ):
        a = await printer_factory(name="A", model="X1C")
        b = await printer_factory(name="B", model="X1C")
        helper = TestTodoAccess()
        admin, _ = await helper._setup(async_client, ["queue:read_all"], printer_ids=[a.id])
        free_id = await helper._add_user(async_client, admin, "free")
        await make_item(None, name="Limited", target_model="X1C", created_by_id=helper.member_id)
        await make_item(None, name="Free", target_model="X1C", created_by_id=free_id)

        groups = await self._printer_entries(async_client, admin, [a.id, b.id])

        kinds = ("plate_clear", "sensor_interlock", "power_on_failed")
        # The scheduler never sends "Limited" to B, so nothing on B is listed for it.
        assert groups[a.id] == dict.fromkeys(kinds, ["Limited", "Free"])
        assert groups[b.id] == dict.fromkeys(kinds, ["Free"])

    async def test_a_printer_only_out_of_scope_jobs_match_gets_no_group(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A", model="X1C")
        b = await printer_factory(name="B", model="X1C")
        helper = TestTodoAccess()
        admin, _ = await helper._setup(async_client, ["queue:read_all"], printer_ids=[a.id])
        await make_item(None, name="Limited", target_model="X1C", created_by_id=helper.member_id)

        groups = await self._printer_entries(async_client, admin, [a.id, b.id])

        assert set(groups) == {a.id}

    async def test_the_viewer_still_sees_only_their_own_printers(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A", model="X1C")
        b = await printer_factory(name="B", model="X1C")
        helper = TestTodoAccess()
        admin, member = await helper._setup(async_client, ["queue:read_all"], printer_ids=[a.id])
        free_id = await helper._add_user(async_client, admin, "free")
        await make_item(None, name="Free", target_model="X1C", created_by_id=free_id)

        groups = await self._printer_entries(async_client, member, [a.id, b.id])

        assert set(groups) == {a.id}


class TestModelJobFilament:
    async def test_model_job_across_three_printers_is_one_entry_with_the_fewest_missing(
        self, async_client, printer_factory, make_item
    ):
        a = await printer_factory(name="A", model="X1C")
        b = await printer_factory(name="B", model="X1C")
        c = await printer_factory(name="C", model="X1C")
        item = await make_item(None, target_model="X1C")
        scheduler._filament_holds = {
            item.id: [
                FilamentHold(item.id, a.id, "X1C", (RED, BLUE)),
                FilamentHold(item.id, b.id, "X1C", (BLUE,)),
                FilamentHold(item.id, c.id, "X1C", (BLUE,)),
            ]
        }
        body = (await async_client.get(URL)).json()
        assert body["count"] == 1
        (group,) = body["groups"]
        assert (group["printer_id"], group["target_model"]) == (None, "X1C")
        (entry,) = group["entries"]
        assert entry["kind"] == "load_filament"
        assert [f["type"] for f in entry["filaments"]] == ["PETG"]
        assert entry["printers"] == [{"id": b.id, "name": "B"}, {"id": c.id, "name": "C"}]

    async def test_a_hold_on_an_out_of_scope_printer_is_dropped_but_the_model_hold_shows(
        self, async_client, printer_factory, make_item
    ):
        a = await printer_factory(name="A", model="X1C")
        b = await printer_factory(name="B", model="X1C")
        gone = await make_item(None, name="Gone", target_model="X1C")
        shown = await make_item(None, name="Shown", target_model="X1C")
        scheduler._filament_holds = {
            gone.id: [FilamentHold(gone.id, b.id, "X1C", (RED,))],
            shown.id: [FilamentHold(shown.id, None, "X1C", (BLUE,))],
        }
        helper = TestTodoAccess()
        _, member = await helper._setup(async_client, ["queue:read_all"], printer_ids=[a.id])
        body = (await async_client.get(URL, headers=_auth(member))).json()
        entries = [e for e in _only_entries(body) if e["kind"] == "load_filament"]
        assert [[j["name"] for j in e["jobs"]] for e in entries] == [["Shown"]]
        assert entries[0]["printers"] == []


class TestTodoStateEntries:
    async def test_offline_printer_awaiting_plate_clear_is_listed(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A")
        await make_item(a.id)
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_connected", MagicMock(return_value=False)),
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
        ):
            body = (await async_client.get(URL)).json()
        assert _only_entries(body)[0]["kind"] == "plate_clear"

    async def test_pinned_power_on_failure_is_listed(self, async_client, printer_factory, make_item):
        a = await printer_factory(name="A")
        await make_item(a.id)
        scheduler._power_failed_pinned = {a.id}
        try:
            body = (await async_client.get(URL)).json()
        finally:
            scheduler._power_failed_pinned = set()
        assert _only_entries(body)[0]["kind"] == "power_on_failed"


class TestCanStart:
    async def test_admin_can_start_a_staged_job(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        await make_item(a.id, manual_start=True, created_by_id=None)
        body = (await async_client.get(URL)).json()
        assert _only_entries(body)[0]["jobs"][0]["can_start"] is True

    async def _can_start(self, async_client, printer_factory, make_item, permissions, own):
        a = await printer_factory()
        helper = TestTodoAccess()
        _, member = await helper._setup(async_client, permissions)
        owner = helper.member_id if own else None
        await make_item(a.id, manual_start=True, created_by_id=owner)
        entry = _only_entries((await async_client.get(URL, headers=_auth(member))).json())[0]
        return entry["jobs"][0]["can_start"]

    async def test_own_job_with_start_unreviewed(self, async_client, printer_factory, make_item):
        perms = ["queue:read_all", "queue:update_own", "queue:start_unreviewed"]
        assert await self._can_start(async_client, printer_factory, make_item, perms, own=True) is True

    async def test_awaiting_review_cannot_be_started_by_its_owner(self, async_client, printer_factory, make_item):
        perms = ["queue:read_all", "queue:update_own"]
        assert await self._can_start(async_client, printer_factory, make_item, perms, own=True) is False

    async def test_no_update_permission_cannot_start(self, async_client, printer_factory, make_item):
        perms = ["queue:read_all"]
        assert await self._can_start(async_client, printer_factory, make_item, perms, own=True) is False

    async def test_someone_elses_job_needs_update_all(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        helper = TestTodoAccess()
        _, member = await helper._setup(async_client, ["queue:read_all", "queue:update_own", "queue:start_unreviewed"])
        await make_item(a.id, manual_start=True, created_by_id=helper.member_id + 100)
        entry = _only_entries((await async_client.get(URL, headers=_auth(member))).json())[0]
        assert entry["jobs"][0]["can_start"] is False

    async def test_update_all_starts_anything(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        helper = TestTodoAccess()
        _, member = await helper._setup(async_client, ["queue:read_all", "queue:update_all"])
        await make_item(a.id, manual_start=True, created_by_id=helper.member_id + 100)
        entry = _only_entries((await async_client.get(URL, headers=_auth(member))).json())[0]
        assert entry["jobs"][0]["can_start"] is True

    async def test_unstaged_jobs_never_carry_it(self, async_client, printer_factory, make_item):
        a = await printer_factory()
        item = await make_item(a.id)
        scheduler._filament_holds = {item.id: [FilamentHold(item.id, a.id, None, (RED,))]}
        entry = _only_entries((await async_client.get(URL)).json())[0]
        assert entry["jobs"][0]["can_start"] is False


class TestReadOwn:
    async def test_read_own_sees_neither_others_jobs_nor_their_printer_entries(
        self, async_client, printer_factory, make_item
    ):
        a = await printer_factory(name="A")
        b = await printer_factory(name="B")
        helper = TestTodoAccess()
        _, member = await helper._setup(async_client, ["queue:read_own"])
        mine = await make_item(a.id, name="Mine", manual_start=True, created_by_id=helper.member_id)
        await make_item(b.id, name="Theirs", manual_start=True, created_by_id=helper.member_id + 100)
        with (
            patch("backend.app.api.routes.print_queue.printer_manager.is_awaiting_plate_clear", lambda pid: True),
            patch("backend.app.api.routes.print_queue.plate_clear.applies", lambda pid, setting: True),
        ):
            body = (await async_client.get(URL, headers=_auth(member))).json()
        assert {g["printer_id"] for g in body["groups"]} == {a.id}
        assert {j["id"] for e in _only_entries(body) for j in e["jobs"]} == {mine.id}


class TestApiKeyCaller:
    async def test_key_acts_as_its_owner(self, async_client, db_session, world):
        from backend.app.models.group import Group
        from backend.tests.integration.test_api_key_own_rows import _key

        group = (await db_session.execute(select(Group).where(Group.name == "own-queuers"))).scalar_one()
        group.permissions = [*group.permissions, "queue:start_unreviewed"]
        mine = await owner_tests._archive(db_session, world["owner"].id, 21)
        theirs = await owner_tests._archive(db_session, world["other"].id, 22)
        printer = world["printer"]
        db_session.add_all(
            [
                PrintQueueItem(
                    printer_id=printer.id,
                    archive_id=mine.id,
                    status="pending",
                    manual_start=True,
                    created_by_id=world["owner"].id,
                    position=1,
                ),
                PrintQueueItem(
                    printer_id=printer.id,
                    archive_id=theirs.id,
                    status="pending",
                    manual_start=True,
                    created_by_id=world["other"].id,
                    position=2,
                ),
            ]
        )
        await db_session.commit()
        key = await _key(db_session, world["owner"].id)

        response = await async_client.get(URL, headers=key)

        assert response.status_code == 200, response.text
        jobs = [j for e in _only_entries(response.json()) for j in e["jobs"]]
        assert [(j["name"], j["can_start"]) for j in jobs] == [("a21", True)]
