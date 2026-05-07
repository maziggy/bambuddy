"""Tests for phantom print investigation hardening (#374).

Tests the tightened archive matching (no ilike) and the
multiple-printing-items warning logic.

These are pure unit tests that test the changed logic directly,
NOT by calling the full on_print_start/on_print_complete callbacks
(which spawn background tasks and require heavy mocking).
"""

import logging

import pytest
from sqlalchemy import or_, select
from sqlalchemy.sql import ClauseElement

from backend.app.models.archive import PrintArchive


class TestArchiveMatchQueryShape:
    """Tests that the archive duplicate lookup query uses exact match, not ilike (#374).

    The old query used `ilike('%{name}%')` which caused "Clip" to match
    "Cable Clip", "Clip Stand", etc. The new query uses exact print_name
    match OR exact filename variants (.3mf, .gcode.3mf).
    """

    def _build_archive_query(self, check_name: str, printer_id: int = 1) -> ClauseElement:
        """Build the exact query used in on_print_start for archive dedup."""
        return (
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer_id)
            .where(PrintArchive.status == "printing")
            .where(
                or_(
                    PrintArchive.print_name == check_name,
                    PrintArchive.filename.in_(
                        [
                            f"{check_name}.3mf",
                            f"{check_name}.gcode.3mf",
                        ]
                    ),
                )
            )
            .order_by(PrintArchive.created_at.desc())
            .limit(1)
        )

    def test_query_does_not_contain_ilike(self):
        """Verify the compiled query does NOT use LIKE/ILIKE."""
        query = self._build_archive_query("Clip")
        query_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        assert "LIKE" not in query_str.upper(), f"Query should not use LIKE: {query_str}"

    def test_query_uses_exact_equality(self):
        """Verify the query uses = for print_name comparison."""
        query = self._build_archive_query("Benchy")
        query_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        assert "print_name = " in query_str or "print_name ='" in query_str or "print_name =" in query_str

    def test_query_uses_in_for_filename_variants(self):
        """Verify the query uses IN for filename matching with .3mf variants."""
        query = self._build_archive_query("MyPrint")
        query_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        assert "IN" in query_str.upper()
        assert "MyPrint.3mf" in query_str
        assert "MyPrint.gcode.3mf" in query_str

    def test_partial_name_not_in_query(self):
        """Verify 'Clip' does not produce a wildcard pattern."""
        query = self._build_archive_query("Clip")
        query_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should NOT contain %Clip% wildcard
        assert "%Clip%" not in query_str

    def test_check_name_derivation_from_subtask(self):
        """Verify check_name is derived correctly from subtask_name."""
        # Simulates: check_name = subtask_name or filename.split("/")[-1].replace(...)
        subtask_name = "Cable Clip"
        filename = "/sdcard/Cable Clip.gcode"
        check_name = subtask_name or filename.split("/")[-1].replace(".gcode", "").replace(".3mf", "")
        assert check_name == "Cable Clip"

        query = self._build_archive_query(check_name)
        query_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Exact match should contain the full name, not a partial
        assert "Cable Clip" in query_str
        assert "%Cable Clip%" not in query_str

    def test_check_name_derivation_from_filename(self):
        """Verify check_name strips extensions correctly from filename."""
        subtask_name = None
        filename = "/sdcard/MyPrint.gcode"
        check_name = subtask_name or filename.split("/")[-1].replace(".gcode", "").replace(".3mf", "")
        assert check_name == "MyPrint"


class TestMultiplePrintingQueueItemsWarning:
    """Tests for the multiple-printing-items warning logic (#374).

    The code in on_print_complete now detects when multiple queue items
    are in 'printing' status for the same printer, which signals a bug.
    """

    def test_single_item_returns_item_no_warning(self, caplog):
        """Verify single item is returned without warning."""
        from unittest.mock import MagicMock

        items = [MagicMock(id=1, archive_id=10, library_file_id=None)]

        # Simulate the exact code from on_print_complete
        with caplog.at_level(logging.WARNING, logger="backend.app.main"):
            logger = logging.getLogger("backend.app.main")
            printer_id = 1
            printing_items = list(items)

            if len(printing_items) > 1:
                logger.warning(
                    "BUG: Multiple queue items in 'printing' status for printer %s: %s",
                    printer_id,
                    [(i.id, i.archive_id, i.library_file_id) for i in printing_items],
                )
            queue_item = printing_items[0] if printing_items else None

        assert queue_item is not None
        assert queue_item.id == 1
        bug_warnings = [r for r in caplog.records if "BUG: Multiple queue items" in r.message]
        assert len(bug_warnings) == 0

    def test_multiple_items_warns_and_returns_first(self, caplog):
        """Verify warning is logged and first item is returned when multiple exist."""
        from unittest.mock import MagicMock

        items = [
            MagicMock(id=1, archive_id=10, library_file_id=None),
            MagicMock(id=2, archive_id=20, library_file_id=None),
        ]

        with caplog.at_level(logging.WARNING, logger="backend.app.main"):
            logger = logging.getLogger("backend.app.main")
            printer_id = 1
            printing_items = list(items)

            if len(printing_items) > 1:
                logger.warning(
                    "BUG: Multiple queue items in 'printing' status for printer %s: %s",
                    printer_id,
                    [(i.id, i.archive_id, i.library_file_id) for i in printing_items],
                )
            queue_item = printing_items[0] if printing_items else None

        assert queue_item is not None
        assert queue_item.id == 1  # First item is used
        bug_warnings = [r for r in caplog.records if "BUG: Multiple queue items" in r.message]
        assert len(bug_warnings) == 1
        assert "printer 1" in bug_warnings[0].message
        # Warning should include item details
        assert "10" in bug_warnings[0].message  # archive_id of item 1
        assert "20" in bug_warnings[0].message  # archive_id of item 2

    def test_empty_list_returns_none_no_warning(self, caplog):
        """Verify None is returned and no warning when no items exist."""
        with caplog.at_level(logging.WARNING, logger="backend.app.main"):
            logger = logging.getLogger("backend.app.main")
            printer_id = 1
            printing_items = []

            if len(printing_items) > 1:
                logger.warning(
                    "BUG: Multiple queue items in 'printing' status for printer %s: %s",
                    printer_id,
                    [(i.id, i.archive_id, i.library_file_id) for i in printing_items],
                )
            queue_item = printing_items[0] if printing_items else None

        assert queue_item is None
        bug_warnings = [r for r in caplog.records if "BUG: Multiple queue items" in r.message]
        assert len(bug_warnings) == 0

    def test_three_items_warns_with_all_details(self, caplog):
        """Verify warning includes all item details when three items found."""
        from unittest.mock import MagicMock

        items = [
            MagicMock(id=1, archive_id=10, library_file_id=None),
            MagicMock(id=2, archive_id=None, library_file_id=5),
            MagicMock(id=3, archive_id=30, library_file_id=None),
        ]

        with caplog.at_level(logging.WARNING, logger="backend.app.main"):
            logger = logging.getLogger("backend.app.main")
            printer_id = 7
            printing_items = list(items)

            if len(printing_items) > 1:
                logger.warning(
                    "BUG: Multiple queue items in 'printing' status for printer %s: %s",
                    printer_id,
                    [(i.id, i.archive_id, i.library_file_id) for i in printing_items],
                )
            queue_item = printing_items[0] if printing_items else None

        assert queue_item.id == 1
        bug_warnings = [r for r in caplog.records if "BUG: Multiple queue items" in r.message]
        assert len(bug_warnings) == 1
        assert "printer 7" in bug_warnings[0].message


class TestBusyPrinterSeedingFromPrintingItems:
    """Regression for the duplicate-dispatch bug observed with quantity>1 batches.

    The old scheduler seeded ``busy_printers`` with an empty set and relied on
    ``_is_printer_idle()`` to gate dispatch. On H2D / P1 series the MQTT state
    lags several seconds behind the print command, so the next ``check_queue``
    tick saw IDLE and dispatched a second queue item onto the same printer —
    both items ended up in 'printing' status. The fix seeds ``busy_printers``
    up-front with every printer that already has an item in 'printing' status.
    """

    @pytest.mark.asyncio
    async def test_seed_query_returns_printers_with_printing_items(self):
        """The seeding query must return every printer_id that has a 'printing' item."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base
        from backend.app.models.print_queue import PrintQueueItem

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            db.add_all(
                [
                    PrintQueueItem(printer_id=1, status="printing", position=1, archive_id=10),
                    PrintQueueItem(printer_id=1, status="pending", position=2, archive_id=10),
                    PrintQueueItem(printer_id=2, status="printing", position=1, archive_id=11),
                    PrintQueueItem(printer_id=3, status="pending", position=1, archive_id=12),
                    PrintQueueItem(printer_id=None, status="pending", position=1, archive_id=13),
                ]
            )
            await db.commit()

            result = await db.execute(
                select(PrintQueueItem.printer_id)
                .where(PrintQueueItem.status == "printing")
                .where(PrintQueueItem.printer_id.is_not(None))
            )
            busy_printers = {pid for (pid,) in result.all() if pid is not None}

        assert busy_printers == {1, 2}

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_seed_query_empty_when_no_printing_items(self):
        """With only pending items, no printer is considered busy by the query."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base
        from backend.app.models.print_queue import PrintQueueItem

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            db.add_all(
                [
                    PrintQueueItem(printer_id=1, status="pending", position=1, archive_id=10),
                    PrintQueueItem(printer_id=2, status="completed", position=1, archive_id=11),
                    PrintQueueItem(printer_id=3, status="failed", position=1, archive_id=12),
                    PrintQueueItem(printer_id=4, status="cancelled", position=1, archive_id=13),
                ]
            )
            await db.commit()

            result = await db.execute(
                select(PrintQueueItem.printer_id)
                .where(PrintQueueItem.status == "printing")
                .where(PrintQueueItem.printer_id.is_not(None))
            )
            busy_printers = {pid for (pid,) in result.all() if pid is not None}

        assert busy_printers == set()

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_check_queue_skips_printer_with_existing_printing_item(self, caplog):
        """Simulate the exact observed bug: a pending item targets a printer that already
        has another queue item in 'printing' status. The scheduler must NOT dispatch the
        pending item even if the live MQTT state reports IDLE.
        """
        from unittest.mock import AsyncMock, patch

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import backend.app.models  # noqa: F401
        from backend.app.core.database import Base
        from backend.app.models.print_queue import PrintQueueItem
        from backend.app.services.print_scheduler import PrintScheduler

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            db.add_all(
                [
                    PrintQueueItem(printer_id=1, status="printing", position=1, archive_id=84),
                    PrintQueueItem(printer_id=1, status="pending", position=2, archive_id=84),
                ]
            )
            await db.commit()

        scheduler = PrintScheduler()
        start_print_mock = AsyncMock()

        with (
            patch("backend.app.services.print_scheduler.async_session", session_maker),
            patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)),
            patch.object(scheduler, "_is_printer_idle", return_value=True),
            patch.object(scheduler, "_check_auto_drying", AsyncMock()),
            patch.object(scheduler, "_start_print", start_print_mock),
            patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        ):
            mock_pm.is_connected.return_value = True
            await scheduler.check_queue()

        start_print_mock.assert_not_called()

        async with session_maker() as db:
            rows = (await db.execute(select(PrintQueueItem).order_by(PrintQueueItem.position))).scalars().all()
            statuses = [r.status for r in rows]
        assert statuses == ["printing", "pending"]

        await engine.dispose()
