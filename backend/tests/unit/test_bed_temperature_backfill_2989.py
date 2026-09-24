"""Archives written before #2989 get their bed temperature read back off disk.

The extractor looked for a ``bed_temperature`` key BambuStudio does not write,
so every archive from a Bambu slice stored NULL -- 0 of 455 real 3MFs resolved
on the install this was measured on. The forward fix reads the array the fitted
plate points at, but only for archives made after it; everything already in the
library stays blank, and preheat keeps falling back to the keep-warm bed
temperature whenever one of those jobs is reprinted from the queue.

This one-shot re-reads the 3MF that is already on disk. It fills NULLs and
nothing else: no value is invented, none is overwritten, and an archive whose
file is gone stays NULL rather than being guessed at.

The 3MFs here are real zips rather than a patched extractor, because
``extract_bed_temperature_from_3mf`` is itself new code and stubbing it would
leave the only thing this migration depends on untested.
"""

import json
import logging
import zipfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core import database as database_module
from backend.app.core.database import Base, _backfill_archive_bed_temperature
from backend.app.models.archive import PrintArchive

# A Textured PEI slice of a two-filament project, in the shape BambuStudio
# writes: an array per plate type, and the fitted plate named separately.
_PEI_55 = {
    "curr_bed_type": "Textured PEI Plate",
    "cool_plate_temp": ["0", "0"],
    "eng_plate_temp": ["0", "0"],
    "hot_plate_temp": ["0", "0"],
    "textured_plate_temp_initial_layer": ["55", "55"],
    "textured_plate_temp": ["55", "55"],
    "supertack_plate_temp": ["0", "0"],
}


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(database_module.settings, "base_dir", tmp_path)
    return tmp_path


def _write_3mf(data_dir, relative: str, config: dict | None) -> str:
    """A 3MF on disk. ``config=None`` writes a file that is not a zip at all."""
    path = data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if config is None:
        path.write_bytes(b"not a zip")
        return relative
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(config))
    return relative


async def _archive(db, file_path: str, *, bed_temperature=None) -> PrintArchive:
    archive = PrintArchive(
        filename="Benchy.gcode.3mf",
        file_path=file_path,
        file_size=1,
        status="completed",
        bed_temperature=bed_temperature,
    )
    db.add(archive)
    await db.flush()
    return archive


async def _bed_temperature(engine, archive_id: int):
    async with engine.begin() as conn:
        return (
            await conn.execute(text("SELECT bed_temperature FROM print_archives WHERE id = :id"), {"id": archive_id})
        ).scalar_one()


class TestItFillsWhatItCan:
    @pytest.mark.asyncio
    async def test_a_null_is_read_from_the_plate_the_project_is_sliced_for(self, engine, data_dir):
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            relative = _write_3mf(data_dir, "archive/1/20260828_Benchy/Benchy.gcode.3mf", _PEI_55)
            archive = await _archive(db, relative)
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, archive.id) == 55

    @pytest.mark.asyncio
    async def test_an_orca_export_still_resolves(self, engine, data_dir):
        """The generic spelling is the fallback, not a second-class citizen."""
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            relative = _write_3mf(data_dir, "archive/1/a/Benchy.gcode.3mf", {"bed_temperature": 60})
            archive = await _archive(db, relative)
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, archive.id) == 60

    @pytest.mark.asyncio
    async def test_several_archives_in_one_pass(self, engine, data_dir):
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            first = await _archive(db, _write_3mf(data_dir, "archive/1/a/x.3mf", _PEI_55))
            second = await _archive(
                db,
                _write_3mf(
                    data_dir, "archive/1/b/y.3mf", {"curr_bed_type": "High Temp Plate", "hot_plate_temp": ["100"]}
                ),
            )
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, first.id) == 55
        assert await _bed_temperature(engine, second.id) == 100


class TestWhatItRefusesToTouch:
    @pytest.mark.asyncio
    async def test_a_value_already_recorded_is_left_alone(self, engine, data_dir):
        """Only NULLs. A temperature somebody set, or one a later archive read
        correctly, must not be rewritten from the file."""
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            relative = _write_3mf(data_dir, "archive/1/a/Benchy.gcode.3mf", _PEI_55)
            archive = await _archive(db, relative, bed_temperature=90)
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, archive.id) == 90

    @pytest.mark.asyncio
    async def test_a_no_3mf_archive_stays_null(self, engine, data_dir):
        """``file_path == ""`` is the ordinary shape of a Studio-sent H2 print.
        There is no file to read, and inventing one is the whole bug."""
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            archive = await _archive(db, "")
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, archive.id) is None

    @pytest.mark.asyncio
    async def test_a_file_that_is_gone_stays_null(self, engine, data_dir):
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            archive = await _archive(db, "archive/1/a/deleted.gcode.3mf")
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, archive.id) is None

    @pytest.mark.asyncio
    async def test_a_file_that_is_not_a_zip_stays_null(self, engine, data_dir):
        """A truncated or corrupted 3MF must not take the whole boot down."""
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            archive = await _archive(db, _write_3mf(data_dir, "archive/1/a/broken.3mf", None))
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, archive.id) is None

    @pytest.mark.asyncio
    async def test_an_all_zero_plate_array_stays_null(self, engine, data_dir):
        """0 means no filament in the project prints on this plate. Recording
        it would read as a cold bed, which is worse than nothing."""
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            relative = _write_3mf(
                data_dir,
                "archive/1/a/zero.3mf",
                {"curr_bed_type": "Cool Plate", "cool_plate_temp": ["0", "0"]},
            )
            archive = await _archive(db, relative)
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, archive.id) is None


class TestItCannotStopBambuddyBooting:
    """The migration sequence has no handler above it.

    ``run_migrations`` is awaited straight from ``init_db`` with no try/except,
    so anything escaping this function stops startup -- and keeps stopping it,
    because the one-shot flag is written inside the transaction that just rolled
    back. Measured: two consecutive boots, same failure, flag never written. So
    the guards here are load-bearing rather than tidy.
    """

    @pytest.mark.asyncio
    async def test_an_unexpected_exception_costs_one_archive_not_the_boot(self, engine, data_dir, monkeypatch, caplog):
        """Not a listed zip error -- the point is that the guard does not depend
        on having predicted which exception a bad file raises."""
        import backend.app.utils.threemf_tools as tools

        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            bad = await _archive(db, _write_3mf(data_dir, "archive/1/a/bad.3mf", _PEI_55))
            await db.commit()

        def _explode(_path):
            raise RecursionError("boom")

        monkeypatch.setattr(tools, "extract_bed_temperature_from_3mf", _explode)

        with caplog.at_level(logging.WARNING):
            async with engine.begin() as conn:
                await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, bad.id) is None
        assert any("could not read" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_one_bad_archive_does_not_stop_the_others(self, engine, data_dir, monkeypatch):
        """The guard is per row, so the rest of the library is still repaired."""
        import backend.app.utils.threemf_tools as tools

        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            bad = await _archive(db, _write_3mf(data_dir, "archive/1/a/bad.3mf", _PEI_55))
            good = await _archive(db, _write_3mf(data_dir, "archive/1/b/good.3mf", _PEI_55))
            await db.commit()

        real = tools.extract_bed_temperature_from_3mf

        def _explode_on_bad(path):
            if path.name == "bad.3mf":
                raise RecursionError("boom")
            return real(path)

        monkeypatch.setattr(tools, "extract_bed_temperature_from_3mf", _explode_on_bad)

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, bad.id) is None
        assert await _bed_temperature(engine, good.id) == 55

    @pytest.mark.asyncio
    async def test_the_extractor_swallows_anything_a_file_can_throw(self, tmp_path):
        """Its callers are inside startup, so None is the only outcome."""
        from backend.app.utils.threemf_tools import extract_bed_temperature_from_3mf

        missing = tmp_path / "nope.3mf"
        directory = tmp_path / "adir.3mf"
        directory.mkdir()
        truncated = tmp_path / "cut.3mf"
        truncated.write_bytes(b"PK\x03\x04 and then nothing")
        empty = tmp_path / "empty.3mf"
        empty.write_bytes(b"")

        for candidate in (missing, directory, truncated, empty):
            assert extract_bed_temperature_from_3mf(candidate) is None

    @pytest.mark.asyncio
    async def test_the_extractor_swallows_what_no_one_predicted(self, tmp_path, monkeypatch):
        """The four cases above all raise OSError or BadZipFile, so on their own
        they would still pass with the guard narrowed back to that pair. This
        one forces something outside it, which is the whole reason the catch is
        broad -- the failure being guarded is an exception nobody listed."""
        import backend.app.utils.threemf_tools as tools

        good = tmp_path / "ok.3mf"
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("Metadata/project_settings.config", json.dumps(_PEI_55))
        assert tools.extract_bed_temperature_from_3mf(good) == 55

        class _Exploding:
            def __init__(self, *a, **k):
                raise RecursionError("boom")

        monkeypatch.setattr(tools.zipfile, "ZipFile", _Exploding)

        assert tools.extract_bed_temperature_from_3mf(good) is None

    @pytest.mark.asyncio
    async def test_a_flag_row_with_an_empty_value_does_not_re_run(self, engine, data_dir):
        """``if already:`` would treat "" as not-done, re-run, and then fail the
        unique key on the INSERT -- a boot loop from a single odd row."""
        async with engine.begin() as conn:
            await conn.execute(
                text('INSERT INTO settings ("key", value) VALUES (:k, :v)'),
                {"k": "_backfill_2989_bed_temperature_done", "v": ""},
            )

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)
        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)


class TestItRunsExactlyOnce:
    @pytest.mark.asyncio
    async def test_the_flag_is_written_even_when_nothing_matched(self, engine, data_dir):
        """The rows it cannot fill are the ones it would reopen every boot."""
        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        async with engine.begin() as conn:
            flag = (
                await conn.execute(
                    text('SELECT value FROM settings WHERE "key" = :k'),
                    {"k": "_backfill_2989_bed_temperature_done"},
                )
            ).scalar_one_or_none()

        assert flag == "true"

    @pytest.mark.asyncio
    async def test_a_second_boot_does_not_rescan(self, engine, data_dir):
        """An archive added after the one-shot has run is left to the forward
        fix, which is what writes bed_temperature for anything new."""
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            first = await _archive(db, _write_3mf(data_dir, "archive/1/a/x.3mf", _PEI_55))
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)
        assert await _bed_temperature(engine, first.id) == 55

        async with sm() as db:
            later = await _archive(db, _write_3mf(data_dir, "archive/1/b/y.3mf", _PEI_55))
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_archive_bed_temperature(conn)

        assert await _bed_temperature(engine, later.id) is None
        # And the flag was not written twice, which the settings table's unique
        # key would refuse anyway -- the guard is the SELECT, not the database.
        async with engine.begin() as conn:
            count = (
                await conn.execute(
                    text('SELECT COUNT(*) FROM settings WHERE "key" = :k'),
                    {"k": "_backfill_2989_bed_temperature_done"},
                )
            ).scalar_one()
        assert count == 1
