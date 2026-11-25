"""A finish photo only its run kept is put back on its archive, once.

A later print's finish photo was appended to the archive's photo list in
place, which the ORM does not write, so only the run's list had it (archive
#455). The archive serves and shows photos from its own list.
"""

from datetime import datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.core.config import settings
from backend.app.core.database import _backfill_archive_run_photos
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry

FLAG = "_backfill_archive_run_photos_done"


@pytest.fixture
async def engine(test_engine, tmp_path, monkeypatch):
    # The suite's database, so a --pg run checks it on PostgreSQL too.
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    return test_engine


def _photo(tmp_path, folder: str, name: str) -> None:
    photos = tmp_path / folder / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / name).write_bytes(b"jpg")


async def _seed(engine, archives: dict[str, list | None], runs: list[tuple[str, int, list | None]]) -> dict[str, int]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        rows = {
            name: PrintArchive(
                filename=f"{name}.3mf", file_path=f"{name}/{name}.3mf", file_size=1, status="completed", photos=photos
            )
            for name, photos in archives.items()
        }
        db.add_all(rows.values())
        await db.flush()
        for name, minute, photos in runs:
            db.add(
                PrintLogEntry(
                    archive_id=rows[name].id,
                    status="completed",
                    started_at=datetime(2026, 11, 5, 9, minute),
                    photos=photos,
                )
            )
        await db.commit()
        return {name: row.id for name, row in rows.items()}


async def _photos(engine) -> dict[int, list | None]:
    maker = async_sessionmaker(engine)
    async with maker() as db:
        return dict((await db.execute(select(PrintArchive.id, PrintArchive.photos))).tuples().all())


async def _run(engine) -> None:
    async with engine.begin() as conn:
        await _backfill_archive_run_photos(conn)


@pytest.mark.asyncio
async def test_a_photo_only_its_run_kept_is_put_back_in_run_order(engine, tmp_path):
    for name in ("p1.jpg", "p2.jpg", "p3.jpg", "upload.jpg"):
        _photo(tmp_path, "a", name)
    ids = await _seed(
        engine,
        {"a": ["p1.jpg", "upload.jpg"]},
        # Inserted out of order: the archive takes them as the prints ran.
        [("a", 30, ["p3.jpg"]), ("a", 0, ["p1.jpg"]), ("a", 15, ["p2.jpg"])],
    )

    await _run(engine)

    assert (await _photos(engine))[ids["a"]] == ["p1.jpg", "upload.jpg", "p2.jpg", "p3.jpg"]


@pytest.mark.asyncio
async def test_an_archive_with_no_photos_left_gets_its_runs(engine, tmp_path):
    _photo(tmp_path, "a", "p2.jpg")
    ids = await _seed(engine, {"a": None}, [("a", 0, None), ("a", 10, ["p2.jpg"])])

    await _run(engine)

    assert (await _photos(engine))[ids["a"]] == ["p2.jpg"]


@pytest.mark.asyncio
async def test_a_photo_whose_file_is_gone_stays_off(engine, tmp_path):
    _photo(tmp_path, "a", "kept.jpg")
    ids = await _seed(
        engine, {"a": ["p1.jpg"], "b": ["x.jpg"]}, [("a", 0, ["gone.jpg", "kept.jpg"]), ("b", 0, ["gone.jpg"])]
    )

    await _run(engine)

    photos = await _photos(engine)
    assert photos[ids["a"]] == ["p1.jpg", "kept.jpg"]
    # Another archive's directory holding the name does not count.
    assert photos[ids["b"]] == ["x.jpg"]


@pytest.mark.asyncio
async def test_archives_that_already_list_their_runs_photos_are_left_alone(engine, tmp_path):
    _photo(tmp_path, "a", "p1.jpg")
    ids = await _seed(engine, {"a": ["upload.jpg", "p1.jpg"], "b": ["only.jpg"]}, [("a", 0, ["p1.jpg"])])

    await _run(engine)

    photos = await _photos(engine)
    assert photos[ids["a"]] == ["upload.jpg", "p1.jpg"]
    assert photos[ids["b"]] == ["only.jpg"]


@pytest.mark.asyncio
async def test_it_runs_once(engine, tmp_path):
    _photo(tmp_path, "a", "p2.jpg")
    ids = await _seed(engine, {"a": ["p1.jpg"]}, [("a", 0, ["p2.jpg"])])

    await _run(engine)
    async with engine.begin() as conn:
        flag = (await conn.execute(text('SELECT value FROM settings WHERE "key" = :k'), {"k": FLAG})).scalar_one()
        assert flag == "true"
        # Taken off the archive again after the repair: a later start must not
        # be what puts it back.
        await conn.execute(text("UPDATE print_archives SET photos = '[\"p1.jpg\"]' WHERE id = :id"), {"id": ids["a"]})

    await _run(engine)

    assert (await _photos(engine))[ids["a"]] == ["p1.jpg"]


@pytest.mark.asyncio
async def test_nothing_to_repair_still_marks_it_done(engine):
    await _run(engine)
    await _run(engine)

    async with engine.begin() as conn:
        count = (await conn.execute(text('SELECT COUNT(*) FROM settings WHERE "key" = :k'), {"k": FLAG})).scalar_one()
    assert count == 1


def test_every_start_runs_it():
    import inspect

    from backend.app.core.database import _run_migrations

    assert "await _backfill_archive_run_photos(conn)" in inspect.getsource(_run_migrations)
