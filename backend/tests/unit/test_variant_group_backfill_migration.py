"""Tests for the variant-group backfill migration (#671 / #2570).

`sliced_from_library_file_id` has been written into `library_files.file_metadata`
by the Slice button and the pipeline runner since those features shipped, and
nothing ever read it back. The migration promotes that inert provenance into
real `file_variant_groups` membership so an existing library arrives with its
slice sets already grouped.

The interesting behaviour is all in what it refuses to group: a lone child, two
children sliced for the same printer, files the user has already grouped by
hand, and trashed rows.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


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


async def _insert_file(
    conn,
    *,
    file_id: int,
    filename: str,
    metadata: dict | None = None,
    deleted: bool = False,
    variant_group_id: int | None = None,
) -> None:
    """Insert a minimal LibraryFile row; only the columns the migration reads."""
    await conn.execute(
        text(
            "INSERT INTO library_files "
            "(id, filename, file_path, file_type, file_size, is_external, print_count, "
            " file_metadata, deleted_at, variant_group_id, variant_position) "
            "VALUES (:id, :filename, :path, 'gcode.3mf', 0, 0, 0, :meta, :deleted, :gid, 0)"
        ),
        {
            "id": file_id,
            "filename": filename,
            "path": f"/lib/{file_id}",
            "meta": json.dumps(metadata) if metadata is not None else None,
            "deleted": "2026-01-01 00:00:00" if deleted else None,
            "gid": variant_group_id,
        },
    )


def _variant(source_id: int, model: str) -> dict:
    return {"sliced_from_library_file_id": source_id, "sliced_for_model": model}


async def _members(conn) -> dict[int, tuple[int | None, int]]:
    rows = (
        await conn.execute(text("SELECT id, variant_group_id, variant_position FROM library_files ORDER BY id"))
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


async def _group_count(conn) -> int:
    return (await conn.execute(text("SELECT COUNT(*) FROM file_variant_groups"))).scalar()


@pytest.mark.asyncio
async def test_groups_two_variants_of_the_same_source(engine):
    """The whole point: an H2S slice and an H2C slice of one model become a group."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(1, "H2S"))
        await _insert_file(conn, file_id=3, filename="bracket_h2c.gcode.3mf", metadata=_variant(1, "H2C"))

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 1
        members = await _members(conn)
        gid = members[2][0]
        assert gid is not None
        assert members[3][0] == gid, "both slices land in the same group"
        assert members[1][0] is None, "the unsliced source is not a dispatch candidate"
        assert (members[2][1], members[3][1]) == (0, 1), "position follows id order, deterministically"

        name = (await conn.execute(text("SELECT name FROM file_variant_groups"))).scalar()
        assert name == "bracket.3mf", "the group is named after the source the user recognises"


@pytest.mark.asyncio
async def test_single_variant_produces_no_group(engine):
    """One candidate is not a choice — grouping it would add a row per sliced
    file in every library while changing nothing at print time."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(1, "H2S"))

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 0
        assert (await _members(conn))[2][0] is None


@pytest.mark.asyncio
async def test_duplicate_model_is_skipped_whole(engine):
    """Two slices for the same printer are not alternatives — the resolver would
    have no basis to prefer one, so the source is left entirely alone."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(conn, file_id=2, filename="bracket_draft.gcode.3mf", metadata=_variant(1, "H2S"))
        await _insert_file(conn, file_id=3, filename="bracket_fine.gcode.3mf", metadata=_variant(1, "H2S"))

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 0
        members = await _members(conn)
        assert members[2][0] is None and members[3][0] is None


@pytest.mark.asyncio
async def test_variant_without_model_is_not_a_candidate(engine):
    """A child with no `sliced_for_model` can never be matched to a printer, so
    it does not count towards the two-candidate threshold."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(1, "H2S"))
        await _insert_file(
            conn,
            file_id=3,
            filename="bracket_unknown.gcode.3mf",
            metadata={"sliced_from_library_file_id": 1},
        )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 0


@pytest.mark.asyncio
async def test_trashed_variants_are_excluded(engine):
    """A soft-deleted file is not printable, so it must not make up the second
    candidate that tips a source into being grouped."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(1, "H2S"))
        await _insert_file(conn, file_id=3, filename="bracket_h2c.gcode.3mf", metadata=_variant(1, "H2C"), deleted=True)

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 0


@pytest.mark.asyncio
async def test_missing_source_still_groups_with_fallback_name(engine):
    """Deleting the source model does not make its slices any less usable
    together, so the group is still built — just named differently."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(99, "H2S"))
        await _insert_file(conn, file_id=3, filename="bracket_h2c.gcode.3mf", metadata=_variant(99, "H2C"))

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 1
        name = (await conn.execute(text("SELECT name FROM file_variant_groups"))).scalar()
        assert name == "H2S + 1 more"


@pytest.mark.asyncio
async def test_separate_sources_get_separate_groups(engine):
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(1, "H2S"))
        await _insert_file(conn, file_id=3, filename="bracket_h2c.gcode.3mf", metadata=_variant(1, "H2C"))
        await _insert_file(conn, file_id=4, filename="clip.3mf")
        await _insert_file(conn, file_id=5, filename="clip_h2s.gcode.3mf", metadata=_variant(4, "H2S"))
        await _insert_file(conn, file_id=6, filename="clip_h2c.gcode.3mf", metadata=_variant(4, "H2C"))

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 2
        members = await _members(conn)
        assert members[2][0] == members[3][0]
        assert members[5][0] == members[6][0]
        assert members[2][0] != members[5][0]


@pytest.mark.asyncio
async def test_backfill_is_idempotent(engine):
    """Every boot re-runs the migration set; the second pass must not clone the
    group or renumber its members."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(1, "H2S"))
        await _insert_file(conn, file_id=3, filename="bracket_h2c.gcode.3mf", metadata=_variant(1, "H2C"))

    async with engine.begin() as conn:
        await run_migrations(conn)
    async with engine.connect() as conn:
        first = await _members(conn)

    async with engine.begin() as conn:
        await run_migrations(conn)
    async with engine.connect() as conn:
        assert await _group_count(conn) == 1
        assert await _members(conn) == first


@pytest.mark.asyncio
async def test_hand_grouped_files_are_left_alone(engine):
    """A user who has already grouped (or deliberately ungrouped) files owns that
    decision — the backfill only ever considers files with no group yet."""
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO file_variant_groups (id, name) VALUES (7, 'my own grouping')"))
        await _insert_file(conn, file_id=1, filename="bracket.3mf")
        await _insert_file(
            conn, file_id=2, filename="bracket_h2s.gcode.3mf", metadata=_variant(1, "H2S"), variant_group_id=7
        )
        await _insert_file(conn, file_id=3, filename="bracket_h2c.gcode.3mf", metadata=_variant(1, "H2C"))

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert await _group_count(conn) == 1, "no second group is invented"
        members = await _members(conn)
        assert members[2][0] == 7, "the user's grouping survives"
        assert members[3][0] is None, "and the leftover sibling is not force-joined to it"
