"""Shared test fixtures for BamBuddy backend tests."""

import asyncio
import atexit
import json
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# IMPORTANT: Set environment variables BEFORE any app imports
# This must happen before settings/config are loaded
os.environ["LOG_TO_FILE"] = "false"
os.environ["DEBUG"] = "false"

# Point the app's own engine at a throwaway database before anything reads
# DATABASE_URL.
#
# The fixtures below build their own SQLite engine, but that is not the only
# engine in play: `core/config.py` snapshots ``DATABASE_URL`` at import time and
# `core/database.py` builds a module-level ``engine`` / ``async_session`` from
# it. Any app code that opens its own session rather than receiving the fixture
# one therefore talks to whatever database the developer's `.env` names. The
# clearest example is ``run_with_retry`` (used by the print-completion path),
# whose sessions come from ``backend.app.core.database`` — so the widespread
# ``patch("backend.app.main.async_session")`` does not intercept them.
#
# Left alone that is not a hypothetical: on a plain checkout it means the suite
# writes to the developer's real SQLite file, and with a PostgreSQL `.env` it
# means a live install. A completion test calling ``on_print_complete(1, ...)``
# closed a queue item belonging to an actual running print that way.
_TEST_APP_DB_DIR = Path(tempfile.mkdtemp(prefix="bambuddy_test_appdb_"))
APP_DATABASE_URL = f"sqlite+aiosqlite:///{_TEST_APP_DB_DIR / 'app.db'}"

# Opt-in PostgreSQL run. TEST_POSTGRES_URL names a throwaway local server
# (postgresql+asyncpg://user:password@127.0.0.1:port/postgres). Each xdist
# worker gets two fresh databases there, under a prefix this file owns: one for
# the app's own engine, one for the fixtures, kept apart as they are on SQLite.
# Nothing else on that server is touched.
_PG_DB_PREFIX = "bambuddy_testpg_"
TEST_PG_URL: str | None = None
if os.environ.get("TEST_POSTGRES_URL"):
    from sqlalchemy.engine import make_url

    _pg_server = make_url(os.environ["TEST_POSTGRES_URL"])
    if _pg_server.drivername != "postgresql+asyncpg" or _pg_server.host not in ("127.0.0.1", "localhost"):
        raise RuntimeError("TEST_POSTGRES_URL must be a postgresql+asyncpg URL of a local throwaway server")
    _pg_worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    _pg_app_db = f"{_PG_DB_PREFIX}{_pg_worker}_app"
    _pg_test_db = f"{_PG_DB_PREFIX}{_pg_worker}_test"

    async def _recreate_pg_databases() -> None:
        import asyncpg

        conn = await asyncpg.connect(
            user=_pg_server.username,
            password=_pg_server.password,
            host=_pg_server.host,
            port=_pg_server.port or 5432,
            database=_pg_server.database or "postgres",
        )
        try:
            for name in (_pg_app_db, _pg_test_db):
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()

    asyncio.run(_recreate_pg_databases())
    APP_DATABASE_URL = _pg_server.set(database=_pg_app_db).render_as_string(hide_password=False)
    TEST_PG_URL = _pg_server.set(database=_pg_test_db).render_as_string(hide_password=False)

os.environ["DATABASE_URL"] = APP_DATABASE_URL


def _cleanup_test_app_db_dir():
    shutil.rmtree(_TEST_APP_DB_DIR, ignore_errors=True)


atexit.register(_cleanup_test_app_db_dir)


def _assert_disposable_database(url, source: str) -> None:
    """Abort the run unless *url* is the throwaway database created above.

    A guard rather than a comment because the failure it prevents is silent and
    destructive: the suite would appear to pass while having mutated real print
    history. Anything that reintroduces a real ``DATABASE_URL`` — an `.env` read
    later in the import order, a fixture rebuilding the engine — trips this
    instead of reaching the database.
    """
    database = str(getattr(url, "database", "") or "")
    if TEST_PG_URL is not None:
        if (
            str(getattr(url, "drivername", "")) == "postgresql+asyncpg"
            and getattr(url, "host", None) in ("127.0.0.1", "localhost")
            and database.startswith(_PG_DB_PREFIX)
        ):
            return
        raise RuntimeError(f"Refusing to run tests: {source} resolves to {url!r}, not a {_PG_DB_PREFIX}* database.")
    if not str(getattr(url, "drivername", "")).startswith("sqlite") or not database.startswith(str(_TEST_APP_DB_DIR)):
        raise RuntimeError(
            f"Refusing to run tests: {source} resolves to {url!r}, which is not the "
            f"disposable SQLite database under {_TEST_APP_DB_DIR}. Tests must never "
            f"open a session against a real Bambuddy database."
        )


from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

# Ensure settings use our env vars - import and override before database import
from backend.app.core.config import settings  # noqa: E402

settings.log_to_file = False
if settings.database_url != APP_DATABASE_URL:
    raise RuntimeError(
        f"Refusing to run tests: settings.database_url is {settings.database_url!r} "
        f"rather than the disposable test database. Something read DATABASE_URL "
        f"before conftest could override it."
    )

# Use a temp directory for plate calibration to avoid deleting real calibration files
_test_plate_cal_dir = Path(tempfile.mkdtemp(prefix="bambuddy_test_plate_cal_"))
settings.plate_calibration_dir = _test_plate_cal_dir


# Clean up temp directory when tests finish
def _cleanup_test_plate_cal_dir():
    if _test_plate_cal_dir.exists():
        shutil.rmtree(_test_plate_cal_dir, ignore_errors=True)


atexit.register(_cleanup_test_plate_cal_dir)

from backend.app.core.database import Base, engine as _app_engine  # noqa: E402

# The engine is built at import time from the URL above, so this catches the
# case where that override did not take effect for whatever reason.
_assert_disposable_database(_app_engine.url, "backend.app.core.database.engine")


if TEST_PG_URL is not None:
    # The app's own database gets the schema too, once per worker, as a real
    # install's has it: code that opens its own session (the backup export,
    # print completion) reads and writes it. A short-lived engine, so the app
    # engine's pool holds no connection from this event loop.
    async def _create_app_schema() -> None:
        from backend.app.core.database import _create_engine, load_all_models

        load_all_models()
        schema_engine = _create_engine(APP_DATABASE_URL)
        try:
            async with schema_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await schema_engine.dispose()

    asyncio.run(_create_app_schema())

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


def pytest_collection_modifyitems(config, items):
    """On PostgreSQL, skip the tests of SQLite-specific behaviour."""
    if TEST_PG_URL is None:
        return
    skip = pytest.mark.skip(reason="SQLite-specific behaviour; the suite runs on PostgreSQL")
    for item in items:
        if item.get_closest_marker("sqlite_only"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def mfa_encryption_isolation(monkeypatch, tmp_path):
    """Per-test isolation for MFA encryption state.

    - Sets ``DATA_DIR`` to an isolated tmp path so the auto-bootstrap can
      never write ``.mfa_encryption_key`` into the repo or share state
      across tests / xdist workers.
    - Removes any inherited ``MFA_ENCRYPTION_KEY`` env var.
    - With ``DATA_DIR`` pointing at a writable ``tmp_path``, the default
      bootstrap path on first ``_get_fernet()`` call is **auto-generation**
      (key_source='generated'), NOT plaintext fallback. Tests that need the
      plaintext fallback path must monkeypatch ``_load_or_generate_key`` to
      return ``(None, 'none')`` (or 'none_write_failed' / 'none_corrupted')
      explicitly — see ``test_plaintext_passthrough_without_key`` for an
      example.
    - Resets the ``encryption`` module-level singletons before AND after the
      test so reorder doesn't leak cached Fernet instances.

    Tests that want to exercise an active key should call
    ``monkeypatch.setenv("MFA_ENCRYPTION_KEY", valid_key)`` and
    ``enc_mod._fernet_instance = None`` inside the test body — the autouse
    fixture only sets defaults, it doesn't lock them in.
    """
    from backend.app.core import encryption as enc_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
    enc_mod._fernet_instance = None
    enc_mod._warn_shown = False
    enc_mod._key_source = None
    yield
    enc_mod._fernet_instance = None
    enc_mod._warn_shown = False
    enc_mod._key_source = None


@pytest.fixture(autouse=True)
def reset_printer_file_cache():
    """A fresh file-browser preview cache per test.

    Test databases start their printer ids at 1 again, so a preview kept by one
    test would be served to the next one's printer 1."""
    from backend.app.services.printer_file_cache import printer_file_cache

    printer_file_cache.clear()
    yield
    printer_file_cache.clear()


@pytest.fixture(autouse=True)
def reset_spoolman_location_sync_cache():
    """Drop the per-URL Spoolman location-sync TTL cache between tests.

    Without this, a test that runs the sync against `http://localhost:7912`
    will skip the sync in any later test that uses the same URL within 60
    real seconds — test ordering would then leak assertions across runs."""
    from backend.app.services.location_service import _spoolman_location_sync_cache_clear

    _spoolman_location_sync_cache_clear()
    yield
    _spoolman_location_sync_cache_clear()


@pytest.fixture(autouse=True)
def reset_slot_unlink_grace():
    """Drop held slot unlinks and cancel their re-checks between tests (#3186).

    The holds live in a module-level dict keyed by printer id, and every test
    database hands out the same ids -- a hold left by one test would let the
    next one's first blank report count as already confirmed."""
    from backend.app.services import slot_unlink_grace

    slot_unlink_grace.reset()
    yield
    slot_unlink_grace.reset()


@pytest.fixture(autouse=True)
def reset_usage_tracker_sessions():
    """Drop the usage tracker's in-flight print sessions between tests.

    They live in a module-level dict keyed by printer id. Any test that runs
    ``main.on_print_start(1, ...)`` against a mocked printer manager leaves a
    session for printer 1 whose fields are MagicMocks, and a later test on the
    same worker that completes a print for printer 1 picks it up as its own."""
    from backend.app.services.usage_tracker import _active_sessions

    _active_sessions.clear()
    yield
    _active_sessions.clear()


@pytest.fixture(autouse=True)
def reset_auth_enabled_cache():
    """Drop the module-level auth-enabled cache between tests (issue #2572).

    ``is_auth_enabled`` caches an enabled=True result for a TTL. Without this
    reset a test that enables auth would leave ``True`` cached, so a later test
    running in auth-disabled mode (without going through ``set_auth_enabled``)
    would wrongly see auth as enabled until the TTL expired — order-dependent
    flakiness."""
    from backend.app.core.auth import invalidate_auth_enabled_cache

    invalidate_auth_enabled_cache()
    yield
    invalidate_auth_enabled_cache()


@pytest.fixture(autouse=True)
def disconnect_printers_registered_during_a_test():
    """Give every test an empty ``printer_manager`` singleton.

    ``POST /api/v1/printers`` really calls ``connect_printer``, so a test that
    creates a printer through the API parks a live client in the singleton --
    and the singleton outlives the per-test in-memory database. The next test
    on the same xdist worker gets a fresh database whose first printer is handed
    the same primary key, and reads that leftover client as its own live status.
    ``test_scheduled_drying_routes`` saw exactly that: an "online" printer with
    no firmware version, so scheduling a dry came back 400 instead of 200.

    Snapshotting the ids at test entry was insufficient: a client leaked by a
    previous module became part of that snapshot and therefore survived every
    later cleanup on the same xdist worker. Clear both before and after each
    test. ``disconnect_printer`` also clears model/printer-info caches and stops
    any paho thread owned by the leaked client.
    """
    from backend.app.services.printer_manager import printer_manager

    for printer_id in list(printer_manager._clients):
        printer_manager.disconnect_printer(printer_id)
    yield
    for printer_id in list(printer_manager._clients):
        printer_manager.disconnect_printer(printer_id)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    # Dispose the module-level engine so aiosqlite worker threads finish
    # before the event loop closes, preventing "Event loop is closed" errors.
    from backend.app.core.database import engine

    loop.run_until_complete(engine.dispose())
    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()


# The test database's schema as built, per worker: built once and emptied
# between tests. Rebuilding it for every test (DROP SCHEMA + create_all, a few
# hundred DDL statements) was 90% of a PostgreSQL run, with 30 workers and the
# server competing for the same cores.
_pg_schema: dict[str, object] = {}

# Everything a test could change about the schema: relations (tables, indexes,
# sequences, views) with their columns, types, nullability and defaults, and
# every constraint.
_PG_SCHEMA_FINGERPRINT = """
SELECT md5(coalesce(string_agg(item, ',' ORDER BY item), '')) FROM (
    SELECT c.relname || ':' || c.relkind::text || ':' || coalesce(a.attname, '') || ':'
        || coalesce(format_type(a.atttypid, a.atttypmod), '') || ':' || coalesce(a.attnotnull::text, '')
        || ':' || coalesce(pg_get_expr(d.adbin, d.adrelid), '') AS item
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE n.nspname = 'public'
    UNION ALL
    SELECT 'constraint:' || conname || ':' || pg_get_constraintdef(oid)
    FROM pg_constraint WHERE connamespace = 'public'::regnamespace
) AS schema_items
"""


# Every row out and every id back to 1. DELETE rather than TRUNCATE: TRUNCATE
# gives each table and index a new file, about three times the cost for the few
# rows a test leaves. Foreign-key triggers are off for this transaction only
# (the throwaway server's superuser), so the order does not matter.
_PG_EMPTY_TABLES = """
DO $$
DECLARE r record;
BEGIN
    PERFORM set_config('session_replication_role', 'replica', true);
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename LOOP
        EXECUTE format('DELETE FROM public.%I', r.tablename);
    END LOOP;
    FOR r IN SELECT c.oid::regclass AS seq FROM pg_class c
             WHERE c.relkind = 'S' AND c.relnamespace = 'public'::regnamespace LOOP
        PERFORM setval(r.seq, 1, false);
    END LOOP;
END $$
"""


async def _reset_pg_test_schema(conn) -> None:
    """An empty test database with the models' schema, as cheaply as possible.

    Emptied (ids restart at 1) when the schema is still the one built for this
    worker; a full rebuild the first time, after a test changed the schema (a
    migration test adding a column), and when a model loaded since adds a table.
    """
    from sqlalchemy import text

    from backend.app.core.database import load_all_models

    load_all_models()
    tables = set(Base.metadata.tables)
    fingerprint = (await conn.execute(text(_PG_SCHEMA_FINGERPRINT))).scalar_one()
    if _pg_schema.get("fingerprint") != fingerprint or _pg_schema.get("tables") != tables:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
        _pg_schema["fingerprint"] = (await conn.execute(text(_PG_SCHEMA_FINGERPRINT))).scalar_one()
        _pg_schema["tables"] = tables
        return
    await conn.execute(text(_PG_EMPTY_TABLES))


async def _finish_query_cancellations() -> None:
    """Wait for asyncpg to finish cancelling queries in this test's loop.

    A test that cancels a task mid-query (to stop the background work a
    handler started) awaits the task, but asyncpg cancels the query on the
    server in a task of its own. If the test ends first, that task is still
    pending when the loop closes and is reported as an unraisable
    "Connection._cancel" coroutine in some later test.
    """
    current = asyncio.current_task()
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not current
        and not task.done()
        and getattr(task.get_coro(), "__qualname__", "") == "Connection._cancel"
    ]
    if pending:
        await asyncio.wait(pending, timeout=5)


async def _stop_background_tasks() -> None:
    """Cancel the background tasks this test's code spawned, and wait for them.

    A task still in a query when the loop closes gets its answer from
    aiosqlite's worker thread into the closed loop ("Event loop is closed"),
    reported as a thread exception in whatever test runs next on the worker.
    Cancelled here, its session closes while the loop still runs.
    """
    from backend.app.core.tasks import _background_tasks

    loop = asyncio.get_running_loop()
    pending = [task for task in _background_tasks if not task.done() and task.get_loop() is loop]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=5)


async def _release_app_engine() -> None:
    """Close the app engine's pooled connections before this test's loop ends.

    Code that opens its own session (print completion, services) uses the
    app's module-level engine, shared by every test on the worker. Its pool
    would keep a connection opened in this test's event loop into the next
    test's, where SQLAlchemy closes it in a loop it does not belong to; on
    PostgreSQL that leaves asyncpg's cleanup unawaited, reported as an
    unraisable "Connection._cancel" coroutine in some later test.
    """
    # First: a task still holding a connection would return it to the pool
    # after the dispose below has replaced that pool, and it is never closed.
    await _stop_background_tasks()
    await _finish_query_cancellations()
    await _app_engine.dispose()


@pytest.fixture(autouse=True)
async def _app_engine_released_after_each_test():
    """Every test, not only those with test_engine: any of them can reach
    the app engine through code that opens its own session.

    On SQLite too: an aiosqlite connection belongs to the loop it was opened
    in, so one kept in the pool answers the next test's query into a closed
    loop ("Event loop is closed" from aiosqlite's worker thread).
    """
    yield
    await _release_app_engine()


@pytest.fixture
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Every model, as the app's schema has them: a hand-kept list here fell
    # behind and left four tables out of the SQLite test database.
    from backend.app.core.database import load_all_models

    load_all_models()

    if TEST_PG_URL is not None:
        # The same engine setup as the app's (UTC session, aware datetimes
        # made naive), on this worker's own database, emptied for each test.
        from backend.app.core.database import _create_engine

        await engine.dispose()
        engine = _create_engine(TEST_PG_URL)
        async with engine.begin() as conn:
            await _reset_pg_test_schema(conn)
        yield engine
        await _release_app_engine()
        await engine.dispose()
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await _release_app_engine()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    # Allow aiosqlite's background thread to finish processing the close
    # response before the per-function event loop shuts down, preventing
    # "RuntimeError: Event loop is closed" in call_soon_threadsafe.
    await asyncio.sleep(0.1)


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_maker() as session:
        yield session


@pytest.fixture
async def async_client(test_engine, db_session) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    from backend.app.core.database import async_session, get_db
    from backend.app.main import app

    # Create a new session maker for the test engine
    test_async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        # Mirror production get_db (core/database.py): commit on success,
        # rollback on error. Endpoints that rely on the request-scoped
        # implicit commit (e.g. create_project, which only flushes) would
        # otherwise silently lose their writes in tests (#1897).
        async with test_async_session() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # Mock init_printer_connections to prevent MQTT connection attempts during tests
    async def mock_init_printer_connections(db):
        pass  # No-op - don't connect to real printers

    # Also patch the module-level async_session used by services, auth, and middleware
    with (
        patch("backend.app.core.database.async_session", test_async_session),
        patch("backend.app.core.auth.async_session", test_async_session),
        patch("backend.app.main.async_session", test_async_session),
        # Obico endpoints load settings through the service's module-level binding;
        # without this patch they'd read whatever DB the cwd resolves to (#1546).
        patch("backend.app.services.obico_detection.async_session", test_async_session),
        patch("backend.app.main.init_printer_connections", mock_init_printer_connections),
    ):
        # Seed default groups for tests that need them
        from backend.app.core.database import seed_default_groups

        await seed_default_groups()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

        # The app lifespan called init_db() which used the module-level engine
        # (not the test engine), creating aiosqlite connections. Close those,
        # and stop background work still using them, before the loop closes.
        await _release_app_engine()

    app.dependency_overrides.clear()


# ============================================================================
# Mock External Services
# ============================================================================


@pytest.fixture
def mock_tasmota_service():
    """Mock the Tasmota service for smart plug tests."""
    # Patch both the module where it's defined and where it's imported
    with (
        patch("backend.app.services.tasmota.tasmota_service") as mock,
        patch("backend.app.api.routes.smart_plugs.tasmota_service") as mock2,
    ):
        mock.turn_on = AsyncMock(return_value=True)
        mock.turn_off = AsyncMock(return_value=True)
        mock.toggle = AsyncMock(return_value=True)
        mock.get_status = AsyncMock(return_value={"state": "ON", "reachable": True, "device_name": "Test Plug"})
        mock.get_energy = AsyncMock(
            return_value={
                "power": 150.5,
                "voltage": 120.0,
                "current": 1.25,
                "today": 2.5,
                "total": 100.0,
                "factor": 0.95,
            }
        )
        mock.test_connection = AsyncMock(return_value={"success": True, "state": "ON", "device_name": "Test Plug"})
        # Copy mocks to second patch target
        mock2.turn_on = mock.turn_on
        mock2.turn_off = mock.turn_off
        mock2.toggle = mock.toggle
        mock2.get_status = mock.get_status
        mock2.get_energy = mock.get_energy
        mock2.test_connection = mock.test_connection
        yield mock


@pytest.fixture
def mock_homeassistant_service():
    """Mock the Home Assistant service for smart plug tests."""
    # Patch both the module where it's defined and where it's imported
    with (
        patch("backend.app.services.homeassistant.homeassistant_service") as mock,
        patch("backend.app.api.routes.smart_plugs.homeassistant_service") as mock2,
    ):
        mock.turn_on = AsyncMock(return_value=True)
        mock.turn_off = AsyncMock(return_value=True)
        mock.toggle = AsyncMock(return_value=True)
        mock.get_status = AsyncMock(return_value={"state": "ON", "reachable": True, "device_name": "Test HA Entity"})
        mock.get_energy = AsyncMock(return_value=None)  # Most HA entities don't have power monitoring
        mock.test_connection = AsyncMock(return_value={"success": True, "message": "API running", "error": None})
        mock.list_entities = AsyncMock(
            return_value=[
                {
                    "entity_id": "switch.printer_plug",
                    "friendly_name": "Printer Plug",
                    "state": "on",
                    "domain": "switch",
                },
                {"entity_id": "switch.test", "friendly_name": "Test Switch", "state": "off", "domain": "switch"},
            ]
        )
        mock.configure = MagicMock()
        # Copy mocks to second patch target
        mock2.turn_on = mock.turn_on
        mock2.turn_off = mock.turn_off
        mock2.toggle = mock.toggle
        mock2.get_status = mock.get_status
        mock2.get_energy = mock.get_energy
        mock2.test_connection = mock.test_connection
        mock2.list_entities = mock.list_entities
        mock2.configure = mock.configure
        yield mock


@pytest.fixture
def mock_mqtt_client():
    """Mock the MQTT client for printer communication tests."""
    with patch("backend.app.services.bambu_mqtt.BambuMQTTClient") as mock:
        instance = MagicMock()
        instance.state = MagicMock(connected=True, state="IDLE", progress=0, temperatures={"nozzle": 25, "bed": 25})
        instance.connect = MagicMock()
        instance.disconnect = MagicMock()
        mock.return_value = instance
        yield mock


@pytest.fixture
def mock_mqtt_smart_plug_service():
    """Mock the MQTT smart plug service for MQTT plug tests."""
    with patch("backend.app.api.routes.smart_plugs.mqtt_relay") as mock:
        # Create a mock smart_plug_service
        mock_service = MagicMock()
        mock_service.is_configured = MagicMock(return_value=True)
        mock_service.configure = AsyncMock(return_value=True)
        mock_service.subscribe = MagicMock()
        mock_service.unsubscribe = MagicMock()
        mock_service.get_plug_data = MagicMock(return_value=None)
        mock_service.is_reachable = MagicMock(return_value=False)

        mock.smart_plug_service = mock_service
        yield mock


@pytest.fixture
def mock_ftp_client():
    """Mock the FTP client for file transfer tests."""
    with (
        patch("backend.app.services.bambu_ftp.download_file_async") as download_mock,
        patch("backend.app.services.bambu_ftp.list_files_async") as list_mock,
    ):
        download_mock.return_value = True
        list_mock.return_value = []
        yield {"download": download_mock, "list": list_mock}


@pytest.fixture
def mock_httpx_client():
    """Mock httpx for webhook/notification HTTP calls."""
    with patch("httpx.AsyncClient") as mock_class:
        mock_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        mock_response.json.return_value = {}

        mock_instance.get = AsyncMock(return_value=mock_response)
        mock_instance.post = AsyncMock(return_value=mock_response)
        # ntfy switches to PUT when a photo is attached (e.g. the sample image on Test).
        mock_instance.put = AsyncMock(return_value=mock_response)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock()

        mock_class.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_printer_manager():
    """Mock the printer manager for status checks."""
    with patch("backend.app.services.printer_manager.printer_manager") as mock:
        mock.get_status = MagicMock(
            return_value=MagicMock(
                connected=True,
                state="IDLE",
                progress=0,
                temperatures={"nozzle": 25, "bed": 25, "chamber": 25},
                raw_data={},
            )
        )
        mock.mark_printer_offline = MagicMock()
        yield mock


# ============================================================================
# Factory Fixtures for Test Data
# ============================================================================


@pytest.fixture
def smart_plug_factory(db_session):
    """Factory to create test smart plugs."""

    async def _create_plug(**kwargs):
        from backend.app.models.smart_plug import SmartPlug

        # Determine defaults based on plug_type
        plug_type = kwargs.get("plug_type", "tasmota")

        defaults = {
            "name": "Test Plug",
            "plug_type": plug_type,
            "enabled": True,
            "auto_on": True,
            "auto_off": True,
            "off_delay_mode": "time",
            "off_delay_minutes": 5,
            "off_temp_threshold": 70,
            "schedule_enabled": False,
            "power_alert_enabled": False,
        }

        # Set required fields based on plug_type
        if plug_type == "homeassistant":
            defaults["ha_entity_id"] = "switch.test"
            defaults["ip_address"] = None
        elif plug_type == "mqtt":
            # Legacy fields (for backward compatibility tests)
            defaults["mqtt_topic"] = kwargs.get("mqtt_topic", "test/topic")
            defaults["mqtt_multiplier"] = kwargs.get("mqtt_multiplier", 1.0)
            # New separate topic/path/multiplier fields
            defaults["mqtt_power_topic"] = kwargs.get("mqtt_power_topic")
            defaults["mqtt_power_path"] = kwargs.get("mqtt_power_path", "power")
            defaults["mqtt_power_multiplier"] = kwargs.get("mqtt_power_multiplier", 1.0)
            defaults["mqtt_energy_topic"] = kwargs.get("mqtt_energy_topic")
            defaults["mqtt_energy_path"] = kwargs.get("mqtt_energy_path")
            defaults["mqtt_energy_multiplier"] = kwargs.get("mqtt_energy_multiplier", 1.0)
            defaults["mqtt_state_topic"] = kwargs.get("mqtt_state_topic")
            defaults["mqtt_state_path"] = kwargs.get("mqtt_state_path")
            defaults["mqtt_state_on_value"] = kwargs.get("mqtt_state_on_value")
            defaults["ip_address"] = None
            defaults["ha_entity_id"] = None
        elif plug_type == "rest":
            defaults["rest_on_url"] = kwargs.get("rest_on_url", "http://192.168.1.100/api/plug/on")
            defaults["rest_off_url"] = kwargs.get("rest_off_url", "http://192.168.1.100/api/plug/off")
            defaults["rest_method"] = kwargs.get("rest_method", "POST")
            defaults["ip_address"] = None
            defaults["ha_entity_id"] = None
        else:
            defaults["ip_address"] = "192.168.1.100"
            defaults["ha_entity_id"] = None

        defaults.update(kwargs)

        plug = SmartPlug(**defaults)
        db_session.add(plug)
        await db_session.commit()
        await db_session.refresh(plug)
        return plug

    return _create_plug


@pytest.fixture
async def printer_1(db_session):
    """A printer with id 1, for tests whose rows refer to printer 1 directly.

    Without it those rows point at nothing, which only SQLite (foreign keys
    off) accepts.
    """
    from sqlalchemy import text

    from backend.app.models.printer import Printer

    printer = Printer(
        id=1, name="Printer 1", serial_number="00M09A900000001", ip_address="192.168.1.250", access_code="12345678"
    )
    db_session.add(printer)
    await db_session.flush()
    if TEST_PG_URL is not None:
        # An explicit id does not move the sequence on.
        await db_session.execute(text("SELECT setval(pg_get_serial_sequence('printers', 'id'), 1)"))
    await db_session.commit()
    return printer


@pytest.fixture
def printer_factory(db_session):
    """Factory to create test printers."""
    _counter = [0]  # Use list to allow mutation in nested function

    async def _create_printer(**kwargs):
        from backend.app.models.printer import Printer

        _counter[0] += 1
        counter = _counter[0]

        defaults = {
            "name": "Test Printer",
            "serial_number": f"00M09A{counter:09d}",  # Unique serial per printer
            "ip_address": f"192.168.1.{100 + counter}",  # Unique IP per printer
            "access_code": "12345678",
            "is_active": True,
            "auto_archive": True,
            "model": "X1C",
        }
        defaults.update(kwargs)

        printer = Printer(**defaults)
        db_session.add(printer)
        await db_session.commit()
        await db_session.refresh(printer)
        return printer

    return _create_printer


@pytest.fixture
def location_factory(db_session):
    _counter = [0]

    async def _create_location(**kwargs):
        from backend.app.models.location import Location

        _counter[0] += 1
        counter = _counter[0]

        name = kwargs.pop("name", f"Test Location {counter}")
        defaults = {
            "name": name,
            "name_key": name.strip().lower(),
        }
        defaults.update(kwargs)

        location = Location(**defaults)
        db_session.add(location)
        await db_session.commit()
        await db_session.refresh(location)
        return location

    return _create_location


@pytest.fixture
def notification_provider_factory(db_session):
    """Factory to create test notification providers."""

    async def _create_provider(**kwargs):
        from backend.app.models.notification import NotificationProvider

        config = kwargs.pop("config", {"server": "https://ntfy.sh", "topic": "test-topic"})
        if isinstance(config, dict):
            config = json.dumps(config)

        defaults = {
            "name": "Test Provider",
            "provider_type": "ntfy",
            "enabled": True,
            "config": config,
            "on_print_start": True,
            "on_print_complete": True,
            "on_print_failed": True,
            "on_print_stopped": True,
            "on_print_progress": False,
            "on_print_missing_spool_assignment": False,
            "on_billing_charge_failed": True,
            "on_printer_offline": False,
            "on_printer_error": False,
            "on_filament_low": False,
            "on_maintenance_due": False,
            "on_ams_humidity_high": False,
            "on_ams_temperature_high": False,
            "on_bed_cooled": False,
            "quiet_hours_enabled": False,
            "daily_digest_enabled": False,
        }
        defaults.update(kwargs)

        provider = NotificationProvider(**defaults)
        db_session.add(provider)
        await db_session.commit()
        await db_session.refresh(provider)
        return provider

    return _create_provider


@pytest.fixture
def archive_factory(db_session):
    """Factory to create test archives.

    Also synthesizes one PrintLogEntry per archive (matching the production
    flow where statistics are aggregated from PrintLogEntry, not PrintArchive,
    per #1378). Pass ``with_run=False`` to skip — useful for testing the
    "archived but never printed" state. Pass ``run_status=...`` to override
    the run's status independently of the archive's status field.
    """

    async def _create_archive(printer_id: int, **kwargs):
        from backend.app.models.archive import PrintArchive
        from backend.app.models.print_log import PrintLogEntry

        with_run = kwargs.pop("with_run", True)
        run_status = kwargs.pop("run_status", None)

        defaults = {
            "printer_id": printer_id,
            "filename": "test_print.gcode.3mf",
            "print_name": "Test Print",
            "file_path": "archives/test/test_print.gcode.3mf",
            "file_size": 1024000,
            "status": "completed",
            "filament_type": "PLA",
            "filament_used_grams": 50.0,
            "print_time_seconds": 3600,
        }
        defaults.update(kwargs)

        archive = PrintArchive(**defaults)
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        if with_run:
            duration = None
            if archive.started_at and archive.completed_at:
                duration = int((archive.completed_at - archive.started_at).total_seconds()) or None
            run = PrintLogEntry(
                archive_id=archive.id,
                printer_id=archive.printer_id,
                status=run_status or archive.status,
                started_at=archive.started_at,
                completed_at=archive.completed_at,
                duration_seconds=duration,
                filament_type=archive.filament_type,
                filament_color=archive.filament_color,
                filament_used_grams=archive.filament_used_grams,
                cost=archive.cost,
                energy_kwh=archive.energy_kwh,
                energy_cost=archive.energy_cost,
                failure_reason=archive.failure_reason,
                print_name=archive.print_name,
                created_by_id=archive.created_by_id,
                # Sync the event's created_at with the archive's so date-range
                # filtered tests that backdate an archive still find its event.
                created_at=archive.created_at,
            )
            db_session.add(run)
            await db_session.commit()

        return archive

    return _create_archive


# ============================================================================
# Sample Data Fixtures
# ============================================================================


@pytest.fixture
def sample_mqtt_print_start():
    """Sample MQTT message for print start."""
    return {
        "print": {
            "command": "project_file",
            "param": "/sdcard/test.gcode.3mf",
            "subtask_name": "test_print",
            "gcode_state": "RUNNING",
            "mc_percent": 0,
        }
    }


@pytest.fixture
def sample_mqtt_print_complete():
    """Sample MQTT message for print complete."""
    return {
        "print": {
            "gcode_state": "FINISH",
            "mc_percent": 100,
            "subtask_name": "test_print",
        }
    }


@pytest.fixture
def sample_printer_status():
    """Sample printer status data."""
    return {
        "connected": True,
        "state": "IDLE",
        "progress": 0,
        "layer_num": 0,
        "total_layers": 0,
        "temperatures": {
            "nozzle": 25.0,
            "bed": 25.0,
            "chamber": 25.0,
        },
        "remaining_time": 0,
        "filename": None,
    }


# ============================================================================
# Log Capture Fixtures for Error Detection
# ============================================================================


class LogCapture(logging.Handler):
    """Handler that captures log records for testing."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self.records.append(record)

    def clear(self):
        self.records.clear()

    def get_errors(self) -> list[logging.LogRecord]:
        """Get all ERROR and CRITICAL level records."""
        return [r for r in self.records if r.levelno >= logging.ERROR]

    def get_warnings(self) -> list[logging.LogRecord]:
        """Get all WARNING level records."""
        return [r for r in self.records if r.levelno == logging.WARNING]

    def has_errors(self) -> bool:
        """Check if any errors were logged."""
        return len(self.get_errors()) > 0

    def format_errors(self) -> str:
        """Format all errors as a string for assertion messages."""
        errors = self.get_errors()
        if not errors:
            return "No errors"
        formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
        return "\n".join(formatter.format(r) for r in errors)


@pytest.fixture
def capture_logs():
    """Fixture that captures log output during a test.

    Usage:
        def test_something(capture_logs):
            # Do something that might log errors
            some_function()

            # Check no errors were logged
            assert not capture_logs.has_errors(), capture_logs.format_errors()
    """
    handler = LogCapture()
    handler.setLevel(logging.DEBUG)

    # Attach to root logger to capture all logs
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    yield handler

    root_logger.removeHandler(handler)


@pytest.fixture
def assert_no_log_errors(capture_logs):
    """Fixture that automatically asserts no errors were logged.

    Usage:
        def test_something(assert_no_log_errors):
            # If any ERROR logs occur during this test, it will fail
            some_function()
    """
    yield capture_logs

    errors = capture_logs.get_errors()
    if errors:
        pytest.fail(f"Unexpected log errors:\n{capture_logs.format_errors()}")
