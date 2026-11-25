"""The archive export has one row per print (archive redesign P3, decision 5).

An archive is a file and each print of it a run: a row holds the file's
columns and one run's own. An archive printed before runs were logged keeps
its one row; the print filters pick prints, the others archives.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

T1 = datetime(2026, 10, 1, 9)
T2 = datetime(2026, 10, 20, 9)


async def _rows(async_client, headers=None, **params) -> list[dict]:
    response = await async_client.get(
        "/api/v1/archives/export", params={"format": "csv", **params}, headers=headers or {}
    )
    assert response.status_code == 200, response.text
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


@pytest.fixture
async def prints(db_session, printer_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    planter = PrintArchive(
        printer_id=b.id,
        filename="planter.3mf",
        file_path="archives/t/planter.3mf",
        file_size=1,
        print_name="Planter",
        status="failed",
        tags="garden",
        notes="two plates",
        created_at=T1,
    )
    legacy = PrintArchive(
        printer_id=a.id,
        filename="hook.3mf",
        file_path="archives/t/hook.3mf",
        file_size=1,
        print_name="Hook",
        status="completed",
        filament_used_grams=5.0,
        created_at=T1,
    )
    db_session.add_all([planter, legacy])
    await db_session.flush()
    first = PrintLogEntry(
        archive_id=planter.id,
        printer_id=a.id,
        printer_name="A",
        plate_id=1,
        status="completed",
        started_at=T1,
        filament_used_grams=12.0,
        user_verdict="good",
    )
    second = PrintLogEntry(
        archive_id=planter.id,
        printer_id=b.id,
        printer_name="B",
        plate_id=2,
        status="failed",
        started_at=T2,
        filament_used_grams=3.0,
        failure_reason="spaghetti",
    )
    db_session.add_all([first, second])
    await db_session.commit()
    return {"a": a.id, "b": b.id, "planter": planter.id, "legacy": legacy.id, "first": first.id, "second": second.id}


async def test_one_row_per_print(async_client, prints):
    rows = await _rows(async_client)

    planter = [r for r in rows if r["Print Name"] == "Planter"]
    assert [(r["Print ID"], r["Printer"], r["Plate"], r["Status"], r["Filament (g)"]) for r in planter] == [
        (str(prints["second"]), "B", "2", "failed", "3.0"),
        (str(prints["first"]), "A", "1", "completed", "12.0"),
    ]
    # The file's columns on each.
    assert {(r["ID"], r["Tags"], r["Notes"]) for r in planter} == {(str(prints["planter"]), "garden", "two plates")}
    assert [r["Outcome"] for r in planter] == ["", "good"]


async def test_a_print_logged_before_runs_keeps_its_row(async_client, prints):
    [hook] = [r for r in await _rows(async_client) if r["Print Name"] == "Hook"]

    assert (hook["Print ID"], hook["Status"], hook["Filament (g)"]) == ("", "completed", "5.0")


async def test_the_print_filters_pick_prints(async_client, prints):
    on_a = await _rows(async_client, printer_id=prints["a"])
    failed = await _rows(async_client, status="failed")
    late = await _rows(async_client, date_from="2026-10-15")

    assert sorted(r["Print ID"] for r in on_a) == sorted(["", str(prints["first"])])
    assert [r["Print ID"] for r in failed] == [str(prints["second"])]
    assert [r["Print ID"] for r in late] == [str(prints["second"])]


async def test_a_user_limited_to_some_printers_gets_their_prints(async_client, prints):
    admin = await _admin_token(async_client)
    jwt, _ = await _user(async_client, admin, "of_a", ["archives:read_all", "printers:read"], printer_ids=[prints["a"]])

    rows = await _rows(async_client, headers=_auth(jwt))

    planter = [r for r in rows if r["Print Name"] == "Planter"]
    assert [r["Print ID"] for r in planter] == [str(prints["first"])]


async def test_a_file_printed_only_outside_their_printers_shows_no_print(async_client, db_session, prints):
    """Visible by its own printer (the archive names A), every print on B."""
    archive = await db_session.get(PrintArchive, prints["planter"])
    archive.printer_id = prints["a"]
    first = await db_session.get(PrintLogEntry, prints["first"])
    first.printer_id = prints["b"]
    await db_session.commit()
    admin = await _admin_token(async_client)
    jwt, _ = await _user(async_client, admin, "of_a", ["archives:read_all", "printers:read"], printer_ids=[prints["a"]])

    [planter] = [r for r in await _rows(async_client, headers=_auth(jwt)) if r["Print Name"] == "Planter"]

    assert (planter["Print ID"], planter["Status"], planter["Printer ID"], planter["Tags"]) == ("", "", "", "garden")


async def test_a_print_running_now_is_a_row_of_its_own(async_client, db_session, prints):
    """Not taken from the archive, whose fields may be another printer's."""
    from backend.app.models.print_log import RUN_OPEN

    hook = await db_session.get(PrintArchive, prints["legacy"])
    hook.printer_id = prints["b"]
    running = PrintLogEntry(archive_id=prints["legacy"], printer_id=prints["a"], status=RUN_OPEN, started_at=T2)
    db_session.add(running)
    await db_session.commit()
    running_id = running.id

    [row] = [r for r in await _rows(async_client) if r["Print Name"] == "Hook"]

    assert (row["Print ID"], row["Status"], row["Printer ID"]) == (str(running_id), "printing", str(prints["a"]))


async def test_only_the_exports_own_columns(async_client, prints):
    response = await async_client.get("/api/v1/archives/export", params={"fields": "id,confirm_token"})

    assert response.status_code == 400
    assert "confirm_token" in response.text


async def test_a_print_logged_before_it_kept_its_quantity_has_its_archives(async_client, db_session, prints):
    archive = await db_session.get(PrintArchive, prints["planter"])
    archive.quantity = 4
    await db_session.commit()

    planter = [r for r in await _rows(async_client) if r["Print Name"] == "Planter"]

    assert {r["Items Printed"] for r in planter} == {"4"}


async def test_a_print_logged_before_runs_names_its_printer(async_client, prints):
    [hook] = [r for r in await _rows(async_client) if r["Print Name"] == "Hook"]

    assert hook["Printer"] == "A"


async def test_several_statuses_at_once(async_client, prints):
    rows = await _rows(async_client, status="failed,completed")

    assert sorted(r["Print ID"] for r in rows) == sorted(["", str(prints["first"]), str(prints["second"])])
