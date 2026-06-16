"""Regression test for the VP access-code sync migration.

Non-proxy VPs with a target printer must use the target's access code
because the live-mirror bridge forwards the slicer's MQTT/RTSPS auth
bytes through to the real printer. Earlier UIs let the codes diverge,
producing a VP whose listener accepted the bind but whose bridge then
failed at the second hop. The migration in ``run_migrations`` rewrites
mismatched rows on the next boot after upgrade.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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
    """run_migrations touches multiple tables; the full schema must exist."""
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
async def engine():
    from backend.app.core.database import Base

    _register_all_models()

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed_printer(engine, printer_id: int, name: str, access_code: str) -> None:
    """Insert a printer row through the ORM so Python-side defaults
    (nozzle_count, is_active, auto_archive, print_hours_offset, …) all apply
    without us having to mirror every NOT NULL column in raw SQL."""
    from backend.app.models.printer import Printer

    async with AsyncSession(engine) as session:
        session.add(
            Printer(
                id=printer_id,
                name=name,
                ip_address=f"192.168.1.{printer_id + 100}",
                access_code=access_code,
                serial_number=f"01P00A39180000{printer_id}",
                model="C12",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_non_proxy_vp_with_target_inherits_access_code(engine):
    """A non-proxy VP with a mismatched access_code gets corrected to match
    the target printer's code on the next boot."""
    await _seed_printer(engine, 1, "Real X1C", "REALCODE")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers "
                "(id, name, enabled, mode, access_code, target_printer_id, serial_suffix, position) "
                "VALUES (1, 'Queue VP', 0, 'queue', 'OLDVPCDE', 1, '391800001', 1)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        code = (await conn.execute(text("SELECT access_code FROM virtual_printers WHERE id = 1"))).scalar()
    assert code == "REALCODE"


@pytest.mark.asyncio
async def test_proxy_vp_access_code_is_left_alone(engine):
    """Proxy-mode VPs are NOT touched — the proxy already uses the target's
    code transparently at the protocol level, and the model column can
    legitimately hold an unused access_code value."""
    await _seed_printer(engine, 1, "Real X1C", "REALCODE")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers "
                "(id, name, enabled, mode, access_code, target_printer_id, serial_suffix, position) "
                "VALUES (1, 'Proxy VP', 0, 'proxy', 'PROXYCDE', 1, '391800001', 1)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        code = (await conn.execute(text("SELECT access_code FROM virtual_printers WHERE id = 1"))).scalar()
    assert code == "PROXYCDE"


@pytest.mark.asyncio
async def test_already_matching_vp_is_left_alone(engine):
    """A VP whose code already equals the target's needs no change.
    Confirms the WHERE clause excludes synced rows so re-running is a no-op."""
    await _seed_printer(engine, 1, "Real X1C", "MATCHED1")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers "
                "(id, name, enabled, mode, access_code, target_printer_id, serial_suffix, position) "
                "VALUES (1, 'Synced VP', 0, 'archive', 'MATCHED1', 1, '391800001', 1)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)
    # Re-run to prove idempotency.
    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        code = (await conn.execute(text("SELECT access_code FROM virtual_printers WHERE id = 1"))).scalar()
    assert code == "MATCHED1"


@pytest.mark.asyncio
async def test_non_proxy_vp_without_target_is_left_alone(engine):
    """No target = no bridge = nothing to derive from. The VP keeps its own code."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers "
                "(id, name, enabled, mode, access_code, target_printer_id, serial_suffix, position) "
                "VALUES (1, 'Standalone VP', 0, 'archive', 'STANDALN', NULL, '391800001', 1)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        code = (await conn.execute(text("SELECT access_code FROM virtual_printers WHERE id = 1"))).scalar()
    assert code == "STANDALN"


@pytest.mark.asyncio
async def test_null_vp_access_code_with_target_gets_populated(engine):
    """A VP with no access_code at all (NULL) but a target set is treated
    as a divergence — the migration populates it from the target."""
    await _seed_printer(engine, 1, "Real X1C", "FRESHCDE")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers "
                "(id, name, enabled, mode, access_code, target_printer_id, serial_suffix, position) "
                "VALUES (1, 'Fresh VP', 0, 'queue', NULL, 1, '391800001', 1)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        code = (await conn.execute(text("SELECT access_code FROM virtual_printers WHERE id = 1"))).scalar()
    assert code == "FRESHCDE"


@pytest.mark.asyncio
async def test_multi_vp_sync_one_run(engine):
    """Multiple mismatched VPs against different targets are all corrected
    in a single migration pass."""
    await _seed_printer(engine, 1, "Printer A", "AAAAAAAA")
    await _seed_printer(engine, 2, "Printer B", "BBBBBBBB")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers "
                "(id, name, enabled, mode, access_code, target_printer_id, serial_suffix, position) "
                "VALUES "
                "(1, 'VP-A', 0, 'archive', 'WRONGAAA', 1, '391800001', 1),"
                "(2, 'VP-B', 0, 'queue', 'WRONGBBB', 2, '391800002', 2)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT id, access_code FROM virtual_printers ORDER BY id"))
        rows = dict(result.fetchall())

    assert rows[1] == "AAAAAAAA"
    assert rows[2] == "BBBBBBBB"
