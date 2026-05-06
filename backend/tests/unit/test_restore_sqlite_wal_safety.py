"""Regression tests for the SQLite WAL leftover bug in /settings/restore.

Background — see #1211 / #668. The live database runs in WAL mode
(``database.py:19``: ``PRAGMA journal_mode = WAL``). Anything written to
the database before the restore call that hasn't been checkpointed yet
sits in ``bambuddy.db-wal`` with valid checksums. The original restore
implementation used ``shutil.copy2(backup_db, db_path)`` which only
overwrites the main DB file's content, so on the next open SQLite found
the stale WAL and silently re-applied those page-level writes on top of
the restored DB — partially clobbering it with fresh-install state.

These tests exercise the bug condition deterministically (using the
classic reader-snapshot trick to prevent SQLite's close-time checkpoint)
and pin that the production restore path — the SQLite online backup API
called via ``src_conn.backup(dst_conn)`` — produces a clean restored DB
even with un-checkpointed WAL frames sitting on disk.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest


def _seed_live_db_with_uncheckpointed_wal(live_db: Path) -> sqlite3.Connection:
    """Create a SQLite DB in WAL mode with frames that haven't been
    checkpointed to the main file. Returns a still-open reader so the
    caller can keep the WAL alive and the close-time checkpoint blocked
    until the test is ready to assert.

    The returned connection holds an open ``BEGIN`` transaction, which is
    what prevents SQLite from auto-checkpointing the WAL on the writer's
    close. In production this role is played by the route handler's own
    ``db: Depends(get_db)`` session — FastAPI's dependency injection keeps
    that session alive across the entire request, ``engine.dispose()``
    doesn't touch checked-out connections, and the WAL accordingly
    persists with un-checkpointed frames at the moment the file copy
    would happen.
    """
    writer = sqlite3.connect(str(live_db))
    writer.execute("PRAGMA journal_mode = WAL")
    writer.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    # These rows are the "fresh-install defaults" — what would clobber the
    # restored DB if the WAL were re-applied.
    writer.execute("INSERT INTO settings VALUES ('energy_cost_per_kwh', '0.15')")
    writer.execute("INSERT INTO settings VALUES ('currency', 'EUR')")
    writer.commit()

    reader = sqlite3.connect(str(live_db))
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM settings").fetchall()  # acquires a snapshot

    writer.close()  # WAL persists because reader still holds the snapshot

    # Sanity: the WAL must actually contain frames, otherwise the test is
    # vacuous (we'd be testing the safe case, not the bug condition).
    wal = live_db.parent / f"{live_db.name}-wal"
    assert wal.exists() and wal.stat().st_size > 0, (
        "Test setup failed to leave un-checkpointed WAL frames; the bug condition isn't being exercised."
    )

    return reader


def _make_backup_db(backup_db: Path, *, energy: str, currency: str) -> None:
    """Build a 'backup' SQLite DB at the given path with the user's actual
    settings. Same schema as ``_seed_live_db_with_uncheckpointed_wal`` so
    a successful restore should replace the live DB row-for-row."""
    conn = sqlite3.connect(str(backup_db))
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO settings VALUES (?, ?)", ("energy_cost_per_kwh", energy))
        conn.execute("INSERT INTO settings VALUES (?, ?)", ("currency", currency))
        conn.commit()
    finally:
        conn.close()


def _read_settings(db_path: Path) -> dict[str, str]:
    """Open a fresh connection and return the settings rows as a dict."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return dict(rows)
    finally:
        conn.close()


def test_shutil_copy_loses_to_stale_wal(tmp_path):
    """Pin the bug: ``shutil.copy2`` over a live DB with un-checkpointed
    WAL leaves the WAL behind, and on the next open SQLite re-applies
    those frames on top of the copied content. The user sees a
    "successful" restore that mostly reverted to fresh-install defaults
    (energy=0.15, currency=EUR) instead of their values (0.12, USD).

    Pinned here so a future "small simplification" that replaces the
    backup API call with a file copy can't silently re-introduce the bug.
    """
    live = tmp_path / "live.db"
    backup = tmp_path / "backup.db"

    reader = _seed_live_db_with_uncheckpointed_wal(live)
    _make_backup_db(backup, energy="0.12", currency="USD")

    # The buggy restore: file copy over the live DB.
    shutil.copy2(backup, live)
    reader.close()

    settings = _read_settings(live)
    # The bug manifests as the live DB's WAL frames overwriting the
    # restored content. We pin the symptom directly: at least one of the
    # user's settings was clobbered by the fresh-install defaults.
    assert settings != {"energy_cost_per_kwh": "0.12", "currency": "USD"}, (
        "Expected the shutil.copy2 path to lose data to WAL leftover, "
        "but the restore was clean. If this assertion starts failing, "
        "either the test setup no longer reproduces the bug condition "
        "or SQLite's behaviour changed — re-investigate before relaxing."
    )


def test_sqlite_backup_api_replaces_live_db_safely(tmp_path):
    """Pin the fix: ``src.backup(dst)`` (SQLite online backup API) over a
    live DB that has un-checkpointed WAL frames produces a restored DB
    with exactly the backup contents. No fresh-install state leaks
    through.

    Mirrors the production path in ``backend/app/api/routes/settings.py``
    (``restore_backup``) so a regression in either the route or the
    helper used by it surfaces here.
    """
    live = tmp_path / "live.db"
    backup = tmp_path / "backup.db"

    reader = _seed_live_db_with_uncheckpointed_wal(live)
    _make_backup_db(backup, energy="0.12", currency="USD")

    # The production path: SQLite online backup API.
    src_conn = sqlite3.connect(str(backup))
    try:
        dst_conn = sqlite3.connect(str(live))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    reader.close()

    settings = _read_settings(live)
    assert settings == {"energy_cost_per_kwh": "0.12", "currency": "USD"}, (
        f"Restore lost or corrupted user data. Got {settings!r}. If "
        "energy_cost_per_kwh is back to 0.15 or currency is back to EUR "
        "the WAL leftover bug has regressed — see #1211."
    )


def test_sqlite_backup_api_works_when_no_wal_frames(tmp_path):
    """Defensive: the fix must also work in the simple case where the
    live DB has no leftover WAL (e.g. fresh container, restore as the
    very first action). Failing here would indicate the production path
    has accidentally become specific to the WAL-leftover scenario.
    """
    live = tmp_path / "live.db"
    backup = tmp_path / "backup.db"

    # Set up a live DB but force a checkpoint so WAL is empty.
    conn = sqlite3.connect(str(live))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('energy_cost_per_kwh', '0.15')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    _make_backup_db(backup, energy="0.12", currency="USD")

    src_conn = sqlite3.connect(str(backup))
    try:
        dst_conn = sqlite3.connect(str(live))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    settings = _read_settings(live)
    assert settings == {"energy_cost_per_kwh": "0.12", "currency": "USD"}


@pytest.mark.parametrize("backup_size_pages", [1, 100, 1000])
def test_sqlite_backup_api_handles_various_db_sizes(tmp_path, backup_size_pages):
    """The backup API copies in 4 KB pages — make sure single-page,
    medium, and multi-page DBs all round-trip correctly. A regression in
    backup-API usage that only manifested at one size would otherwise
    slip through.
    """
    live = tmp_path / "live.db"
    backup = tmp_path / "backup.db"

    # Live with un-checkpointed WAL (the bug condition).
    reader = _seed_live_db_with_uncheckpointed_wal(live)

    # Build a backup DB sized to roughly the requested page count.
    # 4 KB pages ≈ 100 INTEGER rows per page; over-provision a bit.
    conn = sqlite3.connect(str(backup))
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE bulk (id INTEGER PRIMARY KEY, payload TEXT)")
    conn.execute("INSERT INTO settings VALUES ('energy_cost_per_kwh', '0.12')")
    rows_needed = backup_size_pages * 50
    conn.executemany(
        "INSERT INTO bulk (payload) VALUES (?)",
        [("x" * 80,) for _ in range(rows_needed)],
    )
    conn.commit()
    conn.close()

    src_conn = sqlite3.connect(str(backup))
    try:
        dst_conn = sqlite3.connect(str(live))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    reader.close()

    # Verify both tables round-tripped intact.
    conn = sqlite3.connect(str(live))
    try:
        energy = conn.execute("SELECT value FROM settings WHERE key = 'energy_cost_per_kwh'").fetchone()
        bulk_count = conn.execute("SELECT count(*) FROM bulk").fetchone()
    finally:
        conn.close()

    assert energy == ("0.12",), f"Expected '0.12', got {energy!r}"
    assert bulk_count == (rows_needed,), f"Bulk table size mismatch: expected {rows_needed} rows, got {bulk_count!r}"
