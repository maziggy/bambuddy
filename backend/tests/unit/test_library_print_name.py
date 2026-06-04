"""Tests for library files displaying the filename, not the embedded 3MF Title (#1489).

The 3MF ``<metadata name="Title">`` is the in-app project title — generic
("Exported 3D Model") for a Bambu Studio "Save As", a marketing title for a
MakerWorld download — never the filename the user saved as. The FileManager
keyed its display name / search / sort off ``file_metadata.print_name``, so
storing the Title made every card show the wrong name. ``_without_print_name``
strips it on import; ``_migrate_drop_library_print_name`` clears it from rows
imported before the fix.
"""

from sqlalchemy import select

from backend.app.api.routes.library import _without_print_name
from backend.app.core.database import _migrate_drop_library_print_name
from backend.app.models.library import LibraryFile

# --- _without_print_name ---------------------------------------------------


def test_strips_print_name_keeps_siblings():
    cleaned = _without_print_name({"print_name": "Exported 3D Model", "print_time_seconds": 100})
    assert cleaned == {"print_time_seconds": 100}


def test_none_passes_through():
    assert _without_print_name(None) is None


def test_dict_without_print_name_returned_unchanged():
    meta = {"print_time_seconds": 50}
    # No copy needed when there's nothing to strip — same object back.
    assert _without_print_name(meta) is meta


def test_does_not_mutate_input():
    original = {"print_name": "Whatever", "filament_used_grams": 12}
    cleaned = _without_print_name(original)
    assert original == {"print_name": "Whatever", "filament_used_grams": 12}  # untouched
    assert cleaned == {"filament_used_grams": 12}


def test_print_name_only_collapses_to_empty_dict():
    assert _without_print_name({"print_name": "Exported 3D Model"}) == {}


# --- _migrate_drop_library_print_name --------------------------------------


async def test_migration_strips_print_name_from_existing_rows(db_session, monkeypatch):
    """Rows imported before the fix get print_name cleared; siblings and rows
    that never had it are untouched. Idempotent on a second run.

    The test DB is SQLite; is_sqlite() reads settings.database_url (not the
    test engine), so pin it to exercise the SQLite branch deterministically.
    The PostgreSQL branch is verified against a real PG instance separately."""
    monkeypatch.setattr("backend.app.core.database.is_sqlite", lambda: True)
    db_session.add_all(
        [
            LibraryFile(
                filename="halloween.3mf",
                file_path="/a",
                file_type="3mf",
                file_size=1,
                file_metadata={"print_name": "Haunted House", "print_time_seconds": 100},
            ),
            LibraryFile(
                filename="no_meta.3mf",
                file_path="/b",
                file_type="3mf",
                file_size=1,
                file_metadata={"print_time_seconds": 50},
            ),
            LibraryFile(
                filename="null_meta.3mf",
                file_path="/c",
                file_type="3mf",
                file_size=1,
                file_metadata=None,
            ),
        ]
    )
    await db_session.commit()

    conn = await db_session.connection()
    await _migrate_drop_library_print_name(conn)
    await _migrate_drop_library_print_name(conn)  # idempotent

    db_session.expire_all()
    rows = (await db_session.execute(select(LibraryFile).order_by(LibraryFile.filename))).scalars().all()
    by_name = {r.filename: r for r in rows}

    assert by_name["halloween.3mf"].file_metadata == {"print_time_seconds": 100}
    assert by_name["no_meta.3mf"].file_metadata == {"print_time_seconds": 50}
    assert by_name["null_meta.3mf"].file_metadata is None
