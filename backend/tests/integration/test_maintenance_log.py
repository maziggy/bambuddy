"""The maintenance log: every reset with its comment, who did it, on which printer and when.

Entries outlive the printer, the custom task and the user (their names are
kept as they were), so the log stays an audit trail. It is read per printer
and exported as CSV per printer or for the whole fleet; a printer-scoped
caller sees only their printers, and not the entries of deleted ones.
"""

from __future__ import annotations

import csv
import io

import pytest
from httpx import AsyncClient

from backend.app.models.maintenance import MaintenanceLogEntry
from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

BASE = "/api/v1/maintenance"


async def _first_item(async_client: AsyncClient, printer_id: int) -> dict:
    overview = (await async_client.get(f"{BASE}/printers/{printer_id}")).json()
    return overview["maintenance_items"][0]


async def _perform(async_client: AsyncClient, item_id: int, notes: str | None = None, headers=None):
    body = {} if notes is None else {"notes": notes}
    return await async_client.post(f"{BASE}/items/{item_id}/perform", json=body, headers=headers)


async def _log(async_client: AsyncClient, printer_id: int, headers=None, **params) -> dict:
    response = await async_client.get(f"{BASE}/printers/{printer_id}/log", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _csv_rows(response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert text.startswith("﻿"), "Excel needs the BOM to read UTF-8"
    return list(csv.reader(io.StringIO(text[1:])))


async def _entry(db_session, entry_id: int) -> MaintenanceLogEntry:
    db_session.expire_all()
    entry = await db_session.get(MaintenanceLogEntry, entry_id)
    assert entry is not None
    return entry


class TestLogging:
    async def test_a_reset_logs_the_task_the_printer_and_the_comment(self, async_client, printer_factory):
        printer = await printer_factory(name="Log Printer")
        item = await _first_item(async_client, printer.id)

        response = await _perform(async_client, item["id"], "  Rails cleaned, new grease  ")

        assert response.status_code == 200, response.text
        log = await _log(async_client, printer.id)
        assert log["total"] == 1
        entry = log["items"][0]
        assert entry["printer_id"] == printer.id
        assert entry["printer_name"] == "Log Printer"
        assert entry["maintenance_type_id"] == item["maintenance_type_id"]
        assert entry["maintenance_type_name"] == item["maintenance_type_name"]
        assert entry["notes"] == "Rails cleaned, new grease"
        assert entry["performed_at"] is not None
        # Authentication is off: nobody to name.
        assert entry["performed_by_id"] is None
        assert entry["performed_by_username"] is None

    async def test_the_comment_is_optional_and_blank_is_none(self, async_client, printer_factory):
        printer = await printer_factory(name="No Comment Printer")
        item = await _first_item(async_client, printer.id)

        assert (await _perform(async_client, item["id"])).status_code == 200
        assert (await _perform(async_client, item["id"], "   ")).status_code == 200

        notes = [e["notes"] for e in (await _log(async_client, printer.id))["items"]]
        assert notes == [None, None]

    async def test_a_comment_over_the_limit_is_refused(self, async_client, printer_factory):
        printer = await printer_factory(name="Long Comment Printer")
        item = await _first_item(async_client, printer.id)

        response = await _perform(async_client, item["id"], "x" * 1001)

        assert response.status_code == 422
        assert (await _log(async_client, printer.id))["total"] == 0

    async def test_the_log_is_newest_first_and_paged(self, async_client, printer_factory):
        printer = await printer_factory(name="Paged Printer")
        item = await _first_item(async_client, printer.id)
        for n in range(3):
            await _perform(async_client, item["id"], f"reset {n}")

        first = await _log(async_client, printer.id, limit=2)
        rest = await _log(async_client, printer.id, limit=2, offset=2)

        assert first["total"] == rest["total"] == 3
        assert [e["notes"] for e in first["items"]] == ["reset 2", "reset 1"]
        assert [e["notes"] for e in rest["items"]] == ["reset 0"]

    async def test_another_printer_s_resets_are_not_in_its_log(self, async_client, printer_factory):
        mine, other = await printer_factory(name="Mine"), await printer_factory(name="Other")
        await _perform(async_client, (await _first_item(async_client, other.id))["id"], "other's")

        assert (await _log(async_client, mine.id))["total"] == 0

    async def test_the_log_of_an_unknown_printer_is_not_found(self, async_client):
        response = await async_client.get(f"{BASE}/printers/999999/log")

        assert response.status_code == 404


class TestOutlivingDeletes:
    async def test_entries_outlive_their_printer(self, async_client, printer_factory, db_session):
        printer = await printer_factory(name="Doomed Printer")
        await _perform(async_client, (await _first_item(async_client, printer.id))["id"], "before the end")
        entry_id = (await _log(async_client, printer.id))["items"][0]["id"]

        deleted = await async_client.delete(f"/api/v1/printers/{printer.id}")

        assert deleted.status_code == 200, deleted.text
        entry = await _entry(db_session, entry_id)
        assert entry.printer_id is None
        assert entry.printer_name == "Doomed Printer"
        assert entry.notes == "before the end"
        rows = _csv_rows(await async_client.get(f"{BASE}/log/export"))
        assert ["Doomed Printer", "before the end"] in [[r[1], r[5]] for r in rows[1:]]

    async def test_entries_outlive_a_deleted_custom_task(self, async_client, printer_factory, db_session):
        printer_id = (await printer_factory(name="Custom Task Printer")).id
        task = (
            await async_client.post(
                f"{BASE}/types",
                json={"name": "Replace Hotend", "default_interval_hours": 500, "interval_type": "hours"},
            )
        ).json()
        item = (await async_client.post(f"{BASE}/printers/{printer_id}/assign/{task['id']}")).json()
        await _perform(async_client, item["id"], "0.4 mm hardened")
        entry_id = (await _log(async_client, printer_id))["items"][0]["id"]

        assert (await async_client.delete(f"{BASE}/types/{task['id']}")).status_code == 200

        entry = await _entry(db_session, entry_id)
        assert entry.maintenance_type_id is None
        assert entry.maintenance_type_name == "Replace Hotend"
        assert (await _log(async_client, printer_id))["total"] == 1


class TestExport:
    async def test_a_printer_s_log_exports_as_csv(self, async_client, printer_factory):
        printer = await printer_factory(name="Drucker Übung")
        item = await _first_item(async_client, printer.id)
        await _perform(async_client, item["id"], "Düse gewechselt")

        response = await async_client.get(f"{BASE}/printers/{printer.id}/log/export")

        rows = _csv_rows(response)
        assert rows[0] == ["Performed at (UTC)", "Printer", "Task", "Performed by", "Print hours", "Comment"]
        assert len(rows) == 2
        performed_at, name, task, by, hours, comment = rows[1]
        assert performed_at.endswith("Z")
        assert (name, task, by, comment) == ("Drucker Übung", item["maintenance_type_name"], "", "Düse gewechselt")
        assert float(hours) == pytest.approx(0.0)
        assert "maintenance_log_Drucker" in response.headers["content-disposition"]

    async def test_a_formula_in_a_comment_stays_text(self, async_client, printer_factory):
        printer = await printer_factory(name="Formula Printer")
        await _perform(async_client, (await _first_item(async_client, printer.id))["id"], '=HYPERLINK("x")')

        rows = _csv_rows(await async_client.get(f"{BASE}/printers/{printer.id}/log/export"))

        assert rows[1][5] == '\'=HYPERLINK("x")'

    async def test_the_fleet_export_has_every_printer(self, async_client, printer_factory):
        a, b = await printer_factory(name="Fleet A"), await printer_factory(name="Fleet B")
        for printer in (a, b):
            await _perform(async_client, (await _first_item(async_client, printer.id))["id"], printer.name)

        rows = _csv_rows(await async_client.get(f"{BASE}/log/export"))

        assert {r[1] for r in rows[1:]} >= {"Fleet A", "Fleet B"}


class TestWithAuthentication:
    PERMISSIONS = ["maintenance:read", "maintenance:update", "printers:read"]

    async def test_the_log_names_who_reset(self, async_client, printer_factory):
        printer = await printer_factory(name="Auth Printer")
        admin = await _admin_token(async_client)
        jwt, user_id = await _user(async_client, admin, "mechanic", self.PERMISSIONS)
        overview = (await async_client.get(f"{BASE}/printers/{printer.id}", headers=_auth(jwt))).json()
        item = overview["maintenance_items"][0]

        response = await _perform(async_client, item["id"], "done", headers=_auth(jwt))

        assert response.status_code == 200, response.text
        entry = (await _log(async_client, printer.id, headers=_auth(jwt)))["items"][0]
        assert (entry["performed_by_id"], entry["performed_by_username"]) == (user_id, "mechanic")
        rows = _csv_rows(await async_client.get(f"{BASE}/printers/{printer.id}/log/export", headers=_auth(jwt)))
        assert rows[1][3] == "mechanic"

    async def test_a_scoped_user_exports_only_their_printers(self, async_client, printer_factory):
        mine, theirs, gone = (
            await printer_factory(name="Scoped Mine"),
            await printer_factory(name="Scoped Theirs"),
            await printer_factory(name="Scoped Gone"),
        )
        for printer in (mine, theirs, gone):
            await _perform(async_client, (await _first_item(async_client, printer.id))["id"], printer.name)
        assert (await async_client.delete(f"/api/v1/printers/{gone.id}")).status_code == 200
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "scoped", self.PERMISSIONS, printer_ids=[mine.id])

        rows = _csv_rows(await async_client.get(f"{BASE}/log/export", headers=_auth(jwt)))
        admin_rows = _csv_rows(await async_client.get(f"{BASE}/log/export", headers=_auth(admin)))

        assert [r[1] for r in rows[1:]] == ["Scoped Mine"]
        assert {"Scoped Mine", "Scoped Theirs", "Scoped Gone"} <= {r[1] for r in admin_rows[1:]}

    async def test_a_scoped_user_cannot_read_another_printer_s_log(self, async_client, printer_factory):
        mine, theirs = await printer_factory(name="Scope A"), await printer_factory(name="Scope B")
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "scoped_reader", self.PERMISSIONS, printer_ids=[mine.id])

        log = await async_client.get(f"{BASE}/printers/{theirs.id}/log", headers=_auth(jwt))
        export = await async_client.get(f"{BASE}/printers/{theirs.id}/log/export", headers=_auth(jwt))

        assert log.status_code in (403, 404)
        assert export.status_code in (403, 404)
