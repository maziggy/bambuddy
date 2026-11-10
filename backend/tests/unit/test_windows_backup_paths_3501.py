"""A backup from a Windows install restored on Linux finds its files (#3501).

Windows stores paths relative to the data folder with backslashes; Linux reads
them as part of one file name. The restore turns them into slashes.
"""

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_log import PrintLogEntry
from backend.app.services.backup_paths import convert_windows_paths


def _windows_rows():
    archive = PrintArchive(
        filename="Brachy.3mf",
        file_path="archive\\12\\Brachy.3mf",
        file_size=1,
        thumbnail_path="archive\\12\\thumbnail.png",
        timelapse_path="archive\\12\\timelapse.mp4",
        source_3mf_path="archive\\12\\source\\Brachy.3mf",
        f3d_path="archive\\12\\design.f3d",
    )
    library_file = LibraryFile(
        filename="Brachy.3mf",
        file_path="archive\\library\\files\\abc.3mf",
        file_type="3mf",
        file_size=1,
        thumbnail_path="archive\\library\\thumbnails\\abc.png",
    )
    log_entry = PrintLogEntry(
        status="completed",
        thumbnail_path="archive\\12\\thumbnail.png",
        timelapse_path="archive\\12\\timelapse.mp4",
    )
    return archive, library_file, log_entry


async def _convert(db_session, base_dir, **kwargs) -> int:
    await db_session.flush()
    changed = await convert_windows_paths(await db_session.connection(), base_dir, **kwargs)
    db_session.expire_all()
    return changed


@pytest.mark.asyncio
async def test_windows_paths_get_slashes(db_session, tmp_path):
    db_session.add_all(_windows_rows())

    assert await _convert(db_session, tmp_path, windows=False) == 3

    archive = (await db_session.execute(select(PrintArchive))).scalar_one()
    assert (
        archive.file_path,
        archive.thumbnail_path,
        archive.timelapse_path,
        archive.source_3mf_path,
        archive.f3d_path,
    ) == (
        "archive/12/Brachy.3mf",
        "archive/12/thumbnail.png",
        "archive/12/timelapse.mp4",
        "archive/12/source/Brachy.3mf",
        "archive/12/design.f3d",
    )
    library_file = (await db_session.execute(select(LibraryFile))).scalar_one()
    assert (library_file.file_path, library_file.thumbnail_path) == (
        "archive/library/files/abc.3mf",
        "archive/library/thumbnails/abc.png",
    )
    log_entry = (await db_session.execute(select(PrintLogEntry))).scalar_one()
    assert (log_entry.thumbnail_path, log_entry.timelapse_path) == (
        "archive/12/thumbnail.png",
        "archive/12/timelapse.mp4",
    )


@pytest.mark.asyncio
async def test_a_file_whose_name_has_a_backslash_is_kept(db_session, tmp_path):
    # On Linux a backslash can be part of a real file name.
    (tmp_path / "archive" / "7").mkdir(parents=True)
    (tmp_path / "archive" / "7" / "a\\b.3mf").write_bytes(b"x")
    db_session.add(PrintArchive(filename="a\\b.3mf", file_path="archive/7/a\\b.3mf", file_size=1))

    assert await _convert(db_session, tmp_path, windows=False) == 0

    archive = (await db_session.execute(select(PrintArchive))).scalar_one()
    assert archive.file_path == "archive/7/a\\b.3mf"


@pytest.mark.asyncio
async def test_nothing_changes_on_windows(db_session, tmp_path):
    db_session.add_all(_windows_rows())

    assert await _convert(db_session, tmp_path, windows=True) == 0

    archive = (await db_session.execute(select(PrintArchive))).scalar_one()
    assert archive.file_path == "archive\\12\\Brachy.3mf"


@pytest.mark.asyncio
async def test_linux_paths_and_empty_columns_are_left_alone(db_session, tmp_path):
    db_session.add(PrintArchive(filename="x.3mf", file_path="archive/3/x.3mf", file_size=1, thumbnail_path=None))
    db_session.add(PrintLogEntry(status="completed"))

    assert await _convert(db_session, tmp_path, windows=False) == 0


@pytest.mark.asyncio
async def test_a_second_run_changes_nothing(db_session, tmp_path):
    db_session.add_all(_windows_rows())
    await _convert(db_session, tmp_path, windows=False)

    assert await _convert(db_session, tmp_path, windows=False) == 0
