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

from backend.app.main import (
    _ask_outcome_for_external_print,
    _completion_belongs_to_queue_item,
    _subtask_name_from_filename,
)
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings


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
    printer = Printer(name="P", serial_number="00M09A000000001", ip_address="192.168.1.100", access_code="12345678")
    db.add(printer)
    await db.flush()
    archive_id = None
    if archive_filename is not None:
        archive = PrintArchive(
            printer_id=printer.id,
            filename=archive_filename,
            file_path=f"archive/1/{archive_filename}",
            file_size=1,
            status="printing",
        )
        db.add(archive)
        await db.flush()
        archive_id = archive.id

    item = PrintQueueItem(printer_id=printer.id, status="printing", archive_id=archive_id)
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

    @pytest.mark.parametrize(
        ("archive_filename", "subtask"),
        [
            # A1 mini after power-loss recovery: dispatched as the bare name,
            # resumed and completed under the file name (#3485).
            (
                "Bottom-heavy; stands upright without saucer.3mf",
                "Bottom-heavy;_stands_upright_without_saucer.3mf",
            ),
            ("mini-bench_plate_1.3mf", "mini-bench_plate_1.3mf"),
            ("new bench.gcode.3mf", "new_bench.3mf"),
            ("new bench.gcode.3mf", "new_bench.gcode.3mf"),
        ],
    )
    async def test_accepts_its_own_print_echoed_with_the_extension(self, db_session, archive_filename, subtask):
        item = await _seed(db_session, archive_filename=archive_filename)

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": subtask}) is True

    async def test_a_model_named_like_a_file_still_matches(self, db_session):
        # "Foo.3mf" is the model's own name here; stripping the echo to "Foo"
        # must not be the only form tried.
        item = await _seed(db_session, archive_filename="Foo.3mf.gcode.3mf")

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "Foo.3mf"}) is True

    async def test_an_extension_does_not_make_another_print_match(self, db_session):
        item = await _seed(db_session, archive_filename="AMS_Rack.gcode.3mf")

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "Test.3mf"}) is False

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

    @pytest.mark.sqlite_only  # a dangling reference needs foreign keys off
    async def test_lets_a_dangling_archive_reference_through(self, db_session):
        item = await _seed(db_session, archive_filename=None)
        item.archive_id = 999999

        assert await _completion_belongs_to_queue_item(db_session, item, {"subtask_name": "Anything"}) is True


@pytest.mark.asyncio
class TestOutcomePromptForAResumedQueuePrint:
    """The outcome prompt holds a printing queue row against the observed name
    with the same rule, so a resumed queue print must not read as external."""

    async def test_a_resumed_queue_print_is_not_asked_about(self, db_session):
        item = await _seed(db_session, archive_filename="mini-bench_plate_1.3mf")
        db_session.add(Settings(key="confirm_outcome_external_prints", value="true"))
        await db_session.flush()

        assert await _ask_outcome_for_external_print(db_session, item.printer_id, "mini-bench_plate_1.3mf") is False

    async def test_another_file_is_still_asked_about(self, db_session):
        item = await _seed(db_session, archive_filename="mini-bench_plate_1.3mf")
        db_session.add(Settings(key="confirm_outcome_external_prints", value="true"))
        await db_session.flush()

        assert await _ask_outcome_for_external_print(db_session, item.printer_id, "Other.3mf") is True


@pytest.mark.sqlite_only  # on PostgreSQL the guard checks the throwaway server's databases instead
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
