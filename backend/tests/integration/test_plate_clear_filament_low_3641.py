"""Plate Clear Required names the finished job, Filament Low gives its colour (#3641)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.services.notification_content import NotificationFilament
from backend.app.services.notification_service import NotificationService
from backend.app.services.printer_manager import PrinterManager


def _default(event_type):
    return next(t for t in DEFAULT_TEMPLATES if t["event_type"] == event_type)


@pytest.fixture
def service():
    return NotificationService()


@pytest.fixture
def seed_template(db_session):
    async def _seed(event_type, **edits):
        data = {**_default(event_type), **edits}
        db_session.add(
            NotificationTemplate(
                event_type=event_type,
                name=data["name"],
                title_template=data["title_template"],
                body_template=data["body_template"],
            )
        )
        await db_session.commit()

    return _seed


@pytest.fixture
def discord(notification_provider_factory):
    return lambda **kw: notification_provider_factory(
        provider_type="discord", config={"webhook_url": "https://discord.com/api/webhooks/1/a"}, **kw
    )


async def _embed(service, call):
    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=204))
    with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=client):
        await call
    return client.post.await_args.kwargs["json"]["embeds"][0]


class TestPlateClear:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discord_shows_printer_and_finished_job(self, service, db_session, seed_template, discord):
        await seed_template("plate_clear_required")
        await discord(on_plate_clear_required=True)

        embed = await _embed(
            service,
            service.on_plate_clear_required(1, "Sol P1s links", db_session, job_name="Blanket_Coaster_4x.gcode.3mf"),
        )

        # The words are the default's, unchanged.
        assert embed["description"] == (
            "Sol P1s links: print finished. Confirm the build plate is clear before the queue continues."
        )
        assert embed["fields"] == [
            {"name": "Printer", "value": "Sol P1s links", "inline": False},
            {"name": "Finished", "value": "Blanket_Coaster_4x", "inline": False},
        ]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_a_known_job_there_is_only_the_printer(self, service, db_session, seed_template, discord):
        await seed_template("plate_clear_required")
        await discord(on_plate_clear_required=True)

        embed = await _embed(service, service.on_plate_clear_required(1, "Sol P1s links", db_session))

        assert [f["name"] for f in embed["fields"]] == ["Printer"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_job_name_is_a_variable_for_other_channels(
        self, service, db_session, seed_template, notification_provider_factory
    ):
        await seed_template("plate_clear_required", body_template="{printer}: {job_name} finished")
        await notification_provider_factory(
            provider_type="ntfy", config={"server": "https://ntfy.sh", "topic": "t"}, on_plate_clear_required=True
        )
        send = AsyncMock(return_value=(True, "ok"))

        with patch.object(service, "_send_ntfy", send):
            await service.on_plate_clear_required(1, "Sol P1s links", db_session, job_name="Coaster_4x.gcode.3mf")

        assert send.await_args.args[2] == "Sol P1s links: Coaster_4x finished"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_edited_template_is_sent_as_before(self, service, db_session, seed_template, discord):
        await seed_template("plate_clear_required", body_template="{printer}: clear the bed")
        await discord(on_plate_clear_required=True)

        embed = await _embed(service, service.on_plate_clear_required(1, "Sol", db_session, job_name="Coaster"))

        assert embed == {"title": "Plate Clear Required", "description": "Sol: clear the bed", "color": 0x00AE42}


class TestFinishedJobName:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_archive_names_it_even_when_the_printer_reports_another_subtask(
        self, db_session, printer_factory
    ):
        printer = await printer_factory()
        name = "Blanket_Coaster_4x_p1s.gcode.3mf"
        db_session.add(PrintArchive(printer_id=printer.id, filename=name, file_path=f"a/{name}", file_size=1))
        await db_session.commit()
        state = SimpleNamespace(subtask_name="Blanket_Coaster_4x.P1s_gcode")

        found = await PrinterManager()._finished_job_name(db_session, printer.id, state)

        assert found == name
        assert NotificationService()._clean_filename(found) == "Blanket_Coaster_4x_p1s"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_newest_archive_of_the_printer(self, db_session, printer_factory):
        printer = await printer_factory()
        other = await printer_factory()
        for printer_id, name in ((printer.id, "old.gcode.3mf"), (printer.id, "newest.gcode.3mf"), (other.id, "x.3mf")):
            db_session.add(PrintArchive(printer_id=printer_id, filename=name, file_path=f"a/{name}", file_size=1))
            await db_session.commit()

        manager = PrinterManager()
        assert await manager._finished_job_name(db_session, printer.id, None) == "newest.gcode.3mf"
        assert await manager._finished_job_name(db_session, printer.id, SimpleNamespace(subtask_name="")) == (
            "newest.gcode.3mf"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_an_archive_the_queue_item_that_ran_there(self, db_session, printer_factory):
        from datetime import datetime

        from backend.app.models.library import LibraryFile
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        lib = LibraryFile(filename="Lamp_5x.gcode.3mf", file_path="l/Lamp_5x.gcode.3mf", file_type="3mf", file_size=1)
        db_session.add(lib)
        await db_session.commit()
        db_session.add(
            PrintQueueItem(
                printer_id=printer.id, library_file_id=lib.id, status="completed", completed_at=datetime.now()
            )
        )
        await db_session.commit()

        found = await PrinterManager()._finished_job_name(
            db_session, printer.id, SimpleNamespace(subtask_name="Lamp_5x.P1s_gcode")
        )

        assert found == "Lamp_5x.gcode.3mf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_archive_or_queue_item_the_printers_own_subtask_name(self, db_session, printer_factory):
        printer = await printer_factory()
        state = SimpleNamespace(subtask_name=" Coaster_4x.gcode ")
        found = await PrinterManager()._finished_job_name(db_session, printer.id, state)
        assert found == "Coaster_4x.gcode"
        assert NotificationService()._clean_filename(found) == "Coaster_4x"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nothing_known_is_none(self, db_session, printer_factory):
        printer = await printer_factory()
        assert await PrinterManager()._finished_job_name(db_session, printer.id, None) is None


class TestFilamentLow:
    FILAMENT = NotificationFilament(type="PLA", color_hex="B39DDBFF", color_name="Lavender Purple", slot="A1")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discord_shows_the_spool_and_takes_its_colour(self, service, db_session, seed_template, discord):
        await seed_template("filament_low")
        await discord(on_filament_low=True)

        embed = await _embed(
            service,
            service.on_filament_low(
                1, "Sol P1s links", "A1", 12, db_session, color="Lavender Purple", filament=self.FILAMENT
            ),
        )

        assert embed["description"] == "Sol P1s links: Slot A1 at 12%"
        assert embed["fields"] == [
            {"name": "Printer", "value": "Sol P1s links", "inline": False},
            {"name": "Slot", "value": "A1", "inline": False},
            {"name": "Filament", "value": "\U0001f7e3 PLA Lavender Purple (#B39DDB)", "inline": False},
            {"name": "Remaining", "value": "12%", "inline": False},
        ]
        assert embed["color"] == 0xB39DDB

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_a_spool_the_fields_have_no_filament_and_the_green_stays(
        self, service, db_session, seed_template, discord
    ):
        await seed_template("filament_low")
        await discord(on_filament_low=True)

        embed = await _embed(service, service.on_filament_low(1, "Sol", "A1", 12, db_session))

        assert [f["name"] for f in embed["fields"]] == ["Printer", "Slot", "Remaining"]
        assert embed["color"] == 0x00AE42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_colour_variables_for_other_channels_and_the_old_ones_unchanged(
        self, service, db_session, seed_template, notification_provider_factory
    ):
        await seed_template(
            "filament_low", body_template="{printer} {slot} {remaining_percent}% {color}: {filament} {color_hex}"
        )
        await notification_provider_factory(
            provider_type="ntfy", config={"server": "https://ntfy.sh", "topic": "t"}, on_filament_low=True
        )
        send = AsyncMock(return_value=(True, "ok"))

        with patch.object(service, "_send_ntfy", send):
            await service.on_filament_low(
                1, "Sol", "A1", 12, db_session, color="Lavender Purple", filament=self.FILAMENT
            )

        assert send.await_args.args[2] == "Sol A1 12% Lavender Purple: \U0001f7e3 PLA Lavender Purple (#B39DDB) #B39DDB"


class TestEmitPassesTheJob:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_rising_edge_names_the_job_the_printer_reports(self):
        from backend.app.services import plate_clear

        plate_clear.load_overrides({})
        manager = PrinterManager()
        manager._printer_info[7] = SimpleNamespace(name="X1C", serial_number="01P00A000000001")
        notify = AsyncMock()
        with (
            patch("backend.app.services.mqtt_relay.mqtt_relay.on_plate_clear_state", AsyncMock()),
            patch("backend.app.services.wled.wled_manager.handle_status", MagicMock()),
            patch.object(manager, "get_status", return_value=SimpleNamespace(subtask_name="Coaster_4x")),
            patch("backend.app.services.notification_service.notification_service.on_plate_clear_required", notify),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        ):
            await manager._emit_plate_clear_change(7, True)

        assert notify.await_args.args[:2] == (7, "X1C")
        assert notify.await_args.kwargs["job_name"] == "Coaster_4x"
