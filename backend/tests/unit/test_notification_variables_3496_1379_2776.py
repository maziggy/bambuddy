"""Print notification variables: location (#3496), costs (#1379), maintenance (#2776).

The wording of the maintenance variables is pinned on plain objects; the
service tests run against the database and capture what the providers are
handed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.print_log import PrintLogEntry
from backend.app.schemas.notification_template import EVENT_VARIABLES
from backend.app.services.maintenance_notification_text import NO_MAINTENANCE_VARIABLES, maintenance_variables
from backend.app.services.notification_service import NotificationService


def _item(name, *, interval=100.0, left=50.0, unit="hours", enabled=True):
    """One task of the maintenance overview, `left` before it falls due (negative: overdue)."""
    return SimpleNamespace(
        maintenance_type_name=name,
        enabled=enabled,
        interval_type=unit,
        interval_hours=interval,
        hours_until_due=left if unit == "hours" else 0,
        days_until_due=left if unit == "days" else None,
        is_due=left <= 0,
    )


class TestMaintenanceWording:
    def test_nothing_to_report_is_blank(self):
        assert maintenance_variables([]) == NO_MAINTENANCE_VARIABLES
        assert maintenance_variables([_item("Disabled", left=-5, enabled=False)]) == NO_MAINTENANCE_VARIABLES

    def test_the_next_task_in_hours(self):
        result = maintenance_variables([_item("Clean nozzle", left=42.5)])

        assert result["maintenance_due_in"] == "42.5 h"
        assert result["maintenance_overdue"] == ""
        assert result["maintenance_summary"] == "Next: Clean nozzle in 42.5 h"

    def test_whole_amounts_have_no_decimals(self):
        assert maintenance_variables([_item("Rails", left=12.0)])["maintenance_due_in"] == "12 h"
        assert maintenance_variables([_item("Rails", left=10.04)])["maintenance_due_in"] == "10 h"

    def test_a_task_counted_in_days_says_days(self):
        result = maintenance_variables([_item("Lubricate", interval=30, left=5, unit="days")])

        assert result["maintenance_due_in"] == "5 days"
        assert maintenance_variables([_item("Lubricate", interval=30, left=1, unit="days")])["maintenance_due_in"] == (
            "1 day"
        )

    def test_overdue_tasks_are_named_with_how_far_over(self):
        result = maintenance_variables(
            [_item("Nozzle", left=-2.5), _item("Rails", interval=30, left=-3, unit="days"), _item("Belts", left=80)]
        )

        assert result["maintenance_overdue"] == "Nozzle (2.5 h over), Rails (3 days over)"
        assert result["maintenance_due_in"] == "80 h"
        assert (
            result["maintenance_summary"] == "Overdue: Nozzle (2.5 h over), Rails (3 days over) | Next: Belts in 80 h"
        )

    def test_all_overdue_leaves_nothing_coming(self):
        result = maintenance_variables([_item("Nozzle", left=-1)])

        assert result["maintenance_due_in"] == ""
        assert result["maintenance_summary"] == "Overdue: Nozzle (1 h over)"

    def test_the_soonest_is_by_share_of_the_interval_so_hours_and_days_compare(self):
        # 40 of 1000 h left is 4%; 10 of 30 days left is 33%: the hours task is nearer.
        result = maintenance_variables(
            [_item("Long interval", interval=30, left=10, unit="days"), _item("Short", interval=1000, left=40)]
        )

        assert result["maintenance_summary"] == "Next: Short in 40 h"

    def test_a_task_without_an_interval_does_not_divide_by_zero(self):
        assert maintenance_variables([_item("Odd", interval=0, left=5)])["maintenance_due_in"] == "5 h"

    def test_disabled_tasks_are_left_out(self):
        result = maintenance_variables([_item("Off", left=-9, enabled=False), _item("On", left=20)])

        assert result["maintenance_overdue"] == ""
        assert result["maintenance_summary"] == "Next: On in 20 h"


async def _send(service: NotificationService, db, call, *args, **kwargs):
    """Run an on_* handler with one provider and return the variables it was given."""
    provider = SimpleNamespace(name="p")
    with (
        patch.object(service, "_get_providers_for_event", AsyncMock(return_value=[provider])),
        patch.object(service, "_send_to_providers", AsyncMock()) as send,
    ):
        await getattr(service, call)(*args, db=db, **kwargs)
    return send.await_args.kwargs["variables"]


class TestVariablesAreDeclared:
    @pytest.mark.parametrize("event", ["print_complete", "print_failed", "print_stopped"])
    def test_outcome_events_declare_costs_maintenance_and_location(self, event):
        names = set(EVENT_VARIABLES[event])

        assert {"location", "filament_cost", "energy_cost", "wear_cost", "total_cost", "currency"} <= names
        assert {"maintenance_due_in", "maintenance_overdue", "maintenance_summary"} <= names

    def test_print_start_has_location_but_no_maintenance(self):
        names = set(EVENT_VARIABLES["print_start"])

        assert "location" in names
        assert not {n for n in names if n.startswith("maintenance_")}

    @pytest.mark.parametrize(
        "event", ["user_print_start", "user_print_complete", "user_print_failed", "user_print_stopped"]
    )
    def test_user_emails_declare_location(self, event):
        assert "location" in EVENT_VARIABLES[event]


class TestLocation:
    @pytest.mark.asyncio
    async def test_print_start_carries_the_printers_location(self, db_session, printer_factory):
        printer = await printer_factory(location="Workshop")

        variables = await _send(
            NotificationService(), db_session, "on_print_start", printer.id, printer.name, {"filename": "a.3mf"}
        )

        assert variables["location"] == "Workshop"

    @pytest.mark.asyncio
    async def test_a_printer_without_one_gives_an_empty_string(self, db_session, printer_factory):
        printer = await printer_factory(location=None)

        variables = await _send(
            NotificationService(), db_session, "on_print_start", printer.id, printer.name, {"filename": "a.3mf"}
        )

        assert variables["location"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["completed", "failed", "stopped"])
    async def test_every_outcome_carries_it(self, db_session, printer_factory, status):
        printer = await printer_factory(location="Shelf 2")

        variables = await _send(
            NotificationService(), db_session, "on_print_complete", printer.id, printer.name, status, {"filename": "a"}
        )

        assert variables["location"] == "Shelf 2"

    @pytest.mark.asyncio
    async def test_the_user_email_carries_it(self, db_session, printer_factory):
        from backend.app.models.settings import Settings
        from backend.app.models.user import User

        printer = await printer_factory(location="Workshop")
        db_session.add_all(
            [
                Settings(key="advanced_auth_enabled", value="true"),
                User(username="alice", email="a@example.com", password_hash="x", role="user", is_active=True),
            ]
        )
        await db_session.commit()
        user_id = (await db_session.execute(select(User.id))).scalar_one()
        sent = AsyncMock()

        with (
            patch("backend.app.services.email_service.get_smtp_settings", AsyncMock(return_value=object())),
            patch("backend.app.services.email_service.send_user_print_notification", sent),
        ):
            await NotificationService().send_user_print_email(
                "user_print_complete", user_id, printer.name, "a.3mf", db_session, printer_id=printer.id
            )

        sent.assert_awaited_once()
        assert sent.await_args.kwargs["variables"]["location"] == "Workshop"


class TestCosts:
    async def _printed(self, db_session, printer_factory, archive_factory, **run_costs):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        entry = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()
        for key, value in run_costs.items():
            setattr(entry, key, value)
        await db_session.commit()
        return printer, archive, entry

    async def _complete(self, db_session, printer, archive, entry, status="completed"):
        return await _send(
            NotificationService(),
            db_session,
            "on_print_complete",
            printer.id,
            printer.name,
            status,
            {"filename": "a"},
            archive_data={"archive_id": archive.id, "run_id": entry.id},
        )

    @pytest.mark.asyncio
    async def test_the_runs_costs_and_their_sum(self, db_session, printer_factory, archive_factory):
        printer, archive, entry = await self._printed(
            db_session, printer_factory, archive_factory, cost=1.234, energy_cost=0.5, wear_cost=0.25
        )

        variables = await self._complete(db_session, printer, archive, entry)

        assert variables["filament_cost"] == "1.23"
        assert variables["energy_cost"] == "0.50"
        assert variables["wear_cost"] == "0.25"
        assert variables["total_cost"] == "1.98"

    @pytest.mark.asyncio
    async def test_an_untracked_cost_is_na_and_the_total_sums_the_rest(
        self, db_session, printer_factory, archive_factory
    ):
        # No smart plug (no energy) and no hourly wear cost on the printer.
        printer, archive, entry = await self._printed(
            db_session, printer_factory, archive_factory, cost=2.0, energy_cost=None, wear_cost=None
        )

        variables = await self._complete(db_session, printer, archive, entry)

        assert variables["energy_cost"] == "N/A"
        assert variables["wear_cost"] == "N/A"
        assert variables["total_cost"] == "2.00"

    @pytest.mark.asyncio
    async def test_nothing_tracked_is_na_throughout(self, db_session, printer_factory, archive_factory):
        printer, archive, entry = await self._printed(
            db_session, printer_factory, archive_factory, cost=None, energy_cost=None, wear_cost=None
        )

        variables = await self._complete(db_session, printer, archive, entry)

        assert {variables[k] for k in ("filament_cost", "energy_cost", "wear_cost", "total_cost")} == {"N/A"}

    @pytest.mark.asyncio
    async def test_a_zero_cost_is_a_cost_not_a_gap(self, db_session, printer_factory, archive_factory):
        printer, archive, entry = await self._printed(
            db_session, printer_factory, archive_factory, cost=0.0, energy_cost=0.0, wear_cost=None
        )

        variables = await self._complete(db_session, printer, archive, entry)

        assert variables["filament_cost"] == "0.00"
        assert variables["total_cost"] == "0.00"

    @pytest.mark.asyncio
    async def test_a_failed_run_reports_its_own_partial_cost(self, db_session, printer_factory, archive_factory):
        printer, archive, entry = await self._printed(
            db_session, printer_factory, archive_factory, cost=0.4, energy_cost=0.1, wear_cost=None
        )

        variables = await self._complete(db_session, printer, archive, entry, status="failed")

        assert variables["total_cost"] == "0.50"

    @pytest.mark.asyncio
    async def test_a_print_without_a_log_entry_is_na(self, db_session, printer_factory):
        printer = await printer_factory()

        variables = await _send(
            NotificationService(),
            db_session,
            "on_print_complete",
            printer.id,
            printer.name,
            "completed",
            {"filename": "a"},
        )

        assert variables["total_cost"] == "N/A"

    @pytest.mark.asyncio
    async def test_the_currency_is_the_one_set_in_settings(self, db_session, printer_factory, archive_factory):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="currency", value="EUR"))
        await db_session.commit()
        printer, archive, entry = await self._printed(db_session, printer_factory, archive_factory, cost=1.0)

        variables = await self._complete(db_session, printer, archive, entry)

        assert variables["currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_the_currency_defaults_to_the_apps_default(self, db_session, printer_factory, archive_factory):
        printer, archive, entry = await self._printed(db_session, printer_factory, archive_factory, cost=1.0)

        variables = await self._complete(db_session, printer, archive, entry)

        assert variables["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_the_currency_is_there_when_the_print_has_no_log_entry(self, db_session, printer_factory):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="currency", value="GBP"))
        await db_session.commit()
        printer = await printer_factory()

        variables = await _send(
            NotificationService(),
            db_session,
            "on_print_complete",
            printer.id,
            printer.name,
            "completed",
            {"filename": "a"},
        )

        assert (variables["total_cost"], variables["currency"]) == ("N/A", "GBP")

    @pytest.mark.asyncio
    async def test_print_start_has_no_costs(self, db_session, printer_factory):
        printer = await printer_factory()

        variables = await _send(
            NotificationService(), db_session, "on_print_start", printer.id, printer.name, {"filename": "a.3mf"}
        )

        assert not {"filament_cost", "total_cost", "energy_cost"} & set(variables)


class TestMaintenance:
    @pytest.mark.asyncio
    async def test_the_variables_come_from_the_maintenance_pages_figures(self, db_session, printer_factory):
        from backend.app.api.routes.maintenance import _get_printer_maintenance_internal

        printer = await printer_factory()
        overview = await _get_printer_maintenance_internal(printer.id, db_session, commit=True)

        variables = await _send(
            NotificationService(),
            db_session,
            "on_print_complete",
            printer.id,
            printer.name,
            "completed",
            {"filename": "a"},
        )

        expected = maintenance_variables(overview.maintenance_items)
        assert {k: variables[k] for k in NO_MAINTENANCE_VARIABLES} == expected
        # A fresh printer has system tasks set up, so there is something to say.
        assert expected["maintenance_summary"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["completed", "failed", "stopped"])
    async def test_every_outcome_carries_them(self, db_session, printer_factory, status):
        printer = await printer_factory()
        fake = SimpleNamespace(maintenance_items=[_item("Clean nozzle", left=42.5)])

        with patch(
            "backend.app.api.routes.maintenance._get_printer_maintenance_internal", AsyncMock(return_value=fake)
        ):
            variables = await _send(
                NotificationService(),
                db_session,
                "on_print_complete",
                printer.id,
                printer.name,
                status,
                {"filename": "a"},
            )

        assert variables["maintenance_due_in"] == "42.5 h"
        assert variables["maintenance_summary"] == "Next: Clean nozzle in 42.5 h"

    @pytest.mark.asyncio
    async def test_print_start_does_not(self, db_session, printer_factory):
        printer = await printer_factory()

        variables = await _send(
            NotificationService(), db_session, "on_print_start", printer.id, printer.name, {"filename": "a.3mf"}
        )

        assert not {k for k in variables if k.startswith("maintenance_")}

    @pytest.mark.asyncio
    async def test_a_failing_lookup_never_costs_the_notification(self, db_session, printer_factory):
        printer = await printer_factory()

        with patch(
            "backend.app.api.routes.maintenance._get_printer_maintenance_internal",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            variables = await _send(
                NotificationService(),
                db_session,
                "on_print_complete",
                printer.id,
                printer.name,
                "completed",
                {"filename": "a"},
            )

        assert variables["maintenance_summary"] == ""
        assert variables["printer"] == printer.name
