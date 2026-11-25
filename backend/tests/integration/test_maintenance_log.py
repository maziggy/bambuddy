"""The maintenance log: every reset with its comment, who did it, on which printer and when.

Entries outlive the printer, the custom task and the user (their names are
kept as they were), so the log stays an audit trail. It is read per printer
and exported as CSV per printer or for the whole fleet; a printer-scoped
caller sees only their printers, and not the entries of deleted ones.
Entries can be deleted, by age or one at a time, with the history row of the
same reset, so a deleted comment is gone from the database.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from backend.app.models.maintenance import MaintenanceHistory, MaintenanceLogEntry
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


async def _age(db_session, entry_id: int, days: int) -> int:
    """Move an entry ``days`` into the past; returns its history row's id."""
    entry = await _entry(db_session, entry_id)
    entry.performed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    history_id = entry.history_id
    await db_session.commit()
    assert history_id is not None
    return history_id


async def _history_exists(db_session, history_id: int) -> bool:
    db_session.expire_all()
    return await db_session.get(MaintenanceHistory, history_id) is not None


async def _logged(async_client, printer_factory, name: str, *notes: str) -> tuple[int, list[int]]:
    """A printer with one reset per note; returns its id and the entry ids, oldest first."""
    printer_id = (await printer_factory(name=name)).id
    item = await _first_item(async_client, printer_id)
    for note in notes:
        await _perform(async_client, item["id"], note)
    entries = (await _log(async_client, printer_id))["items"]
    return printer_id, [e["id"] for e in reversed(entries)]


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

        response = await _perform(async_client, item["id"], "x" * 10001)

        assert response.status_code == 422
        assert (await _log(async_client, printer.id))["total"] == 0

    async def test_a_long_report_fits(self, async_client, printer_factory):
        """Automations post whole reports; the endpoint took any length before."""
        printer = await printer_factory(name="Report Printer")
        item = await _first_item(async_client, printer.id)

        response = await _perform(async_client, item["id"], "x" * 10000)

        assert response.status_code == 200, response.text
        assert len((await _log(async_client, printer.id))["items"][0]["notes"]) == 10000

    async def test_surrounding_whitespace_does_not_count_against_the_limit(self, async_client, printer_factory):
        printer = await printer_factory(name="Padded Printer")
        item = await _first_item(async_client, printer.id)

        response = await _perform(async_client, item["id"], "x" * 9995 + " " * 20 + "\n")

        assert response.status_code == 200, response.text
        assert (await _log(async_client, printer.id))["items"][0]["notes"] == "x" * 9995

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


class TestCleanup:
    async def test_a_printer_s_old_entries_go_with_their_history(self, async_client, printer_factory, db_session):
        printer_id, (old, recent, _today) = await _logged(
            async_client, printer_factory, "Cleanup", "old", "recent", "today"
        )
        other_id, (other_old,) = await _logged(async_client, printer_factory, "Cleanup Other", "other old")
        old_history = await _age(db_session, old, 400)
        recent_history = await _age(db_session, recent, 10)
        other_history = await _age(db_session, other_old, 400)

        response = await async_client.delete(f"{BASE}/printers/{printer_id}/log", params={"older_than_days": 30})

        assert response.status_code == 200, response.text
        assert response.json() == {"deleted": 1}
        assert [e["notes"] for e in (await _log(async_client, printer_id))["items"]] == ["today", "recent"]
        assert not await _history_exists(db_session, old_history)
        assert await _history_exists(db_session, recent_history)
        # Another printer's entries are not touched.
        assert (await _log(async_client, other_id))["total"] == 1
        assert await _history_exists(db_session, other_history)

    async def test_the_age_must_be_given_and_at_least_a_day(self, async_client, printer_factory):
        printer_id, _ = await _logged(async_client, printer_factory, "Cleanup Age", "kept")

        for params in ({}, {"older_than_days": 0}, {"older_than_days": -5}):
            assert (await async_client.delete(f"{BASE}/printers/{printer_id}/log", params=params)).status_code == 422
            assert (await async_client.delete(f"{BASE}/log", params=params)).status_code == 422
        assert (await _log(async_client, printer_id))["total"] == 1

    async def test_cleaning_up_an_unknown_printer_is_not_found(self, async_client):
        response = await async_client.delete(f"{BASE}/printers/999999/log", params={"older_than_days": 1})

        assert response.status_code == 404

    async def test_the_fleet_cleanup_covers_every_printer_and_deleted_ones(
        self, async_client, printer_factory, db_session
    ):
        a_id, (a_old, a_new) = await _logged(async_client, printer_factory, "Fleet Clean A", "a old", "a new")
        b_id, (b_old,) = await _logged(async_client, printer_factory, "Fleet Clean B", "b old")
        gone_id, (gone_old,) = await _logged(async_client, printer_factory, "Fleet Clean Gone", "gone old")
        for entry_id in (a_old, b_old, gone_old):
            await _age(db_session, entry_id, 100)
        assert (await async_client.delete(f"/api/v1/printers/{gone_id}")).status_code == 200

        response = await async_client.delete(f"{BASE}/log", params={"older_than_days": 30})

        assert response.status_code == 200, response.text
        assert response.json() == {"deleted": 3}
        assert [e["id"] for e in (await _log(async_client, a_id))["items"]] == [a_new]
        assert (await _log(async_client, b_id))["total"] == 0
        db_session.expire_all()
        assert await db_session.get(MaintenanceLogEntry, gone_old) is None

    async def test_one_entry_goes_with_its_history_and_the_reset_stays(self, async_client, printer_factory, db_session):
        printer_id, (kept, mistaken) = await _logged(async_client, printer_factory, "Mistake", "kept", "by mistake")
        history_id = (await _entry(db_session, mistaken)).history_id
        before = await _first_item(async_client, printer_id)

        response = await async_client.delete(f"{BASE}/log/{mistaken}")

        assert response.status_code == 200, response.text
        assert [e["id"] for e in (await _log(async_client, printer_id))["items"]] == [kept]
        assert history_id is not None
        assert not await _history_exists(db_session, history_id)
        # Deleting the entry does not undo the reset.
        assert (await _first_item(async_client, printer_id))["last_performed_at"] == before["last_performed_at"]

    async def test_an_entry_whose_history_is_gone_leaves_a_later_reset_s_history_alone(
        self, async_client, printer_factory, db_session
    ):
        # The deleted printer's history row had the highest id, so on SQLite the
        # next reset is given it. Deleting the old entry must not take that
        # reset's history with it.
        doomed = (await printer_factory(name="Doomed")).id
        await _perform(async_client, (await _first_item(async_client, doomed))["id"], "before the end")
        old_entry = (await _log(async_client, doomed))["items"][0]["id"]
        old_history = (await _entry(db_session, old_entry)).history_id
        assert (await async_client.delete(f"/api/v1/printers/{doomed}")).status_code == 200
        assert (await _entry(db_session, old_entry)).history_id is None

        survivor_id, (new_entry,) = await _logged(async_client, printer_factory, "Survivor", "after")
        new_history = (await _entry(db_session, new_entry)).history_id
        assert new_history is not None

        assert (await async_client.delete(f"{BASE}/log/{old_entry}")).status_code == 200

        assert await _history_exists(db_session, new_history)
        assert (await _log(async_client, survivor_id))["total"] == 1
        if db_session.bind.dialect.name == "sqlite":
            # The case this is about: SQLite gave the later reset the freed id.
            # PostgreSQL never reuses one, so there it can only pass.
            assert new_history == old_history

    async def test_deleting_an_unknown_entry_is_not_found(self, async_client):
        assert (await async_client.delete(f"{BASE}/log/999999")).status_code == 404


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

    async def test_cleanup_needs_the_delete_permission(self, async_client, printer_factory):
        printer_id, (entry_id,) = await _logged(async_client, printer_factory, "No Delete", "kept")
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "no_delete", self.PERMISSIONS)

        for path, params in (
            (f"{BASE}/printers/{printer_id}/log", {"older_than_days": 1}),
            (f"{BASE}/log", {"older_than_days": 1}),
            (f"{BASE}/log/{entry_id}", {}),
        ):
            response = await async_client.delete(path, params=params, headers=_auth(jwt))
            assert response.status_code == 403, (path, response.text)
        assert (await _log(async_client, printer_id, headers=_auth(admin)))["total"] == 1

    async def test_a_scoped_user_cleans_up_only_their_printers(self, async_client, printer_factory, db_session):
        mine, (mine_old,) = await _logged(async_client, printer_factory, "Clean Mine", "mine")
        theirs, (theirs_old,) = await _logged(async_client, printer_factory, "Clean Theirs", "theirs")
        gone, (gone_old,) = await _logged(async_client, printer_factory, "Clean Gone", "gone")
        for entry_id in (mine_old, theirs_old, gone_old):
            await _age(db_session, entry_id, 100)
        assert (await async_client.delete(f"/api/v1/printers/{gone}")).status_code == 200
        admin = await _admin_token(async_client)
        jwt, _ = await _user(
            async_client, admin, "scoped_cleaner", [*self.PERMISSIONS, "maintenance:delete"], printer_ids=[mine]
        )

        fleet = await async_client.delete(f"{BASE}/log", params={"older_than_days": 30}, headers=_auth(jwt))
        other_printer = await async_client.delete(
            f"{BASE}/printers/{theirs}/log", params={"older_than_days": 30}, headers=_auth(jwt)
        )
        other_entry = await async_client.delete(f"{BASE}/log/{theirs_old}", headers=_auth(jwt))
        deleted_printer_entry = await async_client.delete(f"{BASE}/log/{gone_old}", headers=_auth(jwt))

        assert fleet.status_code == 200, fleet.text
        assert fleet.json() == {"deleted": 1}
        assert other_printer.status_code in (403, 404)
        assert other_entry.status_code == 404
        assert deleted_printer_entry.status_code == 404
        db_session.expire_all()
        assert await db_session.get(MaintenanceLogEntry, mine_old) is None
        assert await db_session.get(MaintenanceLogEntry, theirs_old) is not None
        assert await db_session.get(MaintenanceLogEntry, gone_old) is not None
