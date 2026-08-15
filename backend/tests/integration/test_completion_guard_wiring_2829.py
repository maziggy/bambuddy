"""The completion guard itself, not just the name comparison (#2829).

There is a unit test for ``_subtask_names_match``. It is not enough on its own:
reverting ``_completion_belongs_to_queue_item`` to the strict equality that
caused the bug leaves every one of those tests green, because they never touch
the guard. So these drive the guard, with a real archive row behind a real
queue row, on the exact strings that stranded the maintainer's H2D.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _archive(db_session, printer, filename):
    from backend.app.models.archive import PrintArchive

    archive = PrintArchive(
        printer_id=printer.id,
        filename=filename,
        file_path=f"archives/{filename}",
        file_size=1024,
        status="printing",
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)
    return archive


async def _item(db_session, printer, archive):
    from backend.app.models.print_queue import PrintQueueItem

    item = PrintQueueItem(printer_id=printer.id, status="printing", archive_id=archive.id)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


async def _belongs(db_session, item, subtask_name):
    from backend.app.main import _completion_belongs_to_queue_item

    return await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": subtask_name})


class TestTheReportedStranding:
    async def test_the_completion_for_its_own_print_is_accepted(self, db_session, printer_factory):
        """Queue item 649 on the maintainer's H2D, verbatim. The printer echoes
        the name back with underscores where the file has spaces; the guard
        read that as a different print and left the row printing forever."""
        printer = await printer_factory()
        archive = await _archive(db_session, printer, "H2D_Carbon_Filter_(V2)_Body & Solid Lid.gcode.3mf")
        item = await _item(db_session, printer, archive)

        assert await _belongs(db_session, item, "H2D_Carbon_Filter_(V2)_Body_&_Solid_Lid")

    async def test_a_truncated_echo_is_accepted(self, db_session, printer_factory):
        printer = await printer_factory()
        name = "169356_204314.STEP + 169356_204314.STEP + 169356_204314.STEP + 169356_204314.STEP + 169356_204314"
        archive = await _archive(db_session, printer, f"{name}.gcode.3mf")
        item = await _item(db_session, printer, archive)

        assert await _belongs(db_session, item, f"{name[:70]}...")


class TestItStillRefuses:
    """The guard's reason for existing: a completion for something else must
    not close a job that is still running."""

    async def test_the_printers_own_calibration_run(self, db_session, printer_factory):
        printer = await printer_factory()
        archive = await _archive(db_session, printer, "H2D_Carbon_Filter_(V2)_Body & Solid Lid.gcode.3mf")
        item = await _item(db_session, printer, archive)

        assert not await _belongs(db_session, item, "auto_pa_line_calib_mode")

    async def test_an_unrelated_print(self, db_session, printer_factory):
        printer = await printer_factory()
        archive = await _archive(db_session, printer, "Benchy.gcode.3mf")
        item = await _item(db_session, printer, archive)

        assert not await _belongs(db_session, item, "Calibration Cube")


class TestUnverifiableIsNotWrong:
    """Refusing what cannot be checked would strand the queue, which is the
    worse of the two failures and the one this issue is about."""

    async def test_no_subtask_name_in_the_event(self, db_session, printer_factory):
        printer = await printer_factory()
        archive = await _archive(db_session, printer, "Benchy.gcode.3mf")
        item = await _item(db_session, printer, archive)

        assert await _belongs(db_session, item, "")

    async def test_a_row_with_no_archive(self, db_session, printer_factory):
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        item = PrintQueueItem(printer_id=printer.id, status="printing")
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)

        assert await _belongs(db_session, item, "Anything At All")
