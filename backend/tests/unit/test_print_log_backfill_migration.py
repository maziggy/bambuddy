"""Regression test for the PrintLogEntry → PrintArchive backfill migration (#1390).

Reporter IndividualGhost1905 upgraded to 0.2.4.1 (which shipped the per-event
aggregation rewrite from #1378) and saw Quick Stats partially break on old
data:

  - Total Filament Cost = 0 (PrintLogEntry.cost was NULL on pre-upgrade rows)
  - Time Accuracy empty for pre-upgrade runs (the new query JOINs on
    archive_id, which the column-add migration left NULL)

#1378's migration added the columns but didn't backfill anything. This test
pins the backfill that the same `run_migrations` pass now performs:

  Step 1: link old log entries to their archive via print_name + printer_id.
  Step 2: copy archive.cost / energy_kwh / energy_cost onto the latest
          matching log entry per archive (so the sum across archives
          reproduces the pre-fix total exactly — pre-#1378, archive.cost
          held the LATEST run's value because reprints overwrote it).

Earlier reprints stay with cost = NULL — matching #1378's "first/latest run
writes, the rest stay NULL" convention for new prints, so reruns don't
double-count.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch in run_migrations regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_all_models():
    """Import every model so Base.metadata knows the full schema."""
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
async def engine_with_legacy_data():
    """Fresh schema + a legacy-shape dataset: two archives, four PrintLogEntry
    rows. The cube.3mf archive carries cost+energy (the user's reprinted file);
    gear.3mf has neither set. Three matching log entries simulate cube's
    reprint history (status: failed → completed → completed). All log entries
    start with archive_id and cost = NULL, exactly like the column-add
    migration leaves on a pre-#1378 install."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.core.database import Base
    from backend.app.models.archive import PrintArchive

    _register_all_models()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        session.add(
            PrintArchive(
                id=1,
                filename="cube.3mf",
                file_path="/x/cube.3mf",
                file_size=100,
                print_name="cube.3mf",
                printer_id=1,
                cost=4.25,
                energy_kwh=0.42,
                energy_cost=0.063,
                status="completed",
            )
        )
        session.add(
            PrintArchive(
                id=2,
                filename="gear.3mf",
                file_path="/x/gear.3mf",
                file_size=100,
                print_name="gear.3mf",
                printer_id=1,
                status="completed",
            )
        )
        await session.commit()

    async with engine.begin() as conn:
        # Three log entries for cube.3mf (two early reprints + a latest run),
        # one for gear.3mf. All with archive_id and cost NULL — exactly the
        # state the column-add migration leaves on pre-#1378 installs.
        base = datetime.now(timezone.utc) - timedelta(days=10)
        for i, (delta_days, status, print_name) in enumerate(
            [
                (0, "failed", "cube.3mf"),
                (1, "completed", "cube.3mf"),
                (2, "completed", "cube.3mf"),  # latest run for cube — must receive backfill
                (3, "completed", "gear.3mf"),
            ],
            start=1,
        ):
            ts = (base + timedelta(days=delta_days)).isoformat()
            await conn.execute(
                text("""
                    INSERT INTO print_log_entries
                        (id, print_name, printer_id, status, started_at, completed_at,
                         duration_seconds, filament_used_grams, created_at)
                    VALUES (:id, :pn, 1, :status, :ts, :ts, 3600, 25.0, :ts)
                """),
                {"id": i, "pn": print_name, "status": status, "ts": ts},
            )

        # Force NULL on the columns we want the migration to touch — the
        # CREATE TABLE from Base.metadata.create_all already left them NULL,
        # but we set explicitly so the fixture's intent is loud.
        await conn.execute(
            text("UPDATE print_log_entries SET archive_id = NULL, cost = NULL, energy_kwh = NULL, energy_cost = NULL")
        )

    yield engine
    await engine.dispose()


async def test_backfill_links_log_entries_to_their_archive(engine_with_legacy_data):
    """All four entries should pick up archive_id after the migration runs."""
    async with engine_with_legacy_data.begin() as conn:
        await run_migrations(conn)

    async with engine_with_legacy_data.connect() as conn:
        result = await conn.execute(text("SELECT id, print_name, archive_id FROM print_log_entries ORDER BY id"))
        rows = result.all()

    assert rows == [
        (1, "cube.3mf", 1),
        (2, "cube.3mf", 1),
        (3, "cube.3mf", 1),
        (4, "gear.3mf", 2),
    ]


async def test_backfill_copies_cost_and_energy_to_latest_run_only(engine_with_legacy_data):
    """Pre-#1378 archive.cost = LAST run's value because reprints overwrote it.
    The backfill attributes that cost to the latest matching log entry; earlier
    runs stay NULL so summing across runs reproduces sum-of-archive-costs
    exactly — what the user saw before the upgrade."""
    async with engine_with_legacy_data.begin() as conn:
        await run_migrations(conn)

    async with engine_with_legacy_data.connect() as conn:
        result = await conn.execute(text("SELECT id, cost, energy_kwh, energy_cost FROM print_log_entries ORDER BY id"))
        rows = result.all()

    # Two earlier cube runs (id 1, 2): cost stays NULL.
    assert rows[0] == (1, None, None, None)
    assert rows[1] == (2, None, None, None)
    # Latest cube run (id 3): receives archive 1's cost / energy.
    assert rows[2] == (3, 4.25, 0.42, 0.063)
    # gear run (id 4): archive 2 has no cost/energy so log stays NULL too.
    assert rows[3] == (4, None, None, None)


async def test_backfill_is_idempotent(engine_with_legacy_data):
    """Running the migration twice produces the same state — no double-backfill,
    no values pulled off rows the second pass would mistakenly treat as 'new'."""
    async with engine_with_legacy_data.begin() as conn:
        await run_migrations(conn)
    async with engine_with_legacy_data.begin() as conn:
        await run_migrations(conn)

    async with engine_with_legacy_data.connect() as conn:
        result = await conn.execute(text("SELECT id, archive_id, cost FROM print_log_entries ORDER BY id"))
        rows = result.all()

    assert rows == [
        (1, 1, None),
        (2, 1, None),
        (3, 1, 4.25),
        (4, 2, None),
    ]


async def test_backfill_skips_archives_with_any_costed_run(engine_with_legacy_data):
    """If ANY log entry for an archive already has cost set — e.g. the post-#1378
    live write path filled it for a new run — the backfill leaves the entire
    archive alone. This is the migration's idempotency anchor: 'cost is
    accounted for somewhere on this archive's history' is the signal we use
    to decide whether to inject the archive-level value. Backfilling another
    row would double-count once the live writes start adding up."""
    async with engine_with_legacy_data.begin() as conn:
        # Pretend run #1 was written post-fix with its own cost.
        await conn.execute(text("UPDATE print_log_entries SET cost = 1.11 WHERE id = 1"))
        await run_migrations(conn)

    async with engine_with_legacy_data.connect() as conn:
        result = await conn.execute(text("SELECT id, cost FROM print_log_entries ORDER BY id"))
        rows = result.all()

    # Run #1 keeps its live-written cost. The archive already has a costed
    # run, so the migration does NOT inject archive.cost onto run #3.
    # gear.3mf (archive 2) still has nothing — but archive.cost is NULL
    # there too, so the backfill UPDATE would set NULL → NULL anyway, which
    # is the desired no-op.
    assert dict(rows) == {1: 1.11, 2: None, 3: None, 4: None}
