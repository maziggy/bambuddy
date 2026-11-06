"""The archive list's filters look at every print (archive redesign P4).

An archive is a file printed maybe several times, maybe on several printers:
the printer filter finds it on each printer one of its prints ran on, and its
failed prints count the aborted ones too.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import RUN_OPEN, PrintLogEntry
from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

T1 = datetime(2026, 10, 1, 9)
T2 = datetime(2026, 10, 20, 9)


@pytest.fixture
async def printed(db_session, printer_factory):
    a, b, c = await printer_factory(name="A"), await printer_factory(name="B"), await printer_factory(name="C")
    planter = PrintArchive(
        printer_id=b.id,
        filename="planter.3mf",
        file_path="archives/t/planter.3mf",
        file_size=1,
        print_name="Planter",
        status="completed",
    )
    hook = PrintArchive(
        printer_id=c.id, filename="hook.3mf", file_path="archives/t/hook.3mf", file_size=1, print_name="Hook"
    )
    db_session.add_all([planter, hook])
    await db_session.flush()
    db_session.add_all(
        [
            PrintLogEntry(archive_id=planter.id, printer_id=a.id, status="aborted", started_at=T1, created_at=T1),
            PrintLogEntry(archive_id=planter.id, printer_id=b.id, status="completed", started_at=T2, created_at=T2),
        ]
    )
    await db_session.commit()
    return {"a": a.id, "b": b.id, "c": c.id, "planter": planter.id, "hook": hook.id}


async def _names(async_client, headers=None, **params) -> list[str]:
    response = await async_client.get("/api/v1/archives/", params=params, headers=headers or {})
    assert response.status_code == 200, response.text
    return sorted(r["print_name"] for r in response.json())


async def test_an_archive_is_found_on_each_printer_it_printed_on(async_client, printed):
    assert await _names(async_client, printer_id=printed["a"]) == ["Planter"]
    assert await _names(async_client, printer_id=printed["b"]) == ["Planter"]
    assert await _names(async_client, printer_id=printed["c"]) == ["Hook"]


async def test_a_print_running_now_counts(async_client, db_session, printed):
    db_session.add(PrintLogEntry(archive_id=printed["hook"], printer_id=printed["a"], status=RUN_OPEN, started_at=T2))
    await db_session.commit()

    assert await _names(async_client, printer_id=printed["a"]) == ["Hook", "Planter"]


async def test_a_printer_outside_the_callers_finds_nothing(async_client, printed):
    """Planter is theirs to see by its print on A; that it printed on B is not."""
    admin = await _admin_token(async_client)
    jwt, _ = await _user(
        async_client, admin, "of_a", ["archives:read_all", "printers:read"], printer_ids=[printed["a"]]
    )

    assert await _names(async_client, _auth(jwt), printer_id=printed["a"]) == ["Planter"]
    assert await _names(async_client, _auth(jwt), printer_id=printed["b"]) == []


async def test_an_aborted_print_is_a_failed_one(async_client, printed):
    response = await async_client.get("/api/v1/archives/")

    [planter] = [r for r in response.json() if r["id"] == printed["planter"]]
    assert (planter["status"], planter["failed_run_count"]) == ("completed", 1)


async def test_with_a_printer_picked_the_counts_are_its_prints(async_client, printed):
    """Failed Prints under printer B: Planter's aborted print was on A."""
    response = await async_client.get("/api/v1/archives/", params={"printer_id": printed["b"]})

    [planter] = response.json()
    assert (planter["run_count"], planter["failed_run_count"]) == (1, 0)


async def test_search_finds_an_archive_on_each_printer_it_printed_on(async_client, printed):
    response = await async_client.get("/api/v1/archives/search", params={"q": "Planter", "printer_id": printed["a"]})

    assert response.status_code == 200, response.text
    assert [r["print_name"] for r in response.json()] == ["Planter"]


async def test_search_under_a_printer_outside_the_callers_finds_nothing(async_client, printed):
    admin = await _admin_token(async_client)
    jwt, _ = await _user(
        async_client, admin, "of_a", ["archives:read_all", "printers:read"], printer_ids=[printed["a"]]
    )

    response = await async_client.get(
        "/api/v1/archives/search", params={"q": "Planter", "printer_id": printed["b"]}, headers=_auth(jwt)
    )

    assert response.json() == []


# The calendar: the prints of the archives the caller sees, by when they ended.


async def _prints(async_client, headers=None, **params) -> list[int]:
    response = await async_client.get(
        "/api/v1/archives/prints",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31", "tz": "UTC", **params},
        headers=headers or {},
    )
    assert response.status_code == 200, response.text
    return [r["id"] for r in response.json()["items"]]


async def test_the_calendar_gets_the_prints_that_ended_in_the_month(async_client, db_session, printed):
    early = PrintLogEntry(
        archive_id=printed["hook"],
        printer_id=printed["c"],
        status="completed",
        started_at=datetime(2026, 9, 29, 9),
        completed_at=datetime(2026, 10, 1, 1),
    )
    later = PrintLogEntry(
        archive_id=printed["hook"], printer_id=printed["c"], status="completed", completed_at=datetime(2026, 11, 1, 1)
    )
    db_session.add_all([early, later])
    await db_session.commit()
    runs = await async_client.get(f"/api/v1/archives/{printed['planter']}/runs")
    planter = sorted(r["id"] for r in runs.json()["items"])

    # Each print in id order; the Planter prints carry no end time and count
    # by when they were logged.
    ids = await _prints(async_client)
    assert early.id in ids and later.id not in ids
    assert ids == sorted(ids)
    assert set(planter) <= set(ids)
    assert await _prints(async_client, printer_id=printed["c"]) == [early.id]


async def test_the_calendar_shows_an_owners_archive_whoever_printed_it(async_client, db_session, printed):
    """read_own goes by who owns the archive, as the archive list: a print
    of it started by no one (from the printer) is still theirs to see."""
    admin = await _admin_token(async_client)
    jwt, user_id = await _user(async_client, admin, "owner", ["archives:read_own"])
    planter = await db_session.get(PrintArchive, printed["planter"])
    planter.created_by_id = user_id
    # Hook isn't theirs, though they started its print.
    db_session.add(
        PrintLogEntry(
            archive_id=printed["hook"],
            printer_id=printed["c"],
            status="completed",
            created_by_id=user_id,
            created_at=T2,
        )
    )
    await db_session.commit()

    ids = await _prints(async_client, _auth(jwt))

    runs = await async_client.get(f"/api/v1/archives/{printed['planter']}/runs", headers=_auth(admin))
    assert len(ids) == 2
    assert sorted(ids) == sorted(r["id"] for r in runs.json()["items"])


async def test_the_calendar_leaves_out_deleted_archives_and_other_printers(async_client, db_session, printed):
    admin = await _admin_token(async_client)
    jwt, _ = await _user(
        async_client, admin, "of_a", ["archives:read_all", "printers:read"], printer_ids=[printed["a"]]
    )
    on_a = await _prints(async_client, _auth(jwt))
    assert len(on_a) == 1
    assert await _prints(async_client, _auth(jwt), printer_id=printed["b"]) == []

    planter = await db_session.get(PrintArchive, printed["planter"])
    planter.deleted_at = T2
    await db_session.commit()
    assert await _prints(async_client, _auth(admin)) == []
