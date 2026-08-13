"""A print completion must not close a queue row belonging to another print.

``on_print_complete`` finds its queue row by printer and ``status='printing'``
alone -- the MQTT payload carries no run identifier to match on -- so any
completion delivered for a printer closes whichever row happens to be printing.
That is fine while the only source of completions is the printer itself, and
wrong the moment one arrives from anywhere else: a live 14-hour print was closed
18 minutes in, and its plate 2 never dispatched, because a completion for an
unrelated subtask reached the same lookup.

These cover the guard that rules that out, and the deliberate decision to let
the unverifiable cases through rather than strand an item in ``printing``.
"""

import pytest

from backend.app.main import _completion_belongs_to_queue_item, _subtask_name_from_filename
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem


class TestSubtaskNameFromFilename:
    """The dispatcher builds the subtask name off the archive file name, so
    stripping the extensions back off has to land on exactly what MQTT echoes."""

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("AMS_Rack.gcode.3mf", "AMS_Rack"),
            ("AMS_Rack.3mf", "AMS_Rack"),
            ("plate.gcode", "plate"),
            # A dot in the model's own name is not an extension. Path.stem would
            # eat it and produce "My", which matches nothing.
            ("My.Model.3mf", "My.Model"),
            ("My.Model.gcode.3mf", "My.Model"),
            # Extensions are matched case-insensitively; the name is not.
            ("Cover.GCODE.3MF", "Cover"),
            # Only the file name matters -- archives store a path.
            ("archive/1/20260811_112435_AMS_Rack/AMS_Rack.gcode.3mf", "AMS_Rack"),
            # Nothing to strip.
            ("AMS_Rack", "AMS_Rack"),
        ],
    )
    def test_recovers_the_dispatched_subtask_name(self, filename, expected):
        assert _subtask_name_from_filename(filename) == expected


async def _seed(db, *, archive_filename: str | None) -> PrintQueueItem:
    """A printing queue item, optionally linked to an archive."""
    archive_id = None
    if archive_filename is not None:
        archive = PrintArchive(
            printer_id=1,
            filename=archive_filename,
            file_path=f"archive/1/{archive_filename}",
            file_size=1,
            status="printing",
        )
        db.add(archive)
        await db.flush()
        archive_id = archive.id

    item = PrintQueueItem(printer_id=1, status="printing", archive_id=archive_id)
    db.add(item)
    await db.flush()
    return item


@pytest.mark.asyncio
class TestCompletionBelongsToQueueItem:
    async def test_accepts_the_completion_for_its_own_print(self, db_session):
        item = await _seed(db_session, archive_filename="AMS_Rack.gcode.3mf")

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "AMS_Rack"}) is True

    async def test_rejects_a_completion_for_a_different_print(self, db_session):
        # The exact shape of the incident: the row was dispatched as AMS_Rack and
        # a completion for "Test" arrived on the same printer.
        item = await _seed(db_session, archive_filename="AMS_Rack.gcode.3mf")

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "Test"}) is False

    async def test_matches_regardless_of_case(self, db_session):
        item = await _seed(db_session, archive_filename="AMS_Rack.gcode.3mf")

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "ams_rack"}) is True

    @pytest.mark.parametrize("subtask", [None, "", "   "])
    async def test_lets_an_unidentified_completion_through(self, db_session, subtask):
        # No subtask name to compare means unverifiable, not wrong. Refusing here
        # would leave the item printing forever and wedge the printer's queue,
        # which is the failure the indiscriminate lookup existed to avoid.
        item = await _seed(db_session, archive_filename="AMS_Rack.gcode.3mf")

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": subtask}) is True

    async def test_lets_an_archiveless_item_through(self, db_session):
        # Library-file dispatch links the archive after the fact; there is
        # nothing to compare against yet.
        item = await _seed(db_session, archive_filename=None)

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "Anything"}) is True

    async def test_lets_an_archive_without_a_filename_through(self, db_session):
        # `filename` is NOT NULL, but nothing stops it being empty.
        item = await _seed(db_session, archive_filename="")

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "Anything"}) is True

    async def test_lets_a_dangling_archive_reference_through(self, db_session):
        item = await _seed(db_session, archive_filename=None)
        item.archive_id = 999999

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "Anything"}) is True


class TestDisposableDatabaseGuard:
    """The suite must never be able to open a session against a real database.

    ``run_with_retry`` takes its session from ``backend.app.core.database``, not
    from the ``backend.app.main.async_session`` that most tests patch, so an
    unmocked completion path reaches the app's module-level engine. That engine
    is built from ``DATABASE_URL``; conftest redirects it to a throwaway SQLite
    file and asserts the redirect took, because the alternative is a suite that
    passes while having edited someone's live print history.
    """

    def test_the_app_engine_points_at_a_throwaway_sqlite_file(self):
        from backend.app.core.database import engine
        from backend.tests.conftest import _TEST_APP_DB_DIR

        assert engine.url.drivername.startswith("sqlite")
        assert str(engine.url.database).startswith(str(_TEST_APP_DB_DIR))

    def test_the_guard_rejects_a_real_database(self):
        from sqlalchemy.engine import make_url

        from backend.tests.conftest import _assert_disposable_database

        with pytest.raises(RuntimeError, match="Refusing to run tests"):
            _assert_disposable_database(
                make_url("postgresql+asyncpg://user:pw@192.168.0.2:5432/bambuddy"),
                "test",
            )

    def test_the_guard_rejects_another_sqlite_file(self):
        # A developer's own data/bambuddy.db is just as real as a server.
        from sqlalchemy.engine import make_url

        from backend.tests.conftest import _assert_disposable_database

        with pytest.raises(RuntimeError, match="Refusing to run tests"):
            _assert_disposable_database(make_url("sqlite+aiosqlite:///data/bambuddy.db"), "test")
