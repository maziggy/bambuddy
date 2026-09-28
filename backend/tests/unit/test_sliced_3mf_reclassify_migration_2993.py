"""The one-shot pass that reaches library rows already stored (#2993).

The forward fix classifies on content, which does nothing for the files a user
has already imported -- the reporter's whole complaint was about files they had
downloaded and imported before this existed. This backfill re-opens them.

Two properties matter as much as the re-typing itself. It must run once: a
genuine source 3MF keeps matching ``file_type = '3mf'`` forever, so an ungated
pass would re-open every model in the library on every boot. And it must leave
external rows alone: they point at a mount that can be slow, unmounted, or
enormous, and startup is the worst place to discover that.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.fixture(autouse=True)
def base_dir(tmp_path, monkeypatch):
    """Relative file_path values resolve against settings.base_dir."""
    from backend.app.core.database import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    return tmp_path


def _register_all_models():
    from backend.app.models import (  # noqa: F401
        ams_history,
        ams_label,
        api_key,
        archive,
        color_catalog,
        external_link,
        filament,
        group,
        kprofile_note,
        library,
        maintenance,
        notification,
        notification_template,
        print_log,
        print_queue,
        printer,
        project,
        project_bom,
        settings,
        slot_preset,
        smart_plug,
        smart_plug_energy_snapshot,
        spool,
        spool_assignment,
        spool_catalog,
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


@pytest.fixture
async def engine():
    from backend.app.core.database import Base

    _register_all_models()

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _write_3mf(path: Path, *, sliced: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = ["3D/3dmodel.model"] + (["Metadata/plate_3.gcode"] if sliced else ["Metadata/plate_1.png"])
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")


async def _insert_file(conn, *, file_id: int, filename: str, path: str, external: bool = False) -> None:
    await conn.execute(
        text(
            "INSERT INTO library_files "
            "(id, filename, file_path, file_type, file_size, is_external, print_count) "
            "VALUES (:id, :filename, :path, '3mf', 0, :ext, 0)"
        ),
        {"id": file_id, "filename": filename, "path": path, "ext": 1 if external else 0},
    )


async def _types(engine) -> dict[int, str]:
    async with engine.connect() as conn:
        return dict((await conn.execute(text("SELECT id, file_type FROM library_files ORDER BY id"))).fetchall())


@pytest.mark.asyncio
async def test_a_stored_sliced_3mf_is_re_typed(engine, base_dir):
    _write_3mf(base_dir / "files/sliced.3mf", sliced=True)
    _write_3mf(base_dir / "files/model.3mf", sliced=False)
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="Labyrinth.3mf", path="files/sliced.3mf")
        await _insert_file(conn, file_id=2, filename="Labyrinth.3mf", path="files/model.3mf")

    async with engine.begin() as conn:
        await run_migrations(conn)

    types = await _types(engine)
    assert types[1] == "gcode.3mf"
    assert types[2] == "3mf", "a genuine project must not gain a Print button"


@pytest.mark.asyncio
async def test_a_missing_file_does_not_stop_the_pass(engine, base_dir):
    """A library with holes in it still finishes -- the row after the gap is
    the one that proves it."""
    _write_3mf(base_dir / "files/sliced.3mf", sliced=True)
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="gone.3mf", path="files/gone.3mf")
        await _insert_file(conn, file_id=2, filename="Labyrinth.3mf", path="files/sliced.3mf")

    async with engine.begin() as conn:
        await run_migrations(conn)

    types = await _types(engine)
    assert types[1] == "3mf"
    assert types[2] == "gcode.3mf"


@pytest.mark.asyncio
async def test_external_rows_are_left_to_their_own_scan(engine, base_dir):
    """Even a sliced one. The folder's scan re-types it without putting a
    possibly-unreachable mount on the startup path."""
    _write_3mf(base_dir / "mount/sliced.3mf", sliced=True)
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="Labyrinth.3mf", path="mount/sliced.3mf", external=True)

    async with engine.begin() as conn:
        await run_migrations(conn)

    assert (await _types(engine))[1] == "3mf"


@pytest.mark.asyncio
async def test_it_runs_once(engine, base_dir):
    """Every boot re-runs the migration set. A source 3MF matches the query
    forever, so without the flag this re-opens the whole library each time."""
    _write_3mf(base_dir / "files/model.3mf", sliced=False)
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="Labyrinth.3mf", path="files/model.3mf")

    async with engine.begin() as conn:
        await run_migrations(conn)

    # Second boot: the file becomes readable-as-sliced, and must be ignored,
    # which can only happen if the pass is genuinely gated rather than merely
    # idempotent in its effect.
    _write_3mf(base_dir / "files/model.3mf", sliced=True)
    async with engine.begin() as conn:
        await run_migrations(conn)

    assert (await _types(engine))[1] == "3mf"
