"""Integration tests for Shortest Job First (SJF) queue scheduling."""

import pytest
from sqlalchemy import select

from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings


class TestSJFScheduling:
    """Tests for shortest-job-first queue ordering and starvation guard."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Printer {counter}",
                "ip_address": f"192.168.1.{100 + counter}",
                "serial_number": f"TESTSERIAL{counter:04d}",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""
        _counter = [0]

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"test_print_{counter}.3mf",
                "print_name": f"Test Print {counter}",
                "file_path": f"/tmp/test_print_{counter}.3mf",  # nosec B108
                "file_size": 1024,
                "content_hash": f"testhash{counter:08d}",
                "status": "completed",
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.fixture
    async def queue_item_factory(self, db_session, printer_factory, archive_factory):
        """Factory to create test queue items with print_time_seconds."""

        async def _create_queue_item(**kwargs):
            if "printer_id" not in kwargs:
                printer = await printer_factory()
                kwargs["printer_id"] = printer.id

            if "archive_id" not in kwargs:
                archive = await archive_factory()
                kwargs["archive_id"] = archive.id

            defaults = {
                "status": "pending",
                "position": 0,
            }
            defaults.update(kwargs)

            item = PrintQueueItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_queue_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_item_has_print_time_seconds(self, queue_item_factory):
        """Verify print_time_seconds can be stored on queue items."""
        item = await queue_item_factory(print_time_seconds=3600, position=1)
        assert item.print_time_seconds == 3600

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_item_has_been_jumped(self, queue_item_factory):
        """Verify been_jumped defaults to False and can be set."""
        item = await queue_item_factory(position=1)
        assert item.been_jumped is False

        item2 = await queue_item_factory(been_jumped=True, position=2)
        assert item2.been_jumped is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sjf_ordering_shorter_jobs_first(self, db_session, queue_item_factory, printer_factory):
        """Verify SJF query orders by print_time_seconds ascending."""
        printer = await printer_factory()

        # Add items in FIFO order: long, medium, short
        long_job = await queue_item_factory(
            printer_id=printer.id,
            position=1,
            print_time_seconds=28800,  # 8 hours
        )
        medium_job = await queue_item_factory(
            printer_id=printer.id,
            position=2,
            print_time_seconds=3600,  # 1 hour
        )
        short_job = await queue_item_factory(
            printer_id=printer.id,
            position=3,
            print_time_seconds=1200,  # 20 min
        )

        # SJF query: been_jumped DESC, print_time_seconds ASC NULLS LAST, position
        result = await db_session.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.printer_id == printer.id)
            .order_by(
                PrintQueueItem.been_jumped.desc(),
                PrintQueueItem.print_time_seconds.asc().nullslast(),
                PrintQueueItem.position,
            )
        )
        items = list(result.scalars().all())

        assert len(items) == 3
        assert items[0].id == short_job.id  # 20 min first
        assert items[1].id == medium_job.id  # 1 hour second
        assert items[2].id == long_job.id  # 8 hours last

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sjf_null_print_time_goes_last(self, db_session, queue_item_factory, printer_factory):
        """Verify items without print_time_seconds are sorted last in SJF mode."""
        printer = await printer_factory()

        no_time = await queue_item_factory(printer_id=printer.id, position=1, print_time_seconds=None)
        short_job = await queue_item_factory(printer_id=printer.id, position=2, print_time_seconds=600)

        result = await db_session.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.printer_id == printer.id)
            .order_by(
                PrintQueueItem.been_jumped.desc(),
                PrintQueueItem.print_time_seconds.asc().nullslast(),
                PrintQueueItem.position,
            )
        )
        items = list(result.scalars().all())

        assert items[0].id == short_job.id  # Known duration first
        assert items[1].id == no_time.id  # Unknown duration last

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_starvation_guard_jumped_items_first(self, db_session, queue_item_factory, printer_factory):
        """Verify been_jumped items are sorted before non-jumped items."""
        printer = await printer_factory()

        # Long job that was jumped (should go first now)
        jumped_long = await queue_item_factory(
            printer_id=printer.id, position=1, print_time_seconds=28800, been_jumped=True
        )
        # Short job (would normally go first, but jumped_long has priority)
        short_job = await queue_item_factory(printer_id=printer.id, position=2, print_time_seconds=600)

        result = await db_session.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.printer_id == printer.id)
            .order_by(
                PrintQueueItem.been_jumped.desc(),
                PrintQueueItem.print_time_seconds.asc().nullslast(),
                PrintQueueItem.position,
            )
        )
        items = list(result.scalars().all())

        assert items[0].id == jumped_long.id  # Jumped item gets priority
        assert items[1].id == short_job.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fifo_ordering_ignores_print_time(self, db_session, queue_item_factory, printer_factory):
        """Verify default FIFO ordering uses position only, not print_time_seconds."""
        printer = await printer_factory()

        long_first = await queue_item_factory(printer_id=printer.id, position=1, print_time_seconds=28800)
        short_second = await queue_item_factory(printer_id=printer.id, position=2, print_time_seconds=600)

        # Default FIFO query (no SJF)
        result = await db_session.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.printer_id == printer.id)
            .order_by(PrintQueueItem.position)
        )
        items = list(result.scalars().all())

        assert items[0].id == long_first.id  # Position 1 first (FIFO)
        assert items[1].id == short_second.id  # Position 2 second

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sjf_position_as_tiebreaker(self, db_session, queue_item_factory, printer_factory):
        """Verify position is used as tiebreaker when print times are equal."""
        printer = await printer_factory()

        first = await queue_item_factory(printer_id=printer.id, position=1, print_time_seconds=3600)
        second = await queue_item_factory(printer_id=printer.id, position=2, print_time_seconds=3600)

        result = await db_session.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.printer_id == printer.id)
            .order_by(
                PrintQueueItem.been_jumped.desc(),
                PrintQueueItem.print_time_seconds.asc().nullslast(),
                PrintQueueItem.position,
            )
        )
        items = list(result.scalars().all())

        assert items[0].id == first.id  # Same duration, lower position wins
        assert items[1].id == second.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_starvation_flag_set_on_jumped_items(self, db_session, queue_item_factory, printer_factory):
        """Verify the starvation flag logic marks jumped items correctly."""
        printer = await printer_factory()

        # Simulate: long job at position 1, short job at position 2
        long_job = await queue_item_factory(printer_id=printer.id, position=1, print_time_seconds=28800)
        short_job = await queue_item_factory(printer_id=printer.id, position=2, print_time_seconds=1200)

        # Simulate what the scheduler does when SJF picks short_job first:
        # Mark items that were jumped (lower position, longer duration)
        items = [long_job, short_job]
        winning_item = short_job  # SJF would pick this

        for other in items:
            if (
                other.id != winning_item.id
                and other.status == "pending"
                and other.printer_id == winning_item.printer_id
                and not other.been_jumped
                and other.position < winning_item.position
                and (other.print_time_seconds is None or other.print_time_seconds > winning_item.print_time_seconds)
            ):
                other.been_jumped = True

        await db_session.commit()
        await db_session.refresh(long_job)

        assert long_job.been_jumped is True
        assert short_job.been_jumped is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_starvation_guard_prevents_double_jump(self, db_session, queue_item_factory, printer_factory):
        """Verify an already-jumped item won't be jumped again."""
        printer = await printer_factory()

        # Long job already jumped once
        long_job = await queue_item_factory(
            printer_id=printer.id, position=1, print_time_seconds=28800, been_jumped=True
        )
        # Even shorter job arrives
        tiny_job = await queue_item_factory(printer_id=printer.id, position=3, print_time_seconds=300)

        # SJF order: jumped items first, then by duration
        result = await db_session.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.printer_id == printer.id)
            .order_by(
                PrintQueueItem.been_jumped.desc(),
                PrintQueueItem.print_time_seconds.asc().nullslast(),
                PrintQueueItem.position,
            )
        )
        items = list(result.scalars().all())

        # long_job goes first because it was already jumped (starvation protection)
        assert items[0].id == long_job.id
        assert items[1].id == tiny_job.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_shortest_first_setting(self, db_session):
        """Verify the queue_shortest_first setting can be stored and read."""
        setting = Settings(key="queue_shortest_first", value="true")
        db_session.add(setting)
        await db_session.commit()

        result = await db_session.execute(select(Settings).where(Settings.key == "queue_shortest_first"))
        stored = result.scalar_one_or_none()
        assert stored is not None
        assert stored.value == "true"
