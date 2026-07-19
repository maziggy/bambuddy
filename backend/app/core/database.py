import asyncio
import logging

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.app.core.config import settings
from backend.app.core.db_dialect import is_sqlite

logger = logging.getLogger(__name__)


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Set SQLite pragmas on each new connection for concurrency and performance."""
    cursor = dbapi_conn.cursor()
    # WAL mode allows concurrent readers + one writer (vs default DELETE mode which locks entirely)
    cursor.execute("PRAGMA journal_mode = WAL")
    # Wait up to 15 seconds when the database is locked instead of failing immediately
    cursor.execute("PRAGMA busy_timeout = 15000")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()


# Resolved connection-pool configuration, captured at engine creation so
# /system/db-pool can report it without re-deriving the dialect defaults.
_pool_config: dict = {}


def _resolve_pool_kwargs() -> dict:
    """Build the pool kwargs for ``create_async_engine`` (issue #2572).

    Dialect-aware defaults, each overridable via env (``DB_POOL_SIZE`` etc.):
      - PostgreSQL: pool_size 20 + max_overflow 80, ``pool_pre_ping`` (recover
        server-dropped connections instead of erroring the request) and
        ``pool_recycle`` 1800s. The old hard-coded 10 + 20 exhausted on large
        farms while printer callbacks held connections.
      - SQLite: pool_size 20 + max_overflow 200 (unchanged); no pre-ping /
        recycle — the connection is a local file, not a server socket.
    """
    if is_sqlite():
        pool_size = settings.db_pool_size if settings.db_pool_size is not None else 20
        max_overflow = settings.db_max_overflow if settings.db_max_overflow is not None else 200
        kwargs = {"pool_size": pool_size, "max_overflow": max_overflow}
    else:
        pool_size = settings.db_pool_size if settings.db_pool_size is not None else 20
        max_overflow = settings.db_max_overflow if settings.db_max_overflow is not None else 80
        kwargs = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_pre_ping": True,
            "pool_recycle": settings.db_pool_recycle if settings.db_pool_recycle is not None else 1800,
            # LIFO checkout keeps a bursty farm on a small hot connection set and
            # lets overflow connections recycle out during quiet spells (#2572).
            "pool_use_lifo": settings.db_pool_use_lifo if settings.db_pool_use_lifo is not None else True,
        }
    if settings.db_pool_timeout is not None:
        kwargs["pool_timeout"] = settings.db_pool_timeout
    return kwargs


def _create_engine():
    """Create the async engine with dialect-appropriate settings."""
    kwargs = _resolve_pool_kwargs()

    global _pool_config
    _pool_config = {
        "pool_size": kwargs["pool_size"],
        "max_overflow": kwargs["max_overflow"],
        # SQLAlchemy's own defaults when we don't pass the kwarg.
        "pool_timeout": kwargs.get("pool_timeout", 30),
        "pool_recycle": kwargs.get("pool_recycle", -1),
        "pool_pre_ping": kwargs.get("pool_pre_ping", False),
        "pool_use_lifo": kwargs.get("pool_use_lifo", False),
    }

    eng = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        **kwargs,
    )
    if is_sqlite():
        event.listen(eng.sync_engine, "connect", _set_sqlite_pragmas)
    else:
        # Strip timezone info from aware datetimes before they reach asyncpg.
        # asyncpg rejects timezone-aware values for TIMESTAMP WITHOUT TIME ZONE columns.
        # The codebase uses datetime.now(timezone.utc) in many places — this makes
        # Postgres behave like SQLite which ignores timezone info entirely.
        @event.listens_for(eng.sync_engine, "before_cursor_execute", retval=True)
        def _strip_tz_from_params(conn, cursor, statement, parameters, context, executemany):
            import datetime

            if parameters is None:
                return statement, parameters

            # Recursive strip that walks any nesting of dict/list/tuple. Needed
            # because SQLAlchemy passes parameters in several shapes depending
            # on the path: a dict for named binds, a tuple for positional, a
            # list of dicts/tuples for executemany, and for insertmanyvalues
            # sometimes a list of tuples inside an outer list. The simplest
            # correct answer is "strip datetimes at any depth".
            def _strip(val):
                if isinstance(val, datetime.datetime) and val.tzinfo is not None:
                    return val.replace(tzinfo=None)
                if isinstance(val, dict):
                    return {k: _strip(v) for k, v in val.items()}
                if isinstance(val, list):
                    return [_strip(v) for v in val]
                if isinstance(val, tuple):
                    return tuple(_strip(v) for v in val)
                return val

            return statement, _strip(parameters)

    return eng


engine = _create_engine()

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def get_pool_status() -> dict:
    """Snapshot the DB connection pool for diagnostics (issue #2572).

    Returns the resolved configuration plus live gauges (checked-out /
    checked-in / overflow). Reads the pool's own counters — it does NOT
    check out a connection, so it stays truthful even when the pool is
    exhausted. Gauges a given pool implementation doesn't expose come back
    as ``None`` rather than raising.
    """
    pool = engine.sync_engine.pool
    gauges: dict = {}
    for key, method_name in (
        ("current_size", "size"),
        ("checked_out", "checkedout"),
        ("checked_in", "checkedin"),
        ("overflow", "overflow"),
    ):
        method = getattr(pool, method_name, None)
        try:
            gauges[key] = method() if callable(method) else None
        except Exception:
            # A gauge should never take down the diagnostics endpoint.
            gauges[key] = None
    return {
        "dialect": "sqlite" if is_sqlite() else "postgresql",
        "config": dict(_pool_config),
        **gauges,
    }


async def run_with_retry(fn, *, max_attempts: int = 3, label: str = ""):
    """Run an async DB operation with retry for SQLite 'database is locked' errors.

    ``fn`` is an async callable that receives an ``AsyncSession`` and performs
    the full query-mutate-commit cycle.  On each retry a fresh session is used
    so there are no stale-object / expired-attribute issues after rollback.

    On PostgreSQL this calls ``fn`` once with no retry (Postgres uses row-level
    locking and doesn't suffer from single-writer contention).
    """
    if not is_sqlite():
        async with async_session() as db:
            return await fn(db)

    last_exc: OperationalError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with async_session() as db:
                return await fn(db)
        except OperationalError as exc:
            last_exc = exc
            if "database is locked" not in str(exc) or attempt == max_attempts:
                raise
            delay = 0.5 * attempt  # 0.5s, 1.0s
            logger.warning(
                "SQLite locked%s (attempt %d/%d), retrying in %.1fs: %s",
                f" ({label})" if label else "",
                attempt,
                max_attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, but keeps type checkers happy


async def close_all_connections():
    """Close all database connections for backup/restore operations."""
    global engine
    await engine.dispose()


async def reinitialize_database():
    """Reinitialize database connection after restore."""
    global engine, async_session
    engine = _create_engine()
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            # Catch BaseException (not just Exception) so CancelledError —
            # raised when Starlette's BaseHTTPMiddleware cancels the inner
            # task scope on client disconnect — also triggers rollback.
            # `asyncio.shield` keeps the rollback running to completion
            # even when the await itself gets cancelled, so the SQLite
            # write lock is released promptly instead of being held until
            # the connection is GC'd ages later (which was producing the
            # "database is locked" cascade in #1112's support package).
            try:
                await asyncio.shield(session.rollback())
            except BaseException:  # noqa: BLE001 — rollback failure must not mask the original
                pass
            raise
        finally:
            try:
                await asyncio.shield(session.close())
            except BaseException:  # noqa: BLE001 — close failure must not mask the original
                pass


async def init_db():
    # Import models to register them with SQLAlchemy
    from backend.app.models import (  # noqa: F401
        active_print_spoolman,
        ams_history,
        ams_label,
        api_key,
        archive,
        auth_ephemeral,
        bug_report,
        color_catalog,
        external_link,
        filament,
        filament_sku_settings,
        github_backup,
        group,
        kprofile_note,
        library,
        local_preset,
        location,
        long_lived_token,
        maintenance,
        notification,
        notification_template,
        oidc_provider,
        orca_base_cache,
        pending_upload,
        pipeline_run,
        print_batch,
        print_log,
        print_queue,
        printer,
        printer_sensor_history,
        project,
        project_bom,
        settings,
        shopping_list,
        slicer_pipeline,
        slot_preset,
        smart_plug,
        smart_plug_energy_snapshot,
        spool,
        spool_assignment,
        spool_catalog,
        spool_code,
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        spoolman_k_profile,
        spoolman_slot_assignment,
        user,
        user_email_pref,
        user_otp_code,
        user_totp,
        virtual_printer,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Run migrations for new columns (SQLite doesn't auto-add columns)
        await run_migrations(conn)

    # Re-encrypt any legacy plaintext OIDC client_secret / TOTP secret rows
    # that exist from before the encryption key was configured.
    # Runs on a fresh AsyncSession (NOT the run_migrations() connection) so it
    # doesn't share a transaction with the schema-DDL block above — required to
    # avoid SQLite "database is locked" contention on the WAL writer.
    await _migrate_encrypt_legacy_secrets()

    # Seed default notification templates
    await seed_notification_templates()

    # Seed default groups and migrate existing users
    await seed_default_groups()

    # Seed default catalog entries
    await seed_spool_catalog()
    await seed_color_catalog()


# B2: Module-level counter exposing the number of rows skipped during the last
# _migrate_encrypt_legacy_secrets() invocation. Surfaced via /encryption-status
# (migration_error_count) so operators can spot poison rows that need attention.
_migration_error_count: int = 0


def get_migration_error_count() -> int:
    """Return the number of rows that failed to re-encrypt during the last
    _migrate_encrypt_legacy_secrets() run."""
    return _migration_error_count


async def _migrate_encrypt_legacy_secrets() -> None:
    """Re-encrypt OIDC ``client_secret`` and TOTP ``secret`` rows that are still
    stored as plaintext (no ``fernet:`` prefix).

    Called from :func:`init_db` after :func:`run_migrations` finishes. No-ops
    when no encryption key is configured (so plaintext storage stays the
    legacy behaviour for installs without a key).

    B2: per-row strategy — each row is committed in its own AsyncSession so a
    single corrupt row does NOT block other successful re-encryptions on every
    startup forever. The skipped-row count is exposed via
    :func:`get_migration_error_count` and surfaced on /encryption-status.

    B3: unexpected (non-row) failures during the read phase are re-raised so
    operators see the problem instead of silent data corruption — startup
    fails loudly rather than running with half-migrated rows.

    Idempotent: rows that already start with ``fernet:`` are skipped, and the
    write-phase re-checks the prefix before encrypting (guards against double
    encryption from concurrent workers).
    """
    from sqlalchemy import not_, select

    from backend.app.core.encryption import is_encryption_active
    from backend.app.models.oidc_provider import OIDCProvider
    from backend.app.models.user_totp import UserTOTP

    global _migration_error_count

    if not is_encryption_active():
        # Reset stale counter from a previous active-key run — we no longer
        # have any rows to migrate, so the count must not leak across runs.
        _migration_error_count = 0
        return

    # Phase 1 (read): collect (id, stored_value) tuples for plaintext rows.
    # Read phase failures are startup-fatal — re-raise (B3).
    try:
        async with async_session() as ro:
            oidc_rows = await ro.execute(
                select(OIDCProvider.id, OIDCProvider._client_secret_enc).where(
                    not_(OIDCProvider._client_secret_enc.like("fernet:%"))
                )
            )
            oidc_candidates = [(r[0], r[1]) for r in oidc_rows.all()]
            totp_rows = await ro.execute(
                select(UserTOTP.id, UserTOTP._secret_enc).where(not_(UserTOTP._secret_enc.like("fernet:%")))
            )
            totp_candidates = [(r[0], r[1]) for r in totp_rows.all()]
    except Exception:
        logger.error("_migrate_encrypt_legacy_secrets: phase 1 read failed", exc_info=True)
        raise  # B3

    oidc_count = totp_count = error_count = 0

    # Phase 2 (write): each row in its own AsyncSession + transaction.
    # Failure of one row does NOT block the others.
    for oidc_id, stored in oidc_candidates:
        if not stored:
            continue  # defensive: skip empty strings
        try:
            async with async_session() as wr:
                provider = await wr.get(OIDCProvider, oidc_id)
                if provider is None:
                    continue  # row deleted between phase 1 and phase 2
                # Idempotent guard: re-check inside the write session in case a
                # concurrent worker beat us to it.
                if not provider._client_secret_enc.startswith("fernet:"):
                    provider.client_secret = stored  # setter -> mfa_encrypt
                    await wr.commit()
                    oidc_count += 1
        except Exception:
            logger.error(
                "Failed to re-encrypt OIDCProvider id=%s — skipping",
                oidc_id,
                exc_info=True,
            )
            error_count += 1

    for totp_id, stored in totp_candidates:
        if not stored:
            continue
        try:
            async with async_session() as wr:
                totp = await wr.get(UserTOTP, totp_id)
                if totp is None:
                    continue
                if not totp._secret_enc.startswith("fernet:"):
                    totp.secret = stored
                    await wr.commit()
                    totp_count += 1
        except Exception:
            logger.error(
                "Failed to re-encrypt UserTOTP id=%s — skipping",
                totp_id,
                exc_info=True,
            )
            error_count += 1

    _migration_error_count = error_count
    if oidc_count or totp_count:
        logger.info(
            "Re-encrypted legacy plaintext secrets: %d OIDC client_secret(s), %d TOTP secret(s)",
            oidc_count,
            totp_count,
        )
    elif error_count == 0:
        logger.debug("_migrate_encrypt_legacy_secrets: no rows needed re-encryption")
    if error_count:
        logger.error(
            "_migrate_encrypt_legacy_secrets: %d row(s) skipped due to errors. "
            "See /api/v1/auth/encryption-status (migration_error_count).",
            error_count,
        )


async def _safe_execute(conn, sql):
    """Execute a DDL migration statement, silently ignoring idempotency errors.

    'already exists', 'duplicate column name' (SQLite ADD COLUMN), 'no such column'
    (SQLite RENAME COLUMN), 'duplicate key', and the compound
    'column … does not exist' (PostgreSQL RENAME COLUMN idempotency) are swallowed
    so that re-running DDL migrations is safe.  The compound check additionally
    requires the SQL to be a RENAME COLUMN statement so that "does not exist" errors
    from ADD COLUMN or CREATE INDEX (which would indicate schema corruption, not
    idempotency) are never silently swallowed.
    Any other error is logged and re-raised — callers must not assume silent
    recovery, as a failure will abort the migration sequence and prevent
    application startup.

    Only use for DDL statements (ALTER TABLE, CREATE INDEX, etc.).
    For DML backfills (UPDATE, DELETE) use conn.execute() directly inside
    async with conn.begin_nested() so failures are never silently swallowed.

    Uses a savepoint so that a failed statement doesn't poison the surrounding
    transaction (required for PostgreSQL).
    """
    from sqlalchemy import text

    try:
        async with conn.begin_nested():
            await conn.execute(text(sql))
    except (OperationalError, ProgrammingError) as exc:
        msg = str(exc).lower()
        # Only swallow "column … does not exist" for RENAME COLUMN — not for ADD COLUMN
        # or CREATE INDEX where it would indicate schema corruption, not idempotency.
        column_not_exists = "rename column" in sql.lower() and "column" in msg and "does not exist" in msg
        if (
            not any(k in msg for k in ("already exists", "duplicate key", "duplicate column name", "no such column"))
            and not column_not_exists
        ):
            logger.error("Migration statement failed: %s | SQL: %.200s", exc, sql)
            raise


async def _api_keys_column_exists(conn, column_name: str) -> bool:
    """Return True if the named column exists on ``api_keys``.

    Used to gate one-shot data backfills that must run only on the migration
    that adds a column — without this, repeating the UPDATE on every startup
    would silently overwrite values the user later edited in the UI.
    Dialect-specific because SQLite has no information_schema.
    """
    from sqlalchemy import text

    if is_sqlite():
        result = await conn.execute(text("PRAGMA table_info(api_keys)"))
        return any(row[1] == column_name for row in result)
    result = await conn.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = 'api_keys' AND column_name = :col"),
        {"col": column_name},
    )
    return result.scalar_one_or_none() is not None


async def _migrate_normalize_printer_ids(conn) -> None:
    from sqlalchemy import text

    async with conn.begin_nested():
        if is_sqlite():
            await conn.execute(text("UPDATE api_keys SET printer_ids = NULL WHERE printer_ids = '[]'"))
        else:
            await conn.execute(text("UPDATE api_keys SET printer_ids = NULL WHERE printer_ids::text = '[]'"))


async def _migrate_scope_force_color_overrides_to_plate(conn) -> None:
    """Re-scope queue items that carry another plate's filament overrides (#2551).

    Queueing several plates of one 3MF used to store the union of every selected
    plate's overrides on each item, so a ``force_color_match`` plate printing one
    colour sat at Waiting until a printer had the whole batch's palette loaded.
    The write paths now narrow to the plate, but items queued before the fix would
    stay stuck until the user deleted and re-added them by hand — with a waiting
    reason that gives no hint as to why. Repair them here instead.

    Only pending items are touched: a printing or finished item's overrides are a
    record of what it dispatched with, not an instruction. An item whose plate we
    cannot read keeps every override, per ``overrides_for_plate``. Idempotent —
    an already-scoped item narrows to itself and is not rewritten.
    """
    import json
    from pathlib import Path

    from sqlalchemy import text

    from backend.app.services.filament_requirements import overrides_for_plate

    rows = (
        await conn.execute(
            text(
                "SELECT q.id, q.plate_id, q.filament_overrides, "
                "a.file_path AS archive_path, l.file_path AS library_path "
                "FROM print_queue q "
                "LEFT JOIN print_archives a ON a.id = q.archive_id "
                "LEFT JOIN library_files l ON l.id = q.library_file_id "
                "WHERE q.status = 'pending' "
                "AND q.plate_id IS NOT NULL "
                "AND q.filament_overrides IS NOT NULL"
            )
        )
    ).fetchall()

    repaired = 0
    for row in rows:
        try:
            overrides = json.loads(row.filament_overrides)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(overrides, list) or not overrides:
            continue

        stored_path = row.archive_path or row.library_path
        if not stored_path:
            continue
        path = Path(stored_path)
        if not path.is_absolute():
            path = settings.base_dir / stored_path

        scoped = overrides_for_plate(overrides, path, row.plate_id)
        if len(scoped) == len(overrides):
            continue

        async with conn.begin_nested():
            await conn.execute(
                text("UPDATE print_queue SET filament_overrides = :overrides WHERE id = :id"),
                {"overrides": json.dumps(scoped) if scoped else None, "id": row.id},
            )
        repaired += 1

    if repaired:
        logger.info(
            "Re-scoped the filament overrides of %d queued item(s) to the plate they print (#2551)",
            repaired,
        )


async def _migrate_drop_library_print_name(conn) -> None:
    """Strip the embedded 3MF Title (``print_name``) from library file metadata (#1489).

    Library files stored the 3MF's ``<metadata name="Title">`` as
    ``file_metadata.print_name`` — generic ("Exported 3D Model") for Bambu
    Studio exports, a marketing title for MakerWorld downloads — and the
    FileManager wrongly preferred it over the filename for the card label,
    search and sort. New imports no longer store it; this clears it from rows
    imported before the fix so existing libraries don't need a rename
    round-trip. Idempotent — rows without the key are untouched.
    """
    from sqlalchemy import text

    async with conn.begin_nested():
        if is_sqlite():
            await conn.execute(
                text(
                    "UPDATE library_files SET file_metadata = json_remove(file_metadata, '$.print_name') "
                    "WHERE json_extract(file_metadata, '$.print_name') IS NOT NULL"
                )
            )
        else:
            # file_metadata is a JSON (not JSONB) column — cast to jsonb for the
            # key-exists test (jsonb_exists, avoiding the `?` operator which
            # clashes with driver parameter syntax) and the `- key` removal.
            await conn.execute(
                text(
                    "UPDATE library_files SET file_metadata = (file_metadata::jsonb - 'print_name')::json "
                    "WHERE jsonb_exists(file_metadata::jsonb, 'print_name')"
                )
            )


async def _migrate_update_auto_link_constraint(conn) -> None:
    """Update the auto_link CHECK constraint to allow Fall C (custom email claim).

    Old formula: auto_link = FALSE OR (require_ev = TRUE AND email_claim = 'email')
    New formula: auto_link = FALSE OR email_claim != 'email' OR require_ev = TRUE

    Only Fall B (email_claim='email' + require_ev=False) remains blocked.
    Fall C (custom claim, e.g. Azure preferred_username/upn) is now allowed.

    PostgreSQL: DROP CONSTRAINT IF EXISTS + ADD new formula via _safe_execute (idempotent).
    SQLite: table recreation when old formula is detected in sqlite_master (idempotent).
    """
    from sqlalchemy import text

    _NEW_FORMULA = "auto_link_existing_accounts = FALSE OR email_claim != 'email' OR require_email_verified = TRUE"
    _CONSTRAINT_NAME = "ck_auto_link_requires_verified_email_claim"

    if not is_sqlite():
        await _safe_execute(conn, f"ALTER TABLE oidc_providers DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}")
        await _safe_execute(
            conn,
            f"ALTER TABLE oidc_providers ADD CONSTRAINT {_CONSTRAINT_NAME} CHECK ({_NEW_FORMULA})",
        )
    else:
        row = (
            await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='oidc_providers'"))
        ).fetchone()
        # Only recreate if the old (more restrictive) formula is still present.
        # Fresh installs created with the new __table_args__ already have the correct formula.
        # Installs without any constraint (pre-SEC-1 upgrades) are skipped — app-level guards suffice.
        if row and "require_email_verified = TRUE AND email_claim = 'email'" in row[0]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text("DROP TABLE IF EXISTS oidc_providers_v2"))
                    await conn.execute(
                        text(
                            "CREATE TABLE oidc_providers_v2 ("
                            "id INTEGER NOT NULL, "
                            "name VARCHAR(100) NOT NULL, "
                            "issuer_url VARCHAR(500) NOT NULL, "
                            "client_id VARCHAR(255) NOT NULL, "
                            "client_secret VARCHAR(512) NOT NULL, "
                            "scopes VARCHAR(500), "
                            "is_enabled BOOLEAN, "
                            "auto_create_users BOOLEAN, "
                            "auto_link_existing_accounts BOOLEAN DEFAULT 0, "
                            "email_claim VARCHAR(64) DEFAULT 'email', "
                            "require_email_verified BOOLEAN DEFAULT 1, "
                            "icon_url TEXT, "
                            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                            "PRIMARY KEY (id), "
                            f"UNIQUE (name), "
                            f"CONSTRAINT {_CONSTRAINT_NAME} CHECK ({_NEW_FORMULA})"
                            ")"
                        )
                    )
                    await conn.execute(
                        text(
                            "INSERT INTO oidc_providers_v2 "
                            "(id, name, issuer_url, client_id, client_secret, scopes, is_enabled, "
                            "auto_create_users, auto_link_existing_accounts, email_claim, "
                            "require_email_verified, icon_url, created_at, updated_at) "
                            "SELECT id, name, issuer_url, client_id, client_secret, scopes, is_enabled, "
                            "auto_create_users, auto_link_existing_accounts, email_claim, "
                            "require_email_verified, icon_url, created_at, updated_at "
                            "FROM oidc_providers"
                        )
                    )
                    original = (await conn.execute(text("SELECT count(*) FROM oidc_providers"))).scalar_one()
                    copied = (await conn.execute(text("SELECT count(*) FROM oidc_providers_v2"))).scalar_one()
                    if copied != original:
                        raise RuntimeError(
                            f"auto_link constraint migration: row count mismatch after copy "
                            f"({original} in source, {copied} in copy)"
                        )
                    await conn.execute(text("DROP TABLE oidc_providers"))
                    await conn.execute(text("ALTER TABLE oidc_providers_v2 RENAME TO oidc_providers"))
            except Exception as exc:
                logger.error(
                    "auto_link constraint update (SQLite table recreation) FAILED: %s",
                    exc,
                    exc_info=True,
                )
                raise


async def _migrate_widen_spoolman_slot_ams_id_range(conn) -> None:
    """Widen ck_ams_id_range on spoolman_slot_assignments to admit AMS-HT (#1274).

    Old formula: (ams_id >= 0 AND ams_id <= 7) OR ams_id = 255
    New formula: (ams_id >= 0 AND ams_id <= 7) OR (ams_id >= 128 AND ams_id <= 191) OR ams_id = 255

    The H2C/H2D AMS-HT reports ams_id 128+. The old constraint rejected every
    AMS-HT slot link with `IntegrityError: CHECK constraint failed: ck_ams_id_range`.

    PostgreSQL: DROP CONSTRAINT IF EXISTS + ADD new formula via _safe_execute.
    SQLite: table recreation when the old (narrower) formula is detected in
    sqlite_master. Fresh installs already have the widened constraint from
    the CREATE TABLE migration above.
    """
    from sqlalchemy import text

    _NEW_FORMULA = "(ams_id >= 0 AND ams_id <= 7) OR (ams_id >= 128 AND ams_id <= 191) OR ams_id = 255"
    _CONSTRAINT_NAME = "ck_ams_id_range"

    if not is_sqlite():
        await _safe_execute(
            conn,
            f"ALTER TABLE spoolman_slot_assignments DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}",
        )
        await _safe_execute(
            conn,
            f"ALTER TABLE spoolman_slot_assignments ADD CONSTRAINT {_CONSTRAINT_NAME} CHECK ({_NEW_FORMULA})",
        )
        return

    row = (
        await conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='spoolman_slot_assignments'")
        )
    ).fetchone()
    if not row:
        return
    sql = row[0] or ""
    # Already widened by an earlier run or by the fresh-install CREATE TABLE above.
    if "ams_id >= 128" in sql:
        return
    # Pre-migration table without any CHECK constraint at all → leave alone;
    # the app-level validation handles correctness and we don't risk a
    # destructive table rebuild for a constraint that isn't blocking anyone.
    if "ck_ams_id_range" not in sql and "ams_id <= 7" not in sql:
        return

    try:
        async with conn.begin_nested():
            await conn.execute(text("DROP TABLE IF EXISTS spoolman_slot_assignments_v2"))
            await conn.execute(
                text(
                    "CREATE TABLE spoolman_slot_assignments_v2 ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE, "
                    f"ams_id INTEGER NOT NULL CHECK ({_NEW_FORMULA}), "
                    "tray_id INTEGER NOT NULL CHECK (tray_id >= 0 AND tray_id <= 3), "
                    "spoolman_spool_id INTEGER NOT NULL, "
                    "assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "CONSTRAINT uq_slot_assignment UNIQUE(printer_id, ams_id, tray_id)"
                    ")"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO spoolman_slot_assignments_v2 "
                    "(id, printer_id, ams_id, tray_id, spoolman_spool_id, assigned_at) "
                    "SELECT id, printer_id, ams_id, tray_id, spoolman_spool_id, assigned_at "
                    "FROM spoolman_slot_assignments"
                )
            )
            original = (await conn.execute(text("SELECT count(*) FROM spoolman_slot_assignments"))).scalar_one()
            copied = (await conn.execute(text("SELECT count(*) FROM spoolman_slot_assignments_v2"))).scalar_one()
            if copied != original:
                raise RuntimeError(
                    f"spoolman_slot_assignments migration: row count mismatch after copy "
                    f"({original} in source, {copied} in copy)"
                )
            await conn.execute(text("DROP TABLE spoolman_slot_assignments"))
            await conn.execute(text("ALTER TABLE spoolman_slot_assignments_v2 RENAME TO spoolman_slot_assignments"))
            # The index sits on the renamed table; recreate it idempotently
            # to handle older sqlite versions that don't auto-rename indexes.
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_slot_assignment_spool "
                    "ON spoolman_slot_assignments (spoolman_spool_id)"
                )
            )
    except Exception as exc:
        logger.error(
            "spoolman_slot_assignments ck_ams_id_range widening (SQLite table recreation) FAILED: %s",
            exc,
            exc_info=True,
        )
        raise


async def run_migrations(conn):
    """Run all schema migrations and data backfills on startup.

    Includes ALTER TABLE (add columns, rename columns, add constraints),
    CREATE INDEX, CREATE TRIGGER, data UPDATE backfills, and table recreations
    for complex SQLite schema changes that ALTER TABLE cannot handle.

    DDL statements are wrapped in _safe_execute for idempotency.
    DML backfills (UPDATE/DELETE) are executed directly via conn.execute()
    inside begin_nested() so any failure is always fatal and never silently
    swallowed.
    """
    from sqlalchemy import text

    # Migration: Add parent_run_id column to pipeline_runs (#1425 PR C).
    # Links a retry-failed run back to its parent so the dashboard can show
    # "Retry of run #N" inline. Idempotent on both SQLite and Postgres.
    await _safe_execute(
        conn,
        "ALTER TABLE pipeline_runs ADD COLUMN parent_run_id INTEGER REFERENCES pipeline_runs(id) ON DELETE SET NULL",
    )

    # Migration: Add source_archive_id column to pipeline_runs (#1425 PR B follow-up).
    # Allows a pipeline run to source from an archive's source 3MF in addition
    # to a library file. Idempotent — _safe_execute swallows the "already exists"
    # case on both SQLite and Postgres.
    await _safe_execute(
        conn,
        "ALTER TABLE pipeline_runs ADD COLUMN source_archive_id INTEGER REFERENCES print_archives(id) ON DELETE SET NULL",
    )

    # Migration: Add is_favorite column to print_archives
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN is_favorite BOOLEAN DEFAULT 0")

    # Migration: Add content_hash column to print_archives for duplicate detection
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN content_hash VARCHAR(64)")

    # Migration: Add auto_off_executed column to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_executed BOOLEAN DEFAULT 0")

    # Migration: Add on_print_stopped column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_print_stopped BOOLEAN DEFAULT 1")

    # Migration: Add source_3mf_path column to print_archives
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN source_3mf_path VARCHAR(500)")

    # Migration: Add f3d_path column to print_archives for Fusion 360 design files
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN f3d_path VARCHAR(500)")

    # Migration: Add plate_id column to print_archives (#2603). The selected plate
    # of a multi-plate 3MF is copied from the queue item at dispatch so Print
    # History can show the actual plate instead of falling back to Plate 1.
    # Nullable, no default — identical DDL on SQLite and Postgres. Backfilled from
    # linked queue rows below.
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN plate_id INTEGER")

    # Migration: Add on_maintenance_due column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_maintenance_due BOOLEAN DEFAULT 0")

    # Migration: Add location column to printers for grouping
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN location VARCHAR(100)")

    # Migration: Add interval_type column to maintenance_types
    await _safe_execute(conn, "ALTER TABLE maintenance_types ADD COLUMN interval_type VARCHAR(20) DEFAULT 'hours'")

    # Migration: Add is_deleted column to maintenance_types for soft-deletes
    await _safe_execute(conn, "ALTER TABLE maintenance_types ADD COLUMN is_deleted BOOLEAN DEFAULT 0")

    # Migration: Add custom_interval_type column to printer_maintenance
    await _safe_execute(conn, "ALTER TABLE printer_maintenance ADD COLUMN custom_interval_type VARCHAR(20)")

    # Migration: Add power alert columns to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_enabled BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_high REAL")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_low REAL")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_last_triggered DATETIME")

    # Migration: Add schedule columns to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN schedule_enabled BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN schedule_on_time VARCHAR(5)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN schedule_off_time VARCHAR(5)")

    # Migration: Add daily digest columns to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN daily_digest_enabled BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN daily_digest_time VARCHAR(5)")

    # Migration: Add missing-spool-assignment print-start notification toggle
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE notification_providers ADD COLUMN on_print_missing_spool_assignment BOOLEAN DEFAULT 0"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add project_id column to print_archives
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_archives ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add project_id column to print_queue
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE print_queue ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Enforce uniqueness on user_oidc_links for existing rows.
    # create_all() is idempotent and does not add constraints to existing tables,
    # so we create covering unique indexes explicitly here.
    await _safe_execute(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_oidc_link_provider_sub"
        " ON user_oidc_links (provider_id, provider_user_id)",
    )
    await _safe_execute(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_oidc_link_user_provider ON user_oidc_links (user_id, provider_id)",
    )

    # Migration: Create FTS5 virtual table for archive full-text search (SQLite only)
    # PostgreSQL uses tsvector + GIN index instead (set up in archives.py search route)
    if is_sqlite():
        try:
            await conn.execute(
                text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
                    print_name,
                    filename,
                    tags,
                    notes,
                    designer,
                    filament_type,
                    content='print_archives',
                    content_rowid='id'
                )
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

        # Migration: Create triggers to keep FTS index in sync
        try:
            await conn.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS archive_fts_insert AFTER INSERT ON print_archives BEGIN
                    INSERT INTO archive_fts(rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES (new.id, new.print_name, new.filename, new.tags, new.notes, new.designer, new.filament_type);
                END
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

        try:
            await conn.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS archive_fts_delete AFTER DELETE ON print_archives BEGIN
                    INSERT INTO archive_fts(archive_fts, rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES ('delete', old.id, old.print_name, old.filename, old.tags, old.notes, old.designer, old.filament_type);
                END
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

        try:
            await conn.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS archive_fts_update AFTER UPDATE ON print_archives BEGIN
                    INSERT INTO archive_fts(archive_fts, rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES ('delete', old.id, old.print_name, old.filename, old.tags, old.notes, old.designer, old.filament_type);
                    INSERT INTO archive_fts(rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES (new.id, new.print_name, new.filename, new.tags, new.notes, new.designer, new.filament_type);
                END
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add auto_off_pending columns to smart_plugs (for restart recovery)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_pending BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_pending_since DATETIME")

    # Migration: Add auto_off_persistent column to smart_plugs (keep auto-off enabled between prints)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_persistent BOOLEAN DEFAULT 0")

    # Migration: Add AMS alarm notification columns to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_ams_humidity_high BOOLEAN DEFAULT 0")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_ams_temperature_high BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add AMS-HT alarm notification columns to notification_providers
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_humidity_high BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_temperature_high BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add plate not empty notification column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_plate_not_empty BOOLEAN DEFAULT 1")

    # Migration: Add notes column to projects (Phase 2)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN notes TEXT")

    # Migration: Add attachments column to projects (Phase 3)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN attachments JSON")

    # Migration: Add tags column to projects (Phase 4)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN tags TEXT")

    # Migration: Add due_date column to projects (Phase 5)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN due_date DATETIME")

    # Migration: Add priority column to projects (Phase 5)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN priority VARCHAR(20) DEFAULT 'normal'")

    # Migration: Add budget column to projects (Phase 6)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN budget REAL")

    # Migration: Add is_template column to projects (Phase 8)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN is_template BOOLEAN DEFAULT 0")

    # Migration: Add template_source_id column to projects (Phase 8)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN template_source_id INTEGER")

    # Migration: Add parent_id column to projects (Phase 10)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE projects ADD COLUMN parent_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Rename quantity_printed to quantity_acquired in project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items RENAME COLUMN quantity_printed TO quantity_acquired")

    # Migration: Add unit_price column to project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items ADD COLUMN unit_price REAL")

    # Migration: Add sourcing_url column to project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items ADD COLUMN sourcing_url VARCHAR(512)")

    # Migration: Rename notes to remarks in project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items RENAME COLUMN notes TO remarks")

    # Migration: Add show_in_switchbar column to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN show_in_switchbar BOOLEAN DEFAULT 0")

    # Migration: Add runtime tracking columns to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN runtime_seconds INTEGER DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN last_runtime_update DATETIME")

    # Migration: Add quantity column to print_archives for tracking item count
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN quantity INTEGER DEFAULT 1")

    # Migration: Add manual_start column to print_queue for staged prints
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN manual_start BOOLEAN DEFAULT 0")

    # Migration: Add wiki_url column to maintenance_types for documentation links
    await _safe_execute(conn, "ALTER TABLE maintenance_types ADD COLUMN wiki_url VARCHAR(500)")

    # Migration: Add tailscale_disabled column to virtual_printers. Opt-in: default TRUE so
    # the auto-detect + fallback noise only runs for users who explicitly enable it.
    # Postgres rejects `DEFAULT 1` for BOOLEAN (#1070 round-2 review).
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN tailscale_disabled BOOLEAN DEFAULT 1")
    else:
        await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN tailscale_disabled BOOLEAN DEFAULT true")

    # Migration: Add ams_mapping column to print_queue for storing filament slot assignments
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN ams_mapping TEXT")

    # Migration: filament_short flag on print_queue (#1496). Set by the
    # dispatch scheduler when the assigned spool can't satisfy the print's
    # per-slot weight; surfaced as a "filament short" badge on the queue row.
    # Postgres rejects `DEFAULT 0` for BOOLEAN — branch on dialect.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN filament_short BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN filament_short BOOLEAN DEFAULT false")

    # Migration: skip_filament_check flag on print_queue (#1698-followup).
    # Persists the user's "Print Anyway" acknowledgement so the scheduler
    # doesn't re-flag the item every tick after they've confirmed dispatch
    # despite the deficit warning. Set from the start route's skip_filament_check
    # query param and from PrintModal at queue-creation time. Postgres / SQLite
    # boolean default branch matches filament_short above.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN skip_filament_check BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN skip_filament_check BOOLEAN DEFAULT false")

    # Migration: cleanup flag for transient printer-card uploads routed through
    # the scheduler. The archive copy is durable; the library row/file can be
    # deleted after dispatch.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN cleanup_library_after_dispatch BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(
            conn, "ALTER TABLE print_queue ADD COLUMN cleanup_library_after_dispatch BOOLEAN DEFAULT false"
        )

    # Migration: Add queue_force_color_match column to virtual_printers (#1188).
    # Opt-in flag: when true, VP queue-mode uploads pin the per-slot type+color
    # from the 3MF onto the queue item's filament_overrides so the scheduler
    # refuses to dispatch onto a printer with the wrong filament loaded.
    # Default false to preserve current behaviour for upgraders.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN queue_force_color_match BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(
            conn, "ALTER TABLE virtual_printers ADD COLUMN queue_force_color_match BOOLEAN DEFAULT FALSE"
        )

    # Per-VP opt-in for auto-print G-code injection (#1516). Default false so
    # existing gcode_snippets users don't silently start injecting on VP/Studio
    # Send jobs after upgrading.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN gcode_injection BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN gcode_injection BOOLEAN DEFAULT FALSE")

    # Migration: nozzle_mapping + nozzles_info on print_queue for H2C rack-swap
    # slicer-pick preservation (#1780). Opaque JSON-string column carrying
    # BambuStudio's per-filament physical nozzle position IDs, forwarded
    # straight from the VP intake to the dispatcher's project_file MQTT
    # command. NULL on every other model. Nullable TEXT — no Postgres / SQLite
    # divergence here. `nozzles_info` shipped in the original #1780 attempt
    # but BambuStudio never actually sends it (verified via wire capture on
    # H2C, see CHANGELOG 0.2.5b1) — the column stays nullable so old rows
    # still load; nothing reads or writes to it anymore.
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN nozzle_mapping TEXT")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN nozzles_info TEXT")

    # Migration: Add target_parts_count column to projects for tracking total parts needed
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN target_parts_count INTEGER")

    # Migration: Add url + cover_image_filename columns to projects (#1155).
    # url: external link rendered next to the project name on the card.
    # cover_image_filename: filename of the project's hero image inside the
    # existing attachments dir; rendered as a thumbnail on the card.
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN url VARCHAR(2048)")
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN cover_image_filename VARCHAR(255)")

    # Migration: enhanced filament colour handling on color_catalog (#1154).
    # Mirrors the Spool columns added below; widens hex_color to VARCHAR(9)
    # so catalog entries can store an alpha component (#RRGGBBAA). SQLite
    # ignores VARCHAR length, so the widen only matters on PostgreSQL.
    await _safe_execute(conn, "ALTER TABLE color_catalog ADD COLUMN extra_colors VARCHAR(255)")
    await _safe_execute(conn, "ALTER TABLE color_catalog ADD COLUMN effect_type VARCHAR(20)")
    if not is_sqlite():
        await _safe_execute(conn, "ALTER TABLE color_catalog ALTER COLUMN hex_color TYPE VARCHAR(9)")

    # Migration: Make printer_id nullable in print_queue for unassigned queue items
    # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='print_queue'"))
            row = result.fetchone()
            if row and "printer_id INTEGER NOT NULL" in (row[0] or ""):
                await conn.execute(
                    text("""
                    CREATE TABLE print_queue_new (
                        id INTEGER PRIMARY KEY,
                        printer_id INTEGER REFERENCES printers(id) ON DELETE CASCADE,
                        archive_id INTEGER NOT NULL REFERENCES print_archives(id) ON DELETE CASCADE,
                        project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                        position INTEGER DEFAULT 0,
                        scheduled_time DATETIME,
                        manual_start BOOLEAN DEFAULT 0,
                        require_previous_success BOOLEAN DEFAULT 0,
                        auto_off_after BOOLEAN DEFAULT 0,
                        ams_mapping TEXT,
                        status VARCHAR(20) DEFAULT 'pending',
                        started_at DATETIME,
                        completed_at DATETIME,
                        error_message TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO print_queue_new
                    SELECT id, printer_id, archive_id, project_id, position, scheduled_time,
                           manual_start, require_previous_success, auto_off_after, ams_mapping,
                           status, started_at, completed_at, error_message, created_at
                    FROM print_queue
                """)
                )
                await conn.execute(text("DROP TABLE print_queue"))
                await conn.execute(text("ALTER TABLE print_queue_new RENAME TO print_queue"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add plug_type column to smart_plugs for HA integration
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN plug_type VARCHAR(20) DEFAULT 'tasmota'")

    # Migration: Add ha_entity_id column to smart_plugs for HA integration
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_entity_id VARCHAR(100)")

    # Migration: Add project_id column to library_folders for linking folders to projects
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_folders ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add archive_id column to library_folders for linking folders to archives
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_folders ADD COLUMN archive_id INTEGER REFERENCES print_archives(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Make ip_address nullable for HA plugs (SQLite requires table recreation)
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='smart_plugs'"))
            row = result.fetchone()
            if row and "ip_address VARCHAR(45) NOT NULL" in (row[0] or ""):
                await conn.execute(
                    text("""
                    CREATE TABLE smart_plugs_new (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        ip_address VARCHAR(45),
                        plug_type VARCHAR(20) DEFAULT 'tasmota',
                        ha_entity_id VARCHAR(100),
                        printer_id INTEGER UNIQUE REFERENCES printers(id) ON DELETE SET NULL,
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        auto_on BOOLEAN NOT NULL DEFAULT 1,
                        auto_off BOOLEAN NOT NULL DEFAULT 1,
                        auto_off_persistent BOOLEAN NOT NULL DEFAULT 0,
                        off_delay_mode VARCHAR(20) NOT NULL DEFAULT 'time',
                        off_delay_minutes INTEGER NOT NULL DEFAULT 5,
                        off_temp_threshold INTEGER NOT NULL DEFAULT 70,
                        username VARCHAR(50),
                        password VARCHAR(100),
                        power_alert_enabled BOOLEAN NOT NULL DEFAULT 0,
                        power_alert_high FLOAT,
                        power_alert_low FLOAT,
                        power_alert_last_triggered DATETIME,
                        schedule_enabled BOOLEAN NOT NULL DEFAULT 0,
                        schedule_on_time VARCHAR(5),
                        schedule_off_time VARCHAR(5),
                        show_in_switchbar BOOLEAN DEFAULT 0,
                        last_state VARCHAR(10),
                        last_checked DATETIME,
                        auto_off_executed BOOLEAN NOT NULL DEFAULT 0,
                        auto_off_pending BOOLEAN DEFAULT 0,
                        auto_off_pending_since DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO smart_plugs_new
                    SELECT id, name, ip_address,
                           COALESCE(plug_type, 'tasmota'), ha_entity_id, printer_id,
                           enabled, auto_on, auto_off, COALESCE(auto_off_persistent, 0),
                           off_delay_mode, off_delay_minutes, off_temp_threshold,
                           username, password, power_alert_enabled, power_alert_high, power_alert_low,
                           power_alert_last_triggered, schedule_enabled, schedule_on_time, schedule_off_time,
                           COALESCE(show_in_switchbar, 0), last_state, last_checked, auto_off_executed,
                           COALESCE(auto_off_pending, 0), auto_off_pending_since, created_at, updated_at
                    FROM smart_plugs
                """)
                )
                await conn.execute(text("DROP TABLE smart_plugs"))
                await conn.execute(text("ALTER TABLE smart_plugs_new RENAME TO smart_plugs"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add plate_id column to print_queue for multi-plate 3MF support
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN plate_id INTEGER")

    # Migration: Add print options columns to print_queue
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN bed_levelling BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN flow_cali BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN vibration_cali BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN layer_inspect BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN timelapse BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN use_ams BOOLEAN DEFAULT 1")
    # Migration: Add nozzle offset calibration option (dual-nozzle printers, #1682).
    # Postgres rejects `DEFAULT 1` on a BOOLEAN column — use TRUE / 1 per dialect.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN nozzle_offset_cali BOOLEAN DEFAULT 1")
    else:
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN nozzle_offset_cali BOOLEAN DEFAULT TRUE")

    # Migration: Per-item preheat / heat-soak override (#1468). preheat_override
    # is one of {inherit, on, off} — 'inherit' falls back to the global
    # preheat_enabled setting; 'on' / 'off' force the decision. The chamber
    # target column overrides the filament-map derivation when not null.
    # Existing rows default to 'inherit' + NULL so behaviour is unchanged for
    # in-flight queues.
    await _safe_execute(
        conn,
        "ALTER TABLE print_queue ADD COLUMN preheat_override VARCHAR(10) DEFAULT 'inherit'",
    )
    await _safe_execute(
        conn,
        "ALTER TABLE print_queue ADD COLUMN preheat_chamber_target_override INTEGER",
    )

    # Migration: Add library_file_id column to print_queue and make archive_id nullable
    # This allows queue items to reference library files directly (archive created at print start)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_queue ADD COLUMN library_file_id INTEGER REFERENCES library_files(id) ON DELETE CASCADE"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Check if archive_id needs to be made nullable (requires table recreation in SQLite)
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='print_queue'"))
            row = result.fetchone()
            if row and "archive_id INTEGER NOT NULL" in (row[0] or ""):
                await conn.execute(
                    text("""
                    CREATE TABLE print_queue_new2 (
                        id INTEGER PRIMARY KEY,
                        printer_id INTEGER REFERENCES printers(id) ON DELETE CASCADE,
                        archive_id INTEGER REFERENCES print_archives(id) ON DELETE CASCADE,
                        library_file_id INTEGER REFERENCES library_files(id) ON DELETE CASCADE,
                        project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                        position INTEGER DEFAULT 0,
                        scheduled_time DATETIME,
                        manual_start BOOLEAN DEFAULT 0,
                        require_previous_success BOOLEAN DEFAULT 0,
                        auto_off_after BOOLEAN DEFAULT 0,
                        ams_mapping TEXT,
                        plate_id INTEGER,
                        bed_levelling BOOLEAN DEFAULT 1,
                        flow_cali BOOLEAN DEFAULT 0,
                        vibration_cali BOOLEAN DEFAULT 1,
                        layer_inspect BOOLEAN DEFAULT 0,
                        timelapse BOOLEAN DEFAULT 0,
                        use_ams BOOLEAN DEFAULT 1,
                        status VARCHAR(20) DEFAULT 'pending',
                        started_at DATETIME,
                        completed_at DATETIME,
                        error_message TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO print_queue_new2
                    SELECT id, printer_id, archive_id, NULL, project_id, position, scheduled_time,
                           manual_start, require_previous_success, auto_off_after, ams_mapping, plate_id,
                           COALESCE(bed_levelling, 1), COALESCE(flow_cali, 0), COALESCE(vibration_cali, 1),
                           COALESCE(layer_inspect, 0), COALESCE(timelapse, 0), COALESCE(use_ams, 1),
                           status, started_at, completed_at, error_message, created_at
                    FROM print_queue
                """)
                )
                await conn.execute(text("DROP TABLE print_queue"))
                await conn.execute(text("ALTER TABLE print_queue_new2 RENAME TO print_queue"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add HA energy sensor entity columns to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_power_entity VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_energy_today_entity VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_energy_total_entity VARCHAR(100)")

    # Migration: Create users table for authentication
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR(100) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_username ON users(username)"))
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add external camera columns to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN external_camera_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN external_camera_type VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN external_camera_enabled BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN external_camera_snapshot_url VARCHAR(500)")

    # Migration: Add external_url column to print_archives for user-defined links (Printables, etc.)
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN external_url VARCHAR(500)")

    # Migration: Add sliced_for_model column to print_archives for model-based queue assignment
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN sliced_for_model VARCHAR(50)")

    # Migration: Add is_external column to library_files for external cloud files
    await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN is_external BOOLEAN DEFAULT 0")

    # Migration: Add project_id column to library_files
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_files ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add is_external column to library_folders for external cloud folders
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN is_external BOOLEAN DEFAULT 0")

    # Migration: Add external folder settings columns to library_folders
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN external_readonly BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN external_show_hidden BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN external_path VARCHAR(500)")

    # Migration: Add plate_detection_enabled column to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_enabled BOOLEAN DEFAULT 0")

    # Migration: Add plate detection ROI columns to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_x REAL")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_y REAL")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_w REAL")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_h REAL")

    # Migration: Remove UNIQUE constraint from smart_plugs.printer_id
    # This allows HA scripts to coexist with regular plugs (scripts are for multi-device control)
    # SQLite requires table recreation to drop constraints
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            needs_migration = False
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='smart_plugs'"))
            row = result.fetchone()
            table_sql = (row[0] or "").upper() if row else ""
            if "PRINTER_ID" in table_sql and "UNIQUE" in table_sql:
                import re

                if re.search(r'"?PRINTER_ID"?\s+\w+\s+UNIQUE', table_sql) or re.search(
                    r'UNIQUE\s*\([^)]*"?PRINTER_ID"?', table_sql
                ):
                    needs_migration = True
            idx_result = await conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='smart_plugs' AND sql IS NOT NULL")
            )
            for idx_row in idx_result.fetchall():
                idx_sql = (idx_row[0] or "").upper()
                if "UNIQUE" in idx_sql and "PRINTER_ID" in idx_sql:
                    needs_migration = True
                    break
            if needs_migration:
                # Create new table without UNIQUE constraint on printer_id
                await conn.execute(
                    text("""
                    CREATE TABLE smart_plugs_temp (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        ip_address VARCHAR(45),
                        plug_type VARCHAR(20) DEFAULT 'tasmota',
                        ha_entity_id VARCHAR(100),
                        ha_power_entity VARCHAR(100),
                        ha_energy_today_entity VARCHAR(100),
                        ha_energy_total_entity VARCHAR(100),
                        printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        auto_on BOOLEAN NOT NULL DEFAULT 1,
                        auto_off BOOLEAN NOT NULL DEFAULT 1,
                        auto_off_persistent BOOLEAN NOT NULL DEFAULT 0,
                        off_delay_mode VARCHAR(20) NOT NULL DEFAULT 'time',
                        off_delay_minutes INTEGER NOT NULL DEFAULT 5,
                        off_temp_threshold INTEGER NOT NULL DEFAULT 70,
                        username VARCHAR(50),
                        password VARCHAR(100),
                        power_alert_enabled BOOLEAN NOT NULL DEFAULT 0,
                        power_alert_high FLOAT,
                        power_alert_low FLOAT,
                        power_alert_last_triggered DATETIME,
                        schedule_enabled BOOLEAN NOT NULL DEFAULT 0,
                        schedule_on_time VARCHAR(5),
                        schedule_off_time VARCHAR(5),
                        show_in_switchbar BOOLEAN DEFAULT 0,
                        last_state VARCHAR(10),
                        last_checked DATETIME,
                        auto_off_executed BOOLEAN NOT NULL DEFAULT 0,
                        auto_off_pending BOOLEAN DEFAULT 0,
                        auto_off_pending_since DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                """)
                )
                # Copy data
                await conn.execute(
                    text("""
                    INSERT INTO smart_plugs_temp
                    SELECT id, name, ip_address, plug_type, ha_entity_id, ha_power_entity,
                           ha_energy_today_entity, ha_energy_total_entity, printer_id, enabled,
                           auto_on, auto_off, COALESCE(auto_off_persistent, 0),
                           off_delay_mode, off_delay_minutes, off_temp_threshold,
                           username, password, power_alert_enabled, power_alert_high, power_alert_low,
                           power_alert_last_triggered, schedule_enabled, schedule_on_time, schedule_off_time,
                           show_in_switchbar, last_state, last_checked, auto_off_executed,
                           auto_off_pending, auto_off_pending_since, created_at, updated_at
                    FROM smart_plugs
                """)
                )
                # Drop old table and rename new one
                await conn.execute(text("DROP TABLE smart_plugs"))
                await conn.execute(text("ALTER TABLE smart_plugs_temp RENAME TO smart_plugs"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add show_on_printer_card column to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN show_on_printer_card BOOLEAN DEFAULT 1")

    # Migration: Add MQTT smart plug fields (legacy)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_power_path VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_path VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_state_path VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_multiplier REAL DEFAULT 1.0")

    # Migration: Add enhanced MQTT smart plug fields (separate topics and multipliers)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_power_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_power_multiplier REAL DEFAULT 1.0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_multiplier REAL DEFAULT 1.0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_state_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_state_on_value VARCHAR(50)")

    # Migration: Copy existing mqtt_topic to mqtt_power_topic for backward compatibility
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                UPDATE smart_plugs
                SET mqtt_power_topic = mqtt_topic,
                    mqtt_power_multiplier = mqtt_multiplier
                WHERE mqtt_topic IS NOT NULL AND mqtt_power_topic IS NULL
            """)
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Create groups table for permission-based access control
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    description VARCHAR(500),
                    permissions JSON,
                    is_system BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_groups_name ON groups(name)"))
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Create user_groups association table
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS user_groups (
                    user_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, group_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                )
            """)
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add model-based queue assignment columns to print_queue
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN target_model VARCHAR(50)")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN required_filament_types TEXT")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN waiting_reason TEXT")

    # Migration: Add nozzle_count column to printers (for dual-extruder detection)
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN nozzle_count INTEGER DEFAULT 1")

    # Migration: Add print_hours_offset column to printers (baseline hours adjustment)
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN print_hours_offset REAL DEFAULT 0.0")

    # Migration: Add queue notification event columns to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_added BOOLEAN DEFAULT 0")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_assigned BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_started BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_waiting BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_skipped BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_failed BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_completed BOOLEAN DEFAULT 0")

    # Migration: Add created_by_id column to print_archives for user tracking (Issue #206)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_archives ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add created_by_id column to print_queue for user tracking (Issue #206)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE print_queue ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add created_by_id column to library_files for user tracking (Issue #206)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_files ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add target_location column to print_queue for location-based filtering (Issue #220)
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN target_location VARCHAR(100)")

    # Migration: Convert absolute paths to relative paths in library_files table
    # This ensures backup/restore portability across different installations
    try:
        async with conn.begin_nested():
            base_dir_str = str(settings.base_dir)
            # Ensure we have a trailing slash for clean replacement
            if not base_dir_str.endswith("/"):
                base_dir_str += "/"

            # Update file_path - remove base_dir prefix from absolute paths
            await conn.execute(
                text("""
                UPDATE library_files
                SET file_path = SUBSTR(file_path, LENGTH(:base_dir) + 1)
                WHERE file_path LIKE :pattern
            """),
                {"base_dir": base_dir_str, "pattern": base_dir_str + "%"},
            )

            # Update thumbnail_path - remove base_dir prefix from absolute paths
            await conn.execute(
                text("""
                UPDATE library_files
                SET thumbnail_path = SUBSTR(thumbnail_path, LENGTH(:base_dir) + 1)
                WHERE thumbnail_path LIKE :pattern
            """),
                {"base_dir": base_dir_str, "pattern": base_dir_str + "%"},
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Create active_print_spoolman table for Spoolman per-filament tracking.
    # filament_usage is nullable so the no-3MF branch can still create a row
    # that carries only tray_remain_start for the remain%-delta fallback
    # (#1820 — matches internal-inventory Path 2 in usage_tracker).
    await _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS active_print_spoolman (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            archive_id INTEGER NOT NULL REFERENCES print_archives(id) ON DELETE CASCADE,
            filament_usage TEXT,
            ams_trays TEXT NOT NULL,
            slot_to_tray TEXT,
            layer_usage TEXT,
            filament_properties TEXT,
            tray_remain_start TEXT,
            UNIQUE(printer_id, archive_id)
        )
        """
        if is_sqlite()
        else """
        CREATE TABLE IF NOT EXISTS active_print_spoolman (
            id SERIAL PRIMARY KEY,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            archive_id INTEGER NOT NULL REFERENCES print_archives(id) ON DELETE CASCADE,
            filament_usage TEXT,
            ams_trays TEXT NOT NULL,
            slot_to_tray TEXT,
            layer_usage TEXT,
            filament_properties TEXT,
            tray_remain_start TEXT,
            UNIQUE(printer_id, archive_id)
        )
        """,
    )
    # Migration for installs that already created active_print_spoolman with
    # the original schema: add tray_remain_start, and relax filament_usage's
    # NOT NULL so the no-3MF branch can persist a remain-only tracking row.
    await _safe_execute(conn, "ALTER TABLE active_print_spoolman ADD COLUMN tray_remain_start TEXT")
    if is_sqlite():
        # SQLite can't ALTER COLUMN; patch sqlite_master directly. Mirrors the
        # users.password_hash NULL-relaxation a few hundred lines below — see
        # the comment there for the schema_version bump rationale.
        try:
            result = await conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='active_print_spoolman'")
            )
            tbl_sql = result.scalar()
            if tbl_sql and "filament_usage TEXT NOT NULL" in tbl_sql:
                version_result = await conn.execute(text("PRAGMA schema_version"))
                schema_version = version_result.scalar() or 0
                await conn.execute(text("PRAGMA writable_schema = ON"))
                await conn.execute(
                    text(
                        "UPDATE sqlite_master "
                        "SET sql = replace(sql, 'filament_usage TEXT NOT NULL', 'filament_usage TEXT') "
                        "WHERE type='table' AND name='active_print_spoolman'"
                    )
                )
                await conn.execute(text(f"PRAGMA schema_version = {schema_version + 1}"))
                await conn.execute(text("PRAGMA writable_schema = OFF"))
        except (OperationalError, ProgrammingError) as exc:
            logger.warning(
                "Could not relax active_print_spoolman.filament_usage NOT NULL via writable_schema: %s — "
                "no-3MF Spoolman fallback will be a no-op on this install",
                exc,
            )
    else:
        await _safe_execute(conn, "ALTER TABLE active_print_spoolman ALTER COLUMN filament_usage DROP NOT NULL")

    # Migration: Add preset_source column to slot_preset_mappings for local preset support
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE slot_preset_mappings ADD COLUMN preset_source VARCHAR(20) DEFAULT 'cloud'")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add email column to users for Advanced Auth (PR #322)
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN email VARCHAR(255)")

    # Migration: Add inventory spool tracking columns
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN added_full BOOLEAN")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN last_used DATETIME")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN encode_time DATETIME")

    # Migration: Add RFID tag matching columns to spool
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN tag_uid VARCHAR(16)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN tray_uuid VARCHAR(32)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN data_origin VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN tag_type VARCHAR(20)")

    # Migration: Add core_weight_catalog_id to track which catalog entry was used for empty spool weight
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN core_weight_catalog_id INTEGER")

    # Migration: Create spool_usage_history table for filament consumption tracking
    await _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS spool_usage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spool_id INTEGER NOT NULL REFERENCES spool(id) ON DELETE CASCADE,
            printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
            print_name VARCHAR(500),
            weight_used REAL NOT NULL DEFAULT 0,
            percent_used INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'completed',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
        if is_sqlite()
        else """
        CREATE TABLE IF NOT EXISTS spool_usage_history (
            id SERIAL PRIMARY KEY,
            spool_id INTEGER NOT NULL REFERENCES spool(id) ON DELETE CASCADE,
            printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
            print_name VARCHAR(500),
            weight_used REAL NOT NULL DEFAULT 0,
            percent_used INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'completed',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )

    # Migration: Add open_in_new_tab column to external_links
    await _safe_execute(conn, "ALTER TABLE external_links ADD COLUMN open_in_new_tab BOOLEAN DEFAULT 0")

    # Migration: Add bed cooled notification column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_bed_cooled BOOLEAN DEFAULT 0")

    # Migration: Add first layer complete notification column to notification_providers
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_first_layer_complete BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add weight_locked flag to spool table (skip AMS auto-sync for manually-entered weights)
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN weight_locked BOOLEAN DEFAULT 0")

    # Migration: Add SpoolBuddy scale weight tracking columns to spool table
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN last_scale_weight INTEGER")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN last_weighed_at DATETIME")

    # Migration: Add cost tracking fields to spool table
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN cost_per_kg REAL")

    # Migration: Per-spool category + low-stock threshold override (#729). Both
    # nullable — NULL category leaves the spool uncategorised, NULL threshold
    # falls back to the global low_stock_threshold setting.
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN category VARCHAR(50)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN low_stock_threshold_pct INTEGER")
    # Migration: Add user-editable storage location to spool table
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN storage_location VARCHAR(255)")
    # Migration: Add weight_used_baseline anchor for the resettable "Total
    # Consumed" stat (#1390). Existing spools default to 0 (no baseline),
    # so the counter starts unaffected; pressing "Reset usage to 0" now
    # stamps baseline = weight_used without touching remaining.
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN weight_used_baseline REAL DEFAULT 0")
    # Migration: Widen tag_uid column from VARCHAR(16) to VARCHAR(32) to accommodate 7-byte NFC
    # UIDs (14 hex chars) in addition to 8-byte Bambu Lab UIDs (16 hex chars).
    # ALTER COLUMN ... TYPE is PostgreSQL-only syntax; SQLite ignores VARCHAR sizes so no-op there.
    if not is_sqlite():
        await _safe_execute(conn, "ALTER TABLE spool ALTER COLUMN tag_uid TYPE VARCHAR(32)")

    # Migration: enhanced filament colour handling (#1154). `extra_colors` is
    # a comma-separated list of 6- or 8-char hex tokens (no `#`) for multi-
    # colour gradients; `effect_type` is one of {sparkle, wood, marble, glow,
    # matte} as a visual rendering hint. Both nullable — NULL keeps the
    # current single-rgba/no-effect behaviour.
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN extra_colors VARCHAR(255)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN effect_type VARCHAR(20)")
    # Migration: Add cost field to spool_usage_history table
    await _safe_execute(conn, "ALTER TABLE spool_usage_history ADD COLUMN cost REAL")
    # Migration: Add archive_id field to spool_usage_history table
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE spool_usage_history ADD COLUMN archive_id INTEGER REFERENCES print_archives(id)")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Migrate single virtual printer key-value settings to virtual_printers table
    try:
        async with conn.begin_nested():
            result = await conn.execute(text("SELECT COUNT(*) FROM virtual_printers"))
            count = result.scalar() or 0

            if count == 0:
                result = await conn.execute(text("SELECT value FROM settings WHERE key = 'virtual_printer_enabled'"))
                row = result.fetchone()
                if row:
                    # Old settings exist — migrate to first virtual printer row
                    old_enabled = row[0] == "true" if row[0] else False

                    result = await conn.execute(
                        text("SELECT value FROM settings WHERE key = 'virtual_printer_access_code'")
                    )
                    row = result.fetchone()
                    old_access_code = row[0] if row else None

                    result = await conn.execute(text("SELECT value FROM settings WHERE key = 'virtual_printer_mode'"))
                    row = result.fetchone()
                    old_mode = row[0] if row else "archive"
                    # Translate to canonical wire values (#1429 mode-label
                    # discrepancy): legacy `immediate` → `archive`, legacy
                    # `print_queue` → `queue`. The historical `queue` alias
                    # for `review` predates the canonical rename and is
                    # preserved (existing user intent was "pending review").
                    if old_mode == "queue":
                        old_mode = "review"
                    elif old_mode == "immediate":
                        old_mode = "archive"
                    elif old_mode == "print_queue":
                        old_mode = "queue"

                    result = await conn.execute(text("SELECT value FROM settings WHERE key = 'virtual_printer_model'"))
                    row = result.fetchone()
                    old_model = row[0] if row else "BL-P001"

                    result = await conn.execute(
                        text("SELECT value FROM settings WHERE key = 'virtual_printer_target_printer_id'")
                    )
                    row = result.fetchone()
                    old_target_id = int(row[0]) if row and row[0] else None

                    result = await conn.execute(
                        text("SELECT value FROM settings WHERE key = 'virtual_printer_remote_interface_ip'")
                    )
                    row = result.fetchone()
                    old_remote_iface = row[0] if row else None

                    await conn.execute(
                        text("""
                            INSERT INTO virtual_printers
                                (name, enabled, mode, model, access_code, target_printer_id,
                                 bind_ip, remote_interface_ip, serial_suffix, position)
                            VALUES
                                (:name, :enabled, :mode, :model, :access_code, :target_id,
                                 NULL, :remote_iface, '391800001', 0)
                        """),
                        {
                            "name": "Bambuddy",
                            "enabled": old_enabled,
                            "mode": old_mode or "archive",
                            "model": old_model,
                            "access_code": old_access_code,
                            "target_id": old_target_id,
                            "remote_iface": old_remote_iface,
                        },
                    )
    except (OperationalError, ProgrammingError, IntegrityError):
        pass  # Table may not exist yet on first run, or columns have different constraints

    # Migration: Add filament_overrides column to print_queue for filament override in model-based assignment
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN filament_overrides TEXT")

    # Migration: Add NFC reader and display control columns to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN nfc_reader_type VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN nfc_connection VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN display_brightness INTEGER DEFAULT 100")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN display_blank_timeout INTEGER DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN has_backlight BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN last_calibrated_at DATETIME")

    # Migration: Add NFC tag write payload column to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN pending_write_payload TEXT")

    # Migration: Add OTA update tracking columns to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN update_status VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN update_message VARCHAR(255)")

    # Migration: Persist SpoolBuddy backend URL and queued system payload
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN backend_url VARCHAR(255)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN pending_system_payload TEXT")

    # Migration: Add system_stats JSON blob column to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN system_stats TEXT")

    # Migration: Add SSH host key for TOFU verification (H1 security fix)
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN ssh_host_key VARCHAR(500)")
    # Migration: Widen ssh_host_key from VARCHAR(500) to TEXT — RSA-3072+ host keys
    # in OpenSSH format exceed 500 chars (RSA-4096 ~720 chars). PostgreSQL enforces
    # the limit and rejects the UPDATE; SQLite ignores VARCHAR length so no-op there.
    if not is_sqlite():
        await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ALTER COLUMN ssh_host_key TYPE TEXT")

    # Migration: Convert ams_labels table from (printer_id, ams_id) key to ams_serial_number key
    # Labels are now keyed by AMS serial number so they persist when the AMS is moved to another printer.
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            await conn.execute(text("DROP TABLE IF EXISTS ams_labels_new"))
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='ams_labels'"))
            row = result.fetchone()
            if row and "printer_id" in (row[0] or ""):
                # Old schema: rebuild the table with ams_serial_number as the unique key.
                # Existing rows get a synthetic serial "p{printer_id}a{ams_id}" so data is preserved.
                await conn.execute(
                    text("""
                    CREATE TABLE ams_labels_new (
                        id INTEGER PRIMARY KEY,
                        ams_serial_number VARCHAR(50) NOT NULL,
                        ams_id INTEGER,
                        label VARCHAR(100) NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_ams_label_serial UNIQUE (ams_serial_number)
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO ams_labels_new (id, ams_serial_number, ams_id, label, created_at, updated_at)
                    SELECT id,
                           'p' || CAST(printer_id AS TEXT) || 'a' || CAST(ams_id AS TEXT),
                           ams_id,
                           label,
                           created_at,
                           updated_at
                    FROM ams_labels
                """)
                )
                await conn.execute(text("DROP TABLE ams_labels"))
                await conn.execute(text("ALTER TABLE ams_labels_new RENAME TO ams_labels"))
        except (OperationalError, ProgrammingError):
            pass  # Already migrated or table does not exist yet

    # Migration: Add auto_dispatch column to virtual_printers
    await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN auto_dispatch BOOLEAN DEFAULT 1")

    # Migration: Fix VP model codes — convert legacy SSDP codes and display names to correct SSDP codes
    # Legacy codes (from multi-VP refactor) and display names (from proxy auto-inherit)
    vp_model_fixes = {
        "3DPrinter-X1-Carbon": "BL-P001",
        "3DPrinter-X1": "BL-P002",
        "X1C": "BL-P001",
        "X1": "BL-P002",
        "X1E": "C13",
        "X2D": "N6",
        "P1P": "C11",
        "P1S": "C12",
        "P2S": "N7",
        "A1": "N2S",
        "A1 Mini": "N1",
        "H2D": "O1D",
        "H2C": "O1C",
        "H2S": "O1S",
    }
    for old_val, new_val in vp_model_fixes.items():
        await conn.execute(
            text("UPDATE virtual_printers SET model = :new WHERE model = :old"),
            {"old": old_val, "new": new_val},
        )
        await conn.execute(
            text("UPDATE settings SET value = :new WHERE key = 'virtual_printer_model' AND value = :old"),
            {"old": old_val, "new": new_val},
        )

    # Migration: Rename VP mode wire values to match the user-facing labels
    # (#1429 follow-up). The UI button "Archive" had always saved `immediate`
    # and "Queue" had always saved `print_queue` — a mismatch that showed up
    # confusingly in every support bundle. The button labels stay; the wire
    # value is what changes. Idempotent: re-running the UPDATE on canonical
    # values is a no-op. SQLite and Postgres both accept this statement
    # unchanged (string literal comparison, no driver-specific syntax).
    vp_mode_renames = [("immediate", "archive"), ("print_queue", "queue")]
    for old_val, new_val in vp_mode_renames:
        await conn.execute(
            text("UPDATE virtual_printers SET mode = :new WHERE mode = :old"),
            {"old": old_val, "new": new_val},
        )
        await conn.execute(
            text("UPDATE settings SET value = :new WHERE key = 'virtual_printer_mode' AND value = :old"),
            {"old": old_val, "new": new_val},
        )

    # Migration: Auto-sync VP access codes from their target printer.
    # Non-proxy VPs with a target printer (the live-mirror bridge) forward the
    # slicer's MQTT/RTSPS auth bytes through to the real printer, so the VP's
    # access code MUST equal the target's — earlier UIs let them diverge,
    # producing a VP that the slicer could bind but whose bridge silently
    # failed to authenticate against the real printer. The route layer now
    # auto-inherits on every create/update; this backfill corrects any rows
    # that pre-date that change. Idempotent (re-running on synced rows is a
    # no-op because the WHERE clause excludes them). SQLite and Postgres both
    # accept correlated subqueries in UPDATE — no driver-specific syntax.
    mismatch_result = await conn.execute(
        text(
            "SELECT vp.id AS vp_id, vp.name AS vp_name, p.name AS target_name "
            "FROM virtual_printers vp "
            "JOIN printers p ON vp.target_printer_id = p.id "
            "WHERE vp.mode != 'proxy' "
            "  AND (vp.access_code IS NULL OR vp.access_code != p.access_code)"
        )
    )
    for row in mismatch_result.fetchall():
        logger.info(
            "VP %r (id=%d) access code synced from target printer %r",
            row.vp_name,
            row.vp_id,
            row.target_name,
        )
    await conn.execute(
        text(
            "UPDATE virtual_printers "
            "SET access_code = ("
            "    SELECT access_code FROM printers WHERE printers.id = virtual_printers.target_printer_id"
            ") "
            "WHERE virtual_printers.target_printer_id IS NOT NULL "
            "  AND virtual_printers.mode != 'proxy' "
            "  AND (virtual_printers.access_code IS NULL OR virtual_printers.access_code != ("
            "      SELECT access_code FROM printers WHERE printers.id = virtual_printers.target_printer_id"
            "  ))"
        )
    )

    # Migration: Recover queue items that got stuck in `skipped` because of
    # the cancellation-cascade bug (#1667). Pre-fix, the scheduler's
    # `_check_previous_success` lookback excluded `cancelled` but included
    # `skipped`, so a single user-cancelled print poisoned every downstream
    # item with `require_previous_success=True` indefinitely. The reporter saw
    # 18 items blocked over 3 days from one cancellation.
    #
    # Conservative reversal: ONLY reset rows whose immediate predecessor on
    # the same printer (by completed_at desc, excluding the skipped-bug
    # cascade) was `cancelled`. Skipped items whose true predecessor was a
    # real `failed` or `aborted` print stay skipped — those were legitimate.
    # Genuine failure-skips share the same status + error_message + completed_at
    # fingerprint as bug-skips, so the predecessor check is what distinguishes
    # them. Idempotent (post-reset rows no longer match the WHERE clause).
    #
    # Correlated subquery is portable across SQLite and Postgres. The
    # `error_message` literal matches the exact string the buggy scheduler
    # wrote — narrowing further on intent.
    stuck_skipped_result = await conn.execute(
        text(
            "SELECT pq.id, pq.printer_id "
            "FROM print_queue pq "
            "WHERE pq.status = 'skipped' "
            "  AND pq.error_message = 'Previous print failed or was aborted' "
            "  AND pq.completed_at IS NOT NULL "
            "  AND ("
            "    SELECT prev.status FROM print_queue prev "
            "    WHERE prev.printer_id = pq.printer_id "
            "      AND prev.id != pq.id "
            "      AND prev.status IN ('completed', 'failed', 'cancelled', 'aborted') "
            "      AND prev.completed_at IS NOT NULL "
            "      AND prev.completed_at < pq.completed_at "
            "    ORDER BY prev.completed_at DESC LIMIT 1"
            "  ) = 'cancelled'"
        )
    )
    stuck_ids = [row.id for row in stuck_skipped_result.fetchall()]
    if stuck_ids:
        logger.info(
            "Queue cancellation-cascade migration (#1667): resetting %d skipped item(s) to pending",
            len(stuck_ids),
        )
        await conn.execute(
            text(
                "UPDATE print_queue "
                "SET status = 'pending', error_message = NULL, completed_at = NULL "
                "WHERE id IN ("
                "  SELECT pq.id FROM print_queue pq "
                "  WHERE pq.status = 'skipped' "
                "    AND pq.error_message = 'Previous print failed or was aborted' "
                "    AND pq.completed_at IS NOT NULL "
                "    AND ("
                "      SELECT prev.status FROM print_queue prev "
                "      WHERE prev.printer_id = pq.printer_id "
                "        AND prev.id != pq.id "
                "        AND prev.status IN ('completed', 'failed', 'cancelled', 'aborted') "
                "        AND prev.completed_at IS NOT NULL "
                "        AND prev.completed_at < pq.completed_at "
                "      ORDER BY prev.completed_at DESC LIMIT 1"
                "    ) = 'cancelled'"
                ")"
            )
        )

    # Migration: Unify `LibraryFile.file_type` across ingest paths (#1600).
    # Pre-#1600, only the external-folder scan path stored `gcode.3mf` for
    # sliced outputs — the upload, ZIP-extract, and in-process paths all
    # stripped to the trailing `.3mf` and stored `3mf`, so the same file
    # family was split between two values depending on how it was ingested.
    # Going forward `classify_file_type()` is canonical; this backfill flips
    # existing legacy `3mf` rows whose filename ends in `.gcode.3mf` to the
    # canonical compound name. Idempotent (post-update rows no longer match
    # `file_type = '3mf'`) and dialect-neutral (`LOWER` + `LIKE` work the
    # same under SQLite and Postgres).
    await conn.execute(
        text(
            "UPDATE library_files SET file_type = 'gcode.3mf' "
            "WHERE file_type = '3mf' AND LOWER(filename) LIKE '%.gcode.3mf'"
        )
    )

    # Migration: Add per-user Bambu Cloud credential columns
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN cloud_token VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN cloud_email VARCHAR(255)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN cloud_region VARCHAR(10)")

    # Cleanup: Remove obsolete settings keys that are no longer used
    obsolete_keys = ["slicer_binary_path"]
    for key in obsolete_keys:
        await conn.execute(text("DELETE FROM settings WHERE key = :key"), {"key": key})

    # Migration: Create user_email_preferences table for user-specific email notification settings
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS user_email_preferences (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    notify_print_start BOOLEAN NOT NULL DEFAULT 1,
                    notify_print_complete BOOLEAN NOT NULL DEFAULT 1,
                    notify_print_failed BOOLEAN NOT NULL DEFAULT 1,
                    notify_print_stopped BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_user_email_preferences_user_id ON user_email_preferences(user_id)")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Legacy migration: Add notify_print_stopped column (for any existing partial tables)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE user_email_preferences ADD COLUMN notify_print_stopped BOOLEAN NOT NULL DEFAULT 1")
            )
    except (OperationalError, ProgrammingError):
        pass  # Column already exists or table created with full schema

    # Migration: Add camera_rotation column to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN camera_rotation INTEGER DEFAULT 0")

    # Migration: Add awaiting_plate_clear column to printers (#961)
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN awaiting_plate_clear BOOLEAN DEFAULT FALSE NOT NULL")

    # Migration: Add REST/Webhook smart plug fields
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_on_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_on_body TEXT")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_off_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_off_body TEXT")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_method VARCHAR(10)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_headers TEXT")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_status_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_status_path VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_status_on_value VARCHAR(50)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_power_path VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_path VARCHAR(200)")

    # Migration: Add separate REST power/energy URLs and multipliers
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_power_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_power_multiplier REAL DEFAULT 1.0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_multiplier REAL DEFAULT 1.0")

    # Migration (#2539): a REST plug's lifetime energy counter, separate from its
    # today counter. Devices differ in which they expose — a Shelly reports only
    # a cumulative `aenergy.total`, a Tasmota behind a REST bridge reports both —
    # and conflating the two made the cumulative value read as "today", so it
    # never reset at midnight and "Total" stayed empty forever.
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_total_path VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_total_multiplier REAL DEFAULT 1.0")

    # Migration: Add batch_id column to print_queue for batch grouping
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_queue ADD COLUMN batch_id INTEGER REFERENCES print_batches(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass

    # Migration: Shortest-job-first scheduling columns on print_queue
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN print_time_seconds INTEGER")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN been_jumped BOOLEAN DEFAULT FALSE NOT NULL")

    # Migration: Auto-print G-code injection (#422)
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN gcode_injection BOOLEAN DEFAULT FALSE NOT NULL")

    # Migration: Add backup_spools and backup_archives columns to github_backup_config
    await _safe_execute(conn, "ALTER TABLE github_backup_config ADD COLUMN backup_spools BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE github_backup_config ADD COLUMN backup_archives BOOLEAN DEFAULT 0")

    # Migration: Widen columns where SQLite allowed data beyond the declared VARCHAR limit
    if not is_sqlite():
        await _safe_execute(conn, "ALTER TABLE api_keys ALTER COLUMN key_hash TYPE VARCHAR(255)")
        await _safe_execute(conn, "ALTER TABLE api_keys ALTER COLUMN key_prefix TYPE VARCHAR(20)")
        await _safe_execute(conn, "ALTER TABLE print_archives ALTER COLUMN filament_color TYPE VARCHAR(200)")

    # Migration: Create GIN index for full-text search on PostgreSQL
    # (SQLite uses FTS5 virtual table instead, set up above)
    if not is_sqlite():
        try:
            await conn.execute(
                text("""
                CREATE INDEX IF NOT EXISTS idx_archives_fulltext
                ON print_archives
                USING GIN (to_tsvector('simple',
                    COALESCE(print_name, '') || ' ' ||
                    COALESCE(filename, '') || ' ' ||
                    COALESCE(tags, '') || ' ' ||
                    COALESCE(notes, '') || ' ' ||
                    COALESCE(designer, '') || ' ' ||
                    COALESCE(filament_type, '')
                ))
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Normalize empty printer_ids [] to NULL (global access) on API keys
    # Previously both None and [] meant "all printers"; now [] means "no printers"
    # PostgreSQL stores printer_ids as JSONB; comparing JSONB to a string literal fails
    # with "operator does not exist: jsonb = unknown" — cast the literal to jsonb explicitly.
    await _migrate_normalize_printer_ids(conn)

    # Migration: Add auth_source column to users for LDAP support (#794)
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN auth_source VARCHAR(20) DEFAULT 'local' NOT NULL")

    # Migration: Make password_hash nullable for LDAP users (#794)
    # LDAP users have no local password — the column must allow NULL so auto-provisioning
    # doesn't hit a NOT NULL constraint failure on upgraded installs whose users table was
    # originally created before LDAP support landed.
    if is_sqlite():
        # SQLite can't ALTER COLUMN; patch sqlite_master directly via writable_schema.
        # Bump schema_version afterwards so SQLite reloads the table definition from disk —
        # without that bump, the current connection keeps enforcing the old NOT NULL from
        # its cached schema. Safe because row data is untouched and the replace() is a
        # no-op if the constraint has already been removed.
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"))
            users_sql = result.scalar()
            if users_sql and "password_hash VARCHAR(255) NOT NULL" in users_sql:
                version_result = await conn.execute(text("PRAGMA schema_version"))
                schema_version = version_result.scalar() or 0
                await conn.execute(text("PRAGMA writable_schema = ON"))
                await conn.execute(
                    text(
                        "UPDATE sqlite_master "
                        "SET sql = replace(sql, 'password_hash VARCHAR(255) NOT NULL', 'password_hash VARCHAR(255)') "
                        "WHERE type = 'table' AND name = 'users'"
                    )
                )
                await conn.execute(text(f"PRAGMA schema_version = {schema_version + 1}"))
                await conn.execute(text("PRAGMA writable_schema = OFF"))
        except (OperationalError, ProgrammingError) as exc:
            logger.error(
                "Failed to remove NOT NULL from users.password_hash via writable_schema — "
                "OIDC/LDAP user creation will fail on this install: %s",
                exc,
                exc_info=True,
            )
    else:
        await _safe_execute(conn, "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL")

    # Migration: Add energy_start_kwh to print_archives (#941)
    # Persists the smart plug lifetime counter captured at print start, so per-print
    # energy tracking survives a backend restart mid-print.
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN energy_start_kwh REAL")

    # Migration: Add subtask_id to print_archives (#972)
    # MQTT-provided task identifier used to resume the same archive row across a
    # backend restart mid-print. Without it, a long print (e.g. 13h) triggers
    # stale-cancel + new-archive, losing started_at continuity.
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN subtask_id VARCHAR(64)")

    # Migration: Add bed_type to print_archives (#1253)
    # Build plate type extracted from 3MF (curr_bed_type), drives the bed icon
    # rendered on archive cards.
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN bed_type VARCHAR(64)")

    # Migration: Add deleted_at to print_archives (#1343)
    # Soft-delete sentinel so deleting an archive entry from the UI no longer
    # wipes its filament / time / cost contribution from Quick Stats. Listings
    # hide rows where deleted_at IS NOT NULL; the stats endpoint counts them all.
    # DATETIME on SQLite, TIMESTAMP on PostgreSQL (PG doesn't accept DATETIME on
    # ALTER TABLE the same way it tolerates it inside CREATE TABLE).
    _deleted_at_type = "DATETIME" if is_sqlite() else "TIMESTAMP"
    await _safe_execute(conn, f"ALTER TABLE print_archives ADD COLUMN deleted_at {_deleted_at_type}")
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_print_archives_deleted_at ON print_archives (deleted_at)",
    )

    # Migration: Add bambuddy_forced_timelapse to print_archives (#1397)
    # Tracks prints where Bambuddy forced the firmware to record a timelapse
    # so the finish-photo extractor could pull the post-park-pre-drop frame.
    # The cleanup path uses this to delete the timelapse both locally and on
    # the printer's SD after extraction — the user didn't opt in to a
    # timelapse recording. Postgres rejects `DEFAULT 0` for BOOLEAN; SQLite
    # accepts both 0/FALSE — branch the literal.
    _bool_false_literal = "0" if is_sqlite() else "FALSE"
    await _safe_execute(
        conn,
        f"ALTER TABLE print_archives ADD COLUMN bambuddy_forced_timelapse BOOLEAN DEFAULT {_bool_false_literal}",
    )

    # Migration: Create smart_plug_energy_snapshots table (#941)
    # Hourly snapshots of each plug's lifetime counter, so date-range queries in
    # "total consumption" energy mode can compute (last - first) deltas.
    await _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS smart_plug_energy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plug_id INTEGER NOT NULL REFERENCES smart_plugs(id) ON DELETE CASCADE,
            recorded_at DATETIME NOT NULL,
            lifetime_kwh REAL NOT NULL
        )
        """
        if is_sqlite()
        else """
        CREATE TABLE IF NOT EXISTS smart_plug_energy_snapshots (
            id SERIAL PRIMARY KEY,
            plug_id INTEGER NOT NULL REFERENCES smart_plugs(id) ON DELETE CASCADE,
            recorded_at TIMESTAMP NOT NULL,
            lifetime_kwh REAL NOT NULL
        )
        """,
    )
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_plug_energy_snapshots_plug_time "
        "ON smart_plug_energy_snapshots(plug_id, recorded_at)",
    )

    # Migration: Add PKCE code_verifier column to auth_ephemeral_tokens
    await _safe_execute(conn, "ALTER TABLE auth_ephemeral_tokens ADD COLUMN code_verifier VARCHAR(128)")

    # Migration: Add TOTP replay-protection counter to user_totp
    await _safe_execute(conn, "ALTER TABLE user_totp ADD COLUMN last_totp_counter BIGINT")

    # Migration: Add challenge_id for pre-auth token client binding (HttpOnly cookie)
    await _safe_execute(conn, "ALTER TABLE auth_ephemeral_tokens ADD COLUMN challenge_id VARCHAR(128)")

    # Migration: Add auto_link_existing_accounts column to oidc_providers (M-4)
    # Postgres rejects `DEFAULT 0` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN auto_link_existing_accounts BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(
            conn, "ALTER TABLE oidc_providers ADD COLUMN auto_link_existing_accounts BOOLEAN DEFAULT false"
        )

    # Migration: Azure Entra ID support — configurable email claim and verification requirement
    await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN email_claim VARCHAR(64) DEFAULT 'email'")
    # Postgres rejects `DEFAULT 1` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN require_email_verified BOOLEAN DEFAULT 1")
    else:
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN require_email_verified BOOLEAN DEFAULT true")
    # SEC-1 backfill: reset auto_link only for Fall B (email_claim='email' + require_email_verified=False).
    # Fall C (custom claim) is now allowed to use auto_link — do NOT reset those rows.
    # Runs BEFORE the CHECK constraint below so Fall B rows self-heal rather than failing
    # PostgreSQL's "check constraint is violated by some row" on ADD CONSTRAINT.
    # On fresh installs the column defaults guarantee this UPDATE matches zero rows.
    # TRUE/FALSE literals are accepted by both SQLite (≥ 3.23) and PostgreSQL — no dialect branch needed.
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE oidc_providers SET auto_link_existing_accounts = FALSE "
                    "WHERE auto_link_existing_accounts = TRUE "
                    "AND email_claim = 'email' AND require_email_verified = FALSE"
                )
            )
    except Exception as exc:
        logger.error(
            "SEC-1 safety backfill FAILED — auto_link_existing_accounts may remain enabled "
            "on providers with unsafe email settings: %s",
            exc,
            exc_info=True,
        )
        raise

    # SEC-1: Add DB-level CHECK constraint for existing PostgreSQL installs.
    # SQLite does not support ALTER TABLE ADD CONSTRAINT — handled by __table_args__ at creation.
    # Runs AFTER the backfill so Fall B rows don't fail constraint validation.
    if not is_sqlite():
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE oidc_providers ADD CONSTRAINT ck_auto_link_requires_verified_email_claim "
                        "CHECK (auto_link_existing_accounts = FALSE OR email_claim != 'email' OR require_email_verified = TRUE)"
                    )
                )
        except (OperationalError, ProgrammingError) as exc:
            msg = str(exc).lower()
            if "already exists" not in msg:
                logger.error(
                    "Security constraint migration FAILED — auto_link safety constraint may not be enforced: %s",
                    exc,
                    exc_info=True,
                )
                raise

    # Migration: Update auto_link CHECK constraint formula (existing installs).
    # Existing PostgreSQL installs that ran the ADD CONSTRAINT above with the old formula
    # (or a previous version of this code) need an explicit DROP + ADD to update it.
    # For SQLite, the table is recreated with the new constraint formula if the old formula
    # is still present in sqlite_master (SQLite cannot ALTER TABLE DROP/ADD CONSTRAINT).
    await _migrate_update_auto_link_constraint(conn)

    # Migration: Add default_group_id to oidc_providers.
    # Must run AFTER _migrate_update_auto_link_constraint to avoid being dropped during
    # the SQLite table recreation that function performs on stale-formula databases.
    await _safe_execute(
        conn,
        "ALTER TABLE oidc_providers ADD COLUMN default_group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL",
    )

    # Migration: Add cached-icon columns to oidc_providers (#1333).
    # SPA's strict CSP (img-src 'self' data: blob:) blocks hotlinking external
    # icon hosts, so we proxy them: admin sets icon_url, backend fetches and
    # caches the bytes here, the SPA renders <img src="/api/v1/auth/oidc/providers/{id}/icon">.
    # Must run AFTER _migrate_update_auto_link_constraint for the same reason as
    # default_group_id above (SQLite table recreation drops unknown columns).
    # Dialect-conditional type: BLOB on SQLite, BYTEA on PostgreSQL.
    _blob_type = "BLOB" if is_sqlite() else "BYTEA"
    await _safe_execute(conn, f"ALTER TABLE oidc_providers ADD COLUMN icon_data {_blob_type}")
    await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN icon_content_type VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN icon_etag VARCHAR(64)")

    # PostgreSQL-only: enforce the all-or-nothing triplet at the DB layer.
    # SQLite cannot ADD CONSTRAINT to an existing table — fresh SQLite
    # installs get the CHECK via metadata.create_all (model __table_args__);
    # stale SQLite installs rely on the application layer, same trade-off
    # as the default_group_id FK ON DELETE SET NULL above.
    if not is_sqlite():
        await _safe_execute(
            conn,
            "ALTER TABLE oidc_providers ADD CONSTRAINT ck_oidc_icon_triplet_co_null "
            "CHECK ((icon_data IS NULL) = (icon_content_type IS NULL) "
            "AND (icon_content_type IS NULL) = (icon_etag IS NULL))",
        )

    # Migration: Add password_changed_at to users (M-R7-B)
    # Tracks the last time a user's password was changed/reset.  JWTs whose iat
    # predates this timestamp are rejected in all six auth validation paths.
    # R4 fix: TIMESTAMP is accepted by both SQLite and PostgreSQL; DATETIME
    # is rejected by Postgres ("type 'datetime' does not exist"), which made
    # _safe_execute swallow the error and leave existing Postgres installs
    # without the column — causing UndefinedColumnError on every User query.
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMP")

    # Migration: Back-fill password_changed_at = created_at for existing users (I2).
    # Users who never changed their password would have NULL here, meaning old
    # tokens could never be invalidated via the freshness check.  Setting it to
    # created_at is conservative: any token issued before the account was created
    # is always invalid, so this is a safe lower bound.
    async with conn.begin_nested():
        await conn.execute(text("UPDATE users SET password_changed_at = created_at WHERE password_changed_at IS NULL"))

    # Migration: Provenance columns on library_files for MakerWorld imports.
    # source_url is indexed so "already imported" dedupe lookups stay O(log N)
    # as the library grows.
    await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN source_type VARCHAR(32)")
    await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN source_url VARCHAR(512)")
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_library_files_source_url ON library_files(source_url)",
    )

    # Migration: Cache metadata title on pending uploads (#1152 follow-up).
    # Without this column the review card always shows the FTP filename while
    # the eventual archive's print_name comes from the 3MF metadata title,
    # creating a confusing review→archive name mismatch. Captured at upload
    # time so /pending-uploads/ list calls don't have to reopen each 3MF.
    await _safe_execute(
        conn,
        "ALTER TABLE pending_uploads ADD COLUMN metadata_print_name VARCHAR(255)",
    )

    # Migration: Per-user API key ownership + cloud-access scope (#1182).
    # user_id is nullable so legacy keys (created before #1182) survive the
    # migration; cloud routes reject calls from keys without an owner so the
    # operator is forced to recreate them. ON DELETE CASCADE so deleting a user
    # takes their keys with them — orphan keys must never authenticate.
    # SQLite ignores REFERENCES on ADD COLUMN (not enforced but not an error);
    # PostgreSQL enforces the FK from this point forward. Indexed for the
    # auth-gate's owner→keys lookup that runs on every API-keyed request.
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
    )
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys(user_id)",
    )
    # ``DEFAULT 0`` works on SQLite (boolean is just integer-coerced) but
    # asyncpg's strict type-check rejects it: "column is of type boolean but
    # default expression is of type integer". Use ``DEFAULT FALSE`` so both
    # dialects accept the same statement — same pattern as the print_queue
    # gcode_injection migration above.
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN can_access_cloud BOOLEAN DEFAULT FALSE",
    )
    # Narrowly-scoped settings-write toggle for the dynamic-tariff push case
    # documented in wiki/features/energy.md (#1356). Defaults FALSE so existing
    # keys never silently gain settings-write capability on upgrade.
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN can_update_energy_cost BOOLEAN DEFAULT FALSE",
    )

    # GHSA-r2qv-8222-hqg3 (CVE-2026-pending, CVSS 9.9): split file-management out
    # of the implicit "any API key" grant into an explicit scope flag. The
    # allowlist-based ``_check_apikey_permissions`` (see ``core/auth.py``) routes
    # LIBRARY_UPLOAD / LIBRARY_UPDATE_OWN / LIBRARY_DELETE_OWN / MAKERWORLD_IMPORT
    # through this flag. DEFAULT TRUE matches the existing "queue + read" trust
    # baseline; backfill mirrors can_queue so a key the user previously created as
    # "queue-only" retains the file-upload step its queue workflow already used,
    # while a hardened "read-only" key (can_queue=False) does not silently gain a
    # new write capability on upgrade. Backfill is gated on column non-existence
    # so user-edited values are never overwritten on subsequent startup.
    column_existed = await _api_keys_column_exists(conn, "can_manage_library")
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN can_manage_library BOOLEAN DEFAULT TRUE",
    )
    if not column_existed:
        async with conn.begin_nested():
            await conn.execute(text("UPDATE api_keys SET can_manage_library = can_queue"))

    # Same shape: SpoolBuddy NFC/scale/system endpoints plus manual inventory
    # writes split out of the implicit "any API key" grant. Backfill mirrors
    # ``can_queue`` so the bundled SpoolBuddy kiosk key (created via the CLI
    # with can_queue=False) does NOT silently gain inventory writes — but
    # the CLI override sets the new flag True explicitly, since the kiosk
    # itself is the legitimate writer (see ``cli.py``).
    column_existed = await _api_keys_column_exists(conn, "can_manage_inventory")
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN can_manage_inventory BOOLEAN DEFAULT TRUE",
    )
    if not column_existed:
        async with conn.begin_nested():
            await conn.execute(text("UPDATE api_keys SET can_manage_inventory = can_queue"))

    # #1832 follow-up: carve maintenance CRUD out of the admin denylist so
    # HA-style automations can log "cleaned nozzle" via API key. Distinct
    # from the two backfills above: MAINTENANCE_CREATE / _UPDATE / _DELETE
    # were EXPLICITLY denied for every API key under the pre-migration model
    # (they were on ``_APIKEY_DENIED_PERMISSIONS``), so no existing
    # integration relies on them. Column default TRUE matches the "safe,
    # on-by-default" pattern for keys created via the UI going forward;
    # existing rows backfill to FALSE so the upgrade path does not silently
    # widen scope for keys created before this flag existed. Users opt in
    # via Settings → API Keys per key.
    column_existed = await _api_keys_column_exists(conn, "can_manage_maintenance")
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN can_manage_maintenance BOOLEAN DEFAULT TRUE",
    )
    if not column_existed:
        async with conn.begin_nested():
            await conn.execute(text("UPDATE api_keys SET can_manage_maintenance = FALSE"))

    # #1888: carve archive CRUD (create/update/delete — NOT purge) out of the
    # admin denylist so automations can prune old prints via API key. Same
    # shape and reasoning as can_manage_maintenance above: ARCHIVES_CREATE /
    # _UPDATE_* / _DELETE_* were EXPLICITLY denied for every API key under the
    # pre-migration model (they were on ``_APIKEY_DENIED_PERMISSIONS``), so no
    # existing integration relies on them. Column default TRUE for keys created
    # via the UI going forward; existing rows backfill to FALSE so the upgrade
    # path does not silently widen scope for keys created before this flag
    # existed. Users opt in via Settings → API Keys per key. BOOLEAN is valid
    # on both SQLite and Postgres, so no dialect branch is needed.
    column_existed = await _api_keys_column_exists(conn, "can_manage_archives")
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN can_manage_archives BOOLEAN DEFAULT TRUE",
    )
    if not column_existed:
        async with conn.begin_nested():
            await conn.execute(text("UPDATE api_keys SET can_manage_archives = FALSE"))

    # #1893: carve project CRUD + membership (create/update/delete, add-archives)
    # out of the admin denylist so automations can manage projects via API key.
    # Identical shape and reasoning to can_manage_archives above: PROJECTS_CREATE
    # / _UPDATE / _DELETE were EXPLICITLY denied for every API key under the
    # pre-migration model (they were on ``_APIKEY_DENIED_PERMISSIONS``), so no
    # existing integration relies on them. Column default TRUE for keys created
    # via the UI going forward; existing rows backfill to FALSE so the upgrade
    # path does not silently widen scope for keys created before this flag
    # existed. Users opt in via Settings → API Keys per key. BOOLEAN is valid on
    # both SQLite and Postgres, so no dialect branch is needed.
    column_existed = await _api_keys_column_exists(conn, "can_manage_projects")
    await _safe_execute(
        conn,
        "ALTER TABLE api_keys ADD COLUMN can_manage_projects BOOLEAN DEFAULT TRUE",
    )
    if not column_existed:
        async with conn.begin_nested():
            await conn.execute(text("UPDATE api_keys SET can_manage_projects = FALSE"))

    # Migration: Soft-delete column for trash bin (Issue #1008). Indexed so the
    # sweeper's "SELECT ... WHERE deleted_at < cutoff" and the trash list's
    # "WHERE deleted_at IS NOT NULL" stay cheap as the table grows.
    #
    # ``DATETIME`` is a SQLite-only type alias — PostgreSQL rejects it as
    # invalid syntax, _safe_execute swallows the error, and the column is
    # never added (breaking every query that references it). Emit
    # dialect-appropriate SQL so both backends get the column.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN deleted_at DATETIME")
    else:
        await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN deleted_at TIMESTAMP")
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_library_files_deleted_at ON library_files(deleted_at)",
    )

    # Legacy SQLite installs created `settings` without a UNIQUE constraint on `key`,
    # so `INSERT OR IGNORE` below silently degrades to a plain INSERT and dupes rows on
    # every restart. Dedupe (keep lowest id per key) and add the missing unique index
    # before seeding. Safe/idempotent on both dialects — fresh installs already have
    # no dupes and `create_all` already emits the index.
    async with conn.begin_nested():
        await conn.execute(text("DELETE FROM settings WHERE id NOT IN (SELECT MIN(id) FROM settings GROUP BY key)"))
    await _safe_execute(conn, "CREATE UNIQUE INDEX IF NOT EXISTS ix_settings_key ON settings(key)")

    # Migration: Normalise provider_email to lowercase (SEC-3).
    # Required for Entra ID where UPN/email claims may arrive in mixed case.
    # LOWER() is supported by both SQLite and PostgreSQL; the UPDATE is idempotent.
    # Executed directly (not via _safe_execute) so any column-reference failure
    # is always fatal and never silently swallowed.
    async with conn.begin_nested():
        await conn.execute(
            text(
                "UPDATE user_oidc_links SET provider_email = LOWER(provider_email) "
                "WHERE provider_email IS NOT NULL AND provider_email != LOWER(provider_email)"
            )
        )

    # Migration: Create spoolman_slot_assignments table for local AMS-slot→Spoolman-spool mapping.
    # Replaces the pattern of writing spool.location in Spoolman (which polluted the
    # user-editable storage_location field in the UI).
    # ck_ams_id_range formula was widened in #1274 to admit AMS-HT (ams_id 128-191).
    await _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS spoolman_slot_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            ams_id INTEGER NOT NULL CHECK ((ams_id >= 0 AND ams_id <= 7) OR (ams_id >= 128 AND ams_id <= 191) OR ams_id = 255),
            tray_id INTEGER NOT NULL CHECK (tray_id >= 0 AND tray_id <= 3),
            spoolman_spool_id INTEGER NOT NULL,
            assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_slot_assignment UNIQUE(printer_id, ams_id, tray_id)
        )
        """
        if is_sqlite()
        else """
        CREATE TABLE IF NOT EXISTS spoolman_slot_assignments (
            id SERIAL PRIMARY KEY,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            ams_id INTEGER NOT NULL CHECK ((ams_id >= 0 AND ams_id <= 7) OR (ams_id >= 128 AND ams_id <= 191) OR ams_id = 255),
            tray_id INTEGER NOT NULL CHECK (tray_id >= 0 AND tray_id <= 3),
            spoolman_spool_id INTEGER NOT NULL,
            assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_slot_assignment UNIQUE(printer_id, ams_id, tray_id)
        )
        """,
    )
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_slot_assignment_spool ON spoolman_slot_assignments (spoolman_spool_id)",
    )

    # Migration: widen ck_ams_id_range on spoolman_slot_assignments to allow
    # AMS-HT ids (128-191). Existing installs created before #1274 carry the
    # stale formula which rejects every AMS-HT slot link with a CHECK violation.
    await _migrate_widen_spoolman_slot_ams_id_range(conn)

    # Migration: Create spoolman_k_profile table for K-value calibration profiles linked to Spoolman spools.
    await _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS spoolman_k_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spoolman_spool_id INTEGER NOT NULL,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            extruder INTEGER NOT NULL DEFAULT 0 CHECK (extruder >= 0 AND extruder <= 1),
            nozzle_diameter VARCHAR(10) NOT NULL DEFAULT '0.4',
            nozzle_type VARCHAR(50),
            k_value REAL NOT NULL,
            name VARCHAR(100),
            cali_idx INTEGER,
            setting_id VARCHAR(50),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_spoolman_k_profile UNIQUE(spoolman_spool_id, printer_id, extruder, nozzle_diameter)
        )
        """
        if is_sqlite()
        else """
        CREATE TABLE IF NOT EXISTS spoolman_k_profile (
            id SERIAL PRIMARY KEY,
            spoolman_spool_id INTEGER NOT NULL,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            extruder INTEGER NOT NULL DEFAULT 0 CHECK (extruder >= 0 AND extruder <= 1),
            nozzle_diameter VARCHAR(10) NOT NULL DEFAULT '0.4',
            nozzle_type VARCHAR(50),
            k_value DOUBLE PRECISION NOT NULL,
            name VARCHAR(100),
            cali_idx INTEGER,
            setting_id VARCHAR(50),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_spoolman_k_profile UNIQUE(spoolman_spool_id, printer_id, extruder, nozzle_diameter)
        )
        """,
    )
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_spoolman_k_profile_spool ON spoolman_k_profile (spoolman_spool_id)",
    )

    # Migration: Add provider column to github_backup_config for multi-provider support
    await _safe_execute(conn, "ALTER TABLE github_backup_config ADD COLUMN provider VARCHAR(30) DEFAULT 'github'")

    # Migration: Add allow_insecure_http column to github_backup_config for self-hosted HTTP instances
    await _safe_execute(conn, "ALTER TABLE github_backup_config ADD COLUMN allow_insecure_http BOOLEAN DEFAULT FALSE")

    # Seed default settings keys that must exist on fresh install
    default_settings = [
        ("advanced_auth_enabled", "false"),
        ("smtp_auth_enabled", "true"),
    ]
    for key, value in default_settings:
        try:
            if is_sqlite():
                await conn.execute(
                    text("INSERT OR IGNORE INTO settings (key, value) VALUES (:key, :value)"),
                    {"key": key, "value": value},
                )
            else:
                await conn.execute(
                    text("INSERT INTO settings (key, value) VALUES (:key, :value) ON CONFLICT (key) DO NOTHING"),
                    {"key": key, "value": value},
                )
        except (OperationalError, ProgrammingError):
            pass

    # Migration: Create filament_sku_settings table for reorder forecasting
    if is_sqlite():
        await _safe_execute(
            conn,
            """CREATE TABLE IF NOT EXISTS filament_sku_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material VARCHAR(50) NOT NULL,
                subtype VARCHAR(50),
                brand VARCHAR(100),
                lead_time_days INTEGER NOT NULL DEFAULT 0,
                safety_margin_value INTEGER NOT NULL DEFAULT 14,
                safety_margin_unit VARCHAR(10) NOT NULL DEFAULT 'days',
                color_name VARCHAR(100),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (material, subtype, brand, color_name)
            )""",
        )
        async with conn.begin_nested():
            await conn.execute(text("UPDATE filament_sku_settings SET lead_time_days = 0 WHERE lead_time_days = 7"))
        await _safe_execute(
            conn, "ALTER TABLE filament_sku_settings ADD COLUMN safety_margin_value INTEGER NOT NULL DEFAULT 14"
        )
        await _safe_execute(
            conn, "ALTER TABLE filament_sku_settings ADD COLUMN safety_margin_unit VARCHAR(10) NOT NULL DEFAULT 'days'"
        )
        await _safe_execute(
            conn, "ALTER TABLE filament_sku_settings ADD COLUMN alerts_snoozed BOOLEAN NOT NULL DEFAULT 0"
        )
        # Migration: add color_name to filament_sku_settings so forecasts
        # distinguish colours within a SKU. The matching ALTER for
        # filament_shopping_list runs AFTER that table's CREATE below — on
        # fresh installs the table doesn't exist yet at this point and
        # _safe_execute does not swallow "no such table".
        await _safe_execute(conn, "ALTER TABLE filament_sku_settings ADD COLUMN color_name VARCHAR(100)")
        # Backfill and drop legacy safety_margin_days column — SQLite requires a table rebuild.
        # Only run if the stale column still exists.
        cols_result = await conn.execute(text("PRAGMA table_info(filament_sku_settings)"))
        col_names = [row[1] for row in cols_result.fetchall()]
        if "safety_margin_days" in col_names:
            async with conn.begin_nested():
                # Defensive: a previous startup may have crashed mid-rebuild leaving
                # filament_sku_settings_new behind, which would break the CREATE below.
                await conn.execute(text("DROP TABLE IF EXISTS filament_sku_settings_new"))
                await conn.execute(
                    text(
                        "UPDATE filament_sku_settings SET safety_margin_value = safety_margin_days "
                        "WHERE safety_margin_value = 14 AND safety_margin_days != 14"
                    )
                )
                await conn.execute(
                    text(
                        """CREATE TABLE filament_sku_settings_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        material VARCHAR(50) NOT NULL,
                        subtype VARCHAR(50),
                        brand VARCHAR(100),
                        color_name VARCHAR(100),
                        lead_time_days INTEGER NOT NULL DEFAULT 0,
                        safety_margin_value INTEGER NOT NULL DEFAULT 14,
                        safety_margin_unit VARCHAR(10) NOT NULL DEFAULT 'days',
                        alerts_snoozed BOOLEAN NOT NULL DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (material, subtype, brand, color_name)
                    )"""
                    )
                )
                await conn.execute(
                    text(
                        """INSERT INTO filament_sku_settings_new
                        (id, material, subtype, brand, color_name, lead_time_days, safety_margin_value,
                         safety_margin_unit, alerts_snoozed, created_at, updated_at)
                       SELECT id, material, subtype, brand, color_name, lead_time_days, safety_margin_value,
                              safety_margin_unit, COALESCE(alerts_snoozed, 0), created_at, updated_at
                       FROM filament_sku_settings"""
                    )
                )
                await conn.execute(text("DROP TABLE filament_sku_settings"))
                await conn.execute(text("ALTER TABLE filament_sku_settings_new RENAME TO filament_sku_settings"))
        # Widen the unique key to include color_name on pre-existing tables. The
        # auto-created UNIQUE index still covers only (material, subtype, brand)
        # after the ADD COLUMN above, so rebuild the table to refresh it (#forecast
        # -color-grouping). Detected by inspecting the index columns; skipped once
        # color_name is already part of the key.
        idx_rows = await conn.execute(text("PRAGMA index_list(filament_sku_settings)"))
        needs_uq_rebuild = False
        for idx in idx_rows.fetchall():
            if idx[3] != "u":  # origin col: 'u' = UNIQUE constraint, 'c' = CREATE INDEX, 'pk' = primary key
                continue
            info = await conn.execute(text(f"PRAGMA index_info({idx[1]})"))
            cols = {row[2] for row in info.fetchall()}
            if "material" in cols and "color_name" not in cols:
                needs_uq_rebuild = True
                break
        if needs_uq_rebuild:
            async with conn.begin_nested():
                await conn.execute(text("DROP TABLE IF EXISTS filament_sku_settings_uqfix"))
                await conn.execute(
                    text(
                        """CREATE TABLE filament_sku_settings_uqfix (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        material VARCHAR(50) NOT NULL,
                        subtype VARCHAR(50),
                        brand VARCHAR(100),
                        color_name VARCHAR(100),
                        lead_time_days INTEGER NOT NULL DEFAULT 0,
                        safety_margin_value INTEGER NOT NULL DEFAULT 14,
                        safety_margin_unit VARCHAR(10) NOT NULL DEFAULT 'days',
                        alerts_snoozed BOOLEAN NOT NULL DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (material, subtype, brand, color_name)
                    )"""
                    )
                )
                await conn.execute(
                    text(
                        """INSERT INTO filament_sku_settings_uqfix
                        (id, material, subtype, brand, color_name, lead_time_days, safety_margin_value,
                         safety_margin_unit, alerts_snoozed, created_at, updated_at)
                       SELECT id, material, subtype, brand, color_name, lead_time_days, safety_margin_value,
                              safety_margin_unit, COALESCE(alerts_snoozed, 0), created_at, updated_at
                       FROM filament_sku_settings"""
                    )
                )
                await conn.execute(text("DROP TABLE filament_sku_settings"))
                await conn.execute(text("ALTER TABLE filament_sku_settings_uqfix RENAME TO filament_sku_settings"))
        await _safe_execute(
            conn,
            """CREATE TABLE IF NOT EXISTS filament_shopping_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material VARCHAR(50) NOT NULL,
                subtype VARCHAR(50),
                brand VARCHAR(100),
                color_name VARCHAR(100),
                quantity_spools INTEGER NOT NULL DEFAULT 1,
                note VARCHAR(500),
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                purchased_at DATETIME,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
        )
        # Backfill color_name on pre-#1814 upgrades — the CREATE above already
        # has it for fresh installs; the ALTER is the upgrade path. "duplicate
        # column name" is swallowed by _safe_execute, so re-runs are no-ops.
        await _safe_execute(conn, "ALTER TABLE filament_shopping_list ADD COLUMN color_name VARCHAR(100)")
        # SQLite has no implicit updated_at trigger — add one so the column stays current.
        await _safe_execute(
            conn,
            """CREATE TRIGGER IF NOT EXISTS trg_filament_sku_settings_updated_at
               AFTER UPDATE ON filament_sku_settings FOR EACH ROW
               BEGIN
                 UPDATE filament_sku_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
               END""",
        )
    else:
        await _safe_execute(
            conn,
            """CREATE TABLE IF NOT EXISTS filament_sku_settings (
                id SERIAL PRIMARY KEY,
                material VARCHAR(50) NOT NULL,
                subtype VARCHAR(50),
                brand VARCHAR(100),
                lead_time_days INTEGER NOT NULL DEFAULT 0,
                safety_margin_value INTEGER NOT NULL DEFAULT 14,
                safety_margin_unit VARCHAR(10) NOT NULL DEFAULT 'days',
                color_name VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (material, subtype, brand, color_name)
            )""",
        )
        async with conn.begin_nested():
            await conn.execute(text("UPDATE filament_sku_settings SET lead_time_days = 0 WHERE lead_time_days = 7"))
        await _safe_execute(
            conn,
            "ALTER TABLE filament_sku_settings ADD COLUMN IF NOT EXISTS safety_margin_value INTEGER NOT NULL DEFAULT 14",
        )
        await _safe_execute(
            conn,
            "ALTER TABLE filament_sku_settings ADD COLUMN IF NOT EXISTS safety_margin_unit VARCHAR(10) NOT NULL DEFAULT 'days'",
        )
        await _safe_execute(
            conn,
            "ALTER TABLE filament_sku_settings ADD COLUMN IF NOT EXISTS alerts_snoozed BOOLEAN NOT NULL DEFAULT FALSE",
        )
        # Migration: add color_name to filament_sku_settings and widen the
        # unique key to include it so forecasts distinguish colours within a
        # SKU (#forecast-color-grouping). The matching ALTER for
        # filament_shopping_list runs AFTER that table's CREATE below — on
        # fresh installs the table doesn't exist yet at this point.
        await _safe_execute(conn, "ALTER TABLE filament_sku_settings ADD COLUMN IF NOT EXISTS color_name VARCHAR(100)")
        # Widen UNIQUE (material, subtype, brand) → (material, subtype, brand, color_name).
        # The original constraint was declared with name="uq_filament_sku" in the
        # model, so we drop/re-add by that name. Gated on a pg_constraint lookup so
        # the rebuild only runs when color_name is missing from the key — without
        # the gate, every startup would take an ACCESS EXCLUSIVE lock on the table
        # and churn the constraint.
        uq_check = await conn.execute(
            text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                "WHERE c.conname = 'uq_filament_sku' AND a.attname = 'color_name' LIMIT 1"
            )
        )
        if uq_check.scalar_one_or_none() is None:
            await _safe_execute(
                conn,
                "ALTER TABLE filament_sku_settings DROP CONSTRAINT IF EXISTS uq_filament_sku",
            )
            await _safe_execute(
                conn,
                "ALTER TABLE filament_sku_settings ADD CONSTRAINT uq_filament_sku "
                "UNIQUE (material, subtype, brand, color_name)",
            )
        # Only backfill from safety_margin_days if that column still exists (PostgreSQL).
        col_check = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'filament_sku_settings' AND column_name = 'safety_margin_days'"
            )
        )
        if col_check.fetchone():
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "UPDATE filament_sku_settings SET safety_margin_value = safety_margin_days "
                        "WHERE safety_margin_value = 14 AND safety_margin_days != 14"
                    )
                )
        await _safe_execute(
            conn,
            """CREATE TABLE IF NOT EXISTS filament_shopping_list (
                id SERIAL PRIMARY KEY,
                material VARCHAR(50) NOT NULL,
                subtype VARCHAR(50),
                brand VARCHAR(100),
                color_name VARCHAR(100),
                quantity_spools INTEGER NOT NULL DEFAULT 1,
                note VARCHAR(500),
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                purchased_at TIMESTAMP,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        )
        await _safe_execute(
            conn,
            "ALTER TABLE filament_shopping_list ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending'",
        )
        await _safe_execute(conn, "ALTER TABLE filament_shopping_list ADD COLUMN IF NOT EXISTS purchased_at TIMESTAMP")
        # Backfill color_name on pre-#1814 upgrades — the CREATE above already
        # has it for fresh installs; the ALTER is the upgrade path.
        await _safe_execute(conn, "ALTER TABLE filament_shopping_list ADD COLUMN IF NOT EXISTS color_name VARCHAR(100)")

    # Migration: Add inventory stock alert columns to notification_providers.
    # Postgres rejects `DEFAULT 0` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(
            conn, "ALTER TABLE notification_providers ADD COLUMN on_stock_reorder_alert BOOLEAN DEFAULT 0"
        )
        await _safe_execute(
            conn, "ALTER TABLE notification_providers ADD COLUMN on_stock_break_alert BOOLEAN DEFAULT 0"
        )
    else:
        await _safe_execute(
            conn, "ALTER TABLE notification_providers ADD COLUMN on_stock_reorder_alert BOOLEAN DEFAULT false"
        )
        await _safe_execute(
            conn, "ALTER TABLE notification_providers ADD COLUMN on_stock_break_alert BOOLEAN DEFAULT false"
        )

    # Migration: Heal orphan auth-related rows left behind by user-delete
    # on SQLite. user_oidc_links, user_totp, user_otp_codes (introduced in
    # PR #933) and long_lived_tokens (PR #1108) all declare ON DELETE
    # CASCADE on user_id — both predate the explicit APIKey-cleanup
    # pattern in PR #1182. PostgreSQL enforces the cascade, but SQLite
    # ships with FK enforcement off, so rows pointing to a deleted user
    # persisted — blocking SSO re-login (the OIDC callback finds the
    # orphan link, fails to resolve the missing user, and falls through
    # to "account_inactive" instead of triggering auto_create), leaking
    # MFA secrets, and leaving camera-stream tokens whose secret_hash is
    # still verify()-able by lookup_prefix. See issue #1285 (#1295 review
    # extended the cleanup to long_lived_tokens). This migration is a
    # no-op on PostgreSQL and idempotent on SQLite.
    async with conn.begin_nested():
        oidc_result = await conn.execute(
            text("DELETE FROM user_oidc_links WHERE user_id NOT IN (SELECT id FROM users)")
        )
        totp_result = await conn.execute(text("DELETE FROM user_totp WHERE user_id NOT IN (SELECT id FROM users)"))
        otp_result = await conn.execute(text("DELETE FROM user_otp_codes WHERE user_id NOT IN (SELECT id FROM users)"))
        llt_result = await conn.execute(
            text("DELETE FROM long_lived_tokens WHERE user_id NOT IN (SELECT id FROM users)")
        )
    oidc_n = oidc_result.rowcount or 0
    totp_n = totp_result.rowcount or 0
    otp_n = otp_result.rowcount or 0
    llt_n = llt_result.rowcount or 0
    if oidc_n or totp_n or otp_n or llt_n:
        logger.info(
            "Cleaned up orphan auth rows: %d OIDC links, %d TOTP, %d OTP codes, %d long-lived tokens",
            oidc_n,
            totp_n,
            otp_n,
            llt_n,
        )

    # Migration: extend print_log_entries with archive_id, cost, energy, failure_reason,
    # created_by_id (#1378). Statistics queries shift from PrintArchive to PrintLogEntry
    # so reprints contribute new rows instead of overwriting the source archive's data.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN archive_id INTEGER")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN cost REAL")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN energy_kwh REAL")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN energy_cost REAL")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN failure_reason VARCHAR(100)")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN created_by_id INTEGER")
    else:
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN IF NOT EXISTS archive_id INTEGER")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN IF NOT EXISTS cost DOUBLE PRECISION")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN IF NOT EXISTS energy_kwh DOUBLE PRECISION")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN IF NOT EXISTS energy_cost DOUBLE PRECISION")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(100)")
        await _safe_execute(conn, "ALTER TABLE print_log_entries ADD COLUMN IF NOT EXISTS created_by_id INTEGER")
    await _safe_execute(
        conn, "CREATE INDEX IF NOT EXISTS ix_print_log_entries_archive_id ON print_log_entries (archive_id)"
    )

    # Backfill PrintLogEntry → PrintArchive linkage and per-event cost/energy
    # for pre-#1378 rows the column-add migration left NULL (#1390).
    #
    # Without this backfill the user's Quick Stats show Filament Cost = 0 and
    # Time Accuracy empty even though their archives carry both, because:
    #
    #   - the new stats queries SUM PrintLogEntry.cost (NULL for old rows)
    #   - the time-accuracy query JOINs PrintArchive ON archive_id (NULL for
    #     old rows, so old runs get excluded from the average)
    #
    # Pre-#1378, archive.cost / energy_kwh / energy_cost were overwritten by
    # each rerun, so the current archive values represent the *latest* run.
    # Backfilling them onto the latest matching PrintLogEntry per archive
    # reconstructs the pre-fix total exactly (sum across archives stays
    # unchanged), and leaves earlier reprints with NULL cost so they
    # contribute zero — matching the "first/latest writes, rest stay NULL"
    # convention #1378 introduced for new prints.
    #
    # DML, not DDL — use conn.execute() inside a savepoint per _safe_execute's
    # own docstring. SQL is plain ANSI (correlated UPDATE, MAX/GROUP BY/HAVING,
    # CASE in HAVING) and runs unchanged on SQLite + PostgreSQL; verified
    # against postgres:16-alpine + asyncpg.
    #
    # Step 1: link old log entries to their archive via print_name + printer_id.
    # Picks the highest-id matching archive when multiple share the same key
    # (newest archive wins — closest to the log's overwrite-then-leave shape).
    from sqlalchemy import text as _text

    async with conn.begin_nested():
        await conn.execute(
            _text("""
            UPDATE print_log_entries
            SET archive_id = (
                SELECT a.id
                FROM print_archives a
                WHERE a.print_name = print_log_entries.print_name
                  AND (
                      a.printer_id = print_log_entries.printer_id
                      OR (a.printer_id IS NULL AND print_log_entries.printer_id IS NULL)
                  )
                ORDER BY a.id DESC
                LIMIT 1
            )
            WHERE archive_id IS NULL AND print_name IS NOT NULL
            """)
        )

    # Step 2: backfill cost / energy_kwh / energy_cost onto the latest linked
    # log entry per archive — the row whose creation time best matches the
    # value currently stored on the archive (overwrite-on-reprint semantics
    # under the old design). Only fires for archives where NO log entry has
    # cost set yet, which gives the migration a clean idempotency property:
    # the second pass sees the archive already has a cost-bearing run and
    # leaves the rest of its history NULL (instead of marching up the
    # ID-ordered list of NULL runs on every pass).
    async with conn.begin_nested():
        await conn.execute(
            _text("""
            UPDATE print_log_entries
            SET cost = (SELECT cost FROM print_archives WHERE id = print_log_entries.archive_id),
                energy_kwh = (SELECT energy_kwh FROM print_archives WHERE id = print_log_entries.archive_id),
                energy_cost = (SELECT energy_cost FROM print_archives WHERE id = print_log_entries.archive_id)
            WHERE id IN (
                SELECT MAX(id)
                FROM print_log_entries
                WHERE archive_id IS NOT NULL
                GROUP BY archive_id
                HAVING SUM(CASE WHEN cost IS NOT NULL THEN 1 ELSE 0 END) = 0
            )
            """)
        )

    # Migration: smart_plugs gets per-plug auto-off-after-drying toggle and
    # delay (#1349). Fires whenever any AMS attached to the linked printer
    # finishes a dry cycle. Plain ANSI ALTER TABLE works on both SQLite and
    # Postgres for INTEGER/BOOLEAN with simple defaults.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_after_drying BOOLEAN DEFAULT 0")
        await _safe_execute(
            conn, "ALTER TABLE smart_plugs ADD COLUMN off_delay_after_drying_minutes INTEGER DEFAULT 10"
        )
    else:
        await _safe_execute(
            conn,
            "ALTER TABLE smart_plugs ADD COLUMN IF NOT EXISTS auto_off_after_drying BOOLEAN DEFAULT false",
        )
        await _safe_execute(
            conn,
            "ALTER TABLE smart_plugs ADD COLUMN IF NOT EXISTS off_delay_after_drying_minutes INTEGER DEFAULT 10",
        )

    # Migration: Add per-user Orca Cloud credential columns. Mirrors the Bambu
    # Cloud columns but adds refresh_token + expires_at (Supabase PKCE issues
    # short-lived access tokens with rotating refresh tokens), plus three
    # transient PKCE state columns held during the auth handshake. DATETIME
    # is SQLite-only — Postgres uses TIMESTAMP, so the datetime columns are
    # dialect-branched per project convention.
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_token VARCHAR(2000)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_refresh_token VARCHAR(128)")
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_expires_at DATETIME")
    else:
        await _safe_execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS orca_cloud_expires_at TIMESTAMP")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_email VARCHAR(255)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_user_id VARCHAR(64)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_pending_verifier VARCHAR(64)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_pending_state VARCHAR(32)")
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE users ADD COLUMN orca_cloud_pending_at DATETIME")
    else:
        await _safe_execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS orca_cloud_pending_at TIMESTAMP")

    # Migration: record when Bambu rejects a stored cloud token. Until now the
    # only state we kept was the token string itself, so a dead credential was
    # indistinguishable from a live one and the UI reported "connected" forever
    # while every cloud call 401'd. DATETIME is SQLite-only — Postgres uses
    # TIMESTAMP, so the column is dialect-branched per project convention.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE users ADD COLUMN cloud_token_invalid_at DATETIME")
    else:
        await _safe_execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS cloud_token_invalid_at TIMESTAMP")

    # Data migration: drop the embedded 3MF Title (`print_name`) from library
    # file metadata so the FileManager displays the filename, not the title (#1489).
    await _migrate_drop_library_print_name(conn)

    # Data migration: queue items written before #2551 carry every selected plate's
    # filament overrides, so a force-colour plate waits on colours it never prints.
    await _migrate_scope_force_color_overrides_to_plate(conn)

    # Backfill NULL print_archives.created_at — older rows (and rows imported
    # via the SQLite ↔ Postgres cross-DB restore path) can land with NULL
    # because the column was originally created without a DEFAULT clause and
    # server_default=func.now() only fires at table creation, not column
    # population. The list_archives response model requires a datetime, so a
    # single NULL row 500s the whole endpoint (#1732).
    async with conn.begin_nested():
        if is_sqlite():
            await conn.execute(
                text(
                    "UPDATE print_archives "
                    "SET created_at = COALESCE(completed_at, started_at, datetime('now')) "
                    "WHERE created_at IS NULL"
                )
            )
        else:
            await conn.execute(
                text(
                    "UPDATE print_archives "
                    "SET created_at = COALESCE(completed_at, started_at, NOW()) "
                    "WHERE created_at IS NULL"
                )
            )

    # Migration: structured storage locations (#1004). Flat catalog of physical
    # shelves/drawers; spool.location_id FK with storage_location kept denormalized.
    await _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL UNIQUE,
            name_key VARCHAR(255),
            identifier VARCHAR(100),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
        if is_sqlite()
        else """
        CREATE TABLE IF NOT EXISTS locations (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            name_key VARCHAR(255),
            identifier VARCHAR(100),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    await _safe_execute(conn, "ALTER TABLE locations ADD COLUMN name_key VARCHAR(255)")
    await _safe_execute(conn, "CREATE UNIQUE INDEX IF NOT EXISTS ix_locations_name_key ON locations (name_key)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN location_id INTEGER REFERENCES locations(id)")
    await _safe_execute(conn, "CREATE INDEX IF NOT EXISTS ix_spool_location_id ON spool (location_id)")

    # Backfill name_key on legacy rows FIRST. If a pre-existing locations
    # row was manually inserted before this migration ran, its name_key is
    # NULL. The dedup INSERT below would then be silently skipped by
    # UNIQUE(name) (legacy row already has the name), AND the spool-link
    # UPDATE that joins on name_key would miss it. Doing this backfill BEFORE
    # the INSERT keeps the join consistent on both branches of the migration.
    async with conn.begin_nested():
        await conn.execute(
            text(
                """
                UPDATE locations
                SET name_key = LOWER(TRIM(name))
                WHERE name_key IS NULL OR TRIM(name_key) = ''
                """
            )
        )

    # Backfill locations from existing free-text storage_location values.
    # GROUP BY name_key so case variants ("Drybox 1" / "DRYBOX 1") collapse to
    # one row; INSERT OR IGNORE / ON CONFLICT keeps the migration idempotent.
    _location_backfill_sql = (
        """
        INSERT OR IGNORE INTO locations (name, name_key, created_at, updated_at)
        SELECT MIN(TRIM(storage_location)), LOWER(TRIM(storage_location)), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM spool
        WHERE TRIM(COALESCE(storage_location, '')) != ''
        GROUP BY LOWER(TRIM(storage_location))
        """
        if is_sqlite()
        else """
        INSERT INTO locations (name, name_key, created_at, updated_at)
        SELECT MIN(TRIM(storage_location)), LOWER(TRIM(storage_location)), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM spool
        WHERE TRIM(COALESCE(storage_location, '')) != ''
        GROUP BY LOWER(TRIM(storage_location))
        ON CONFLICT (name_key) DO NOTHING
        """
    )
    async with conn.begin_nested():
        await conn.execute(text(_location_backfill_sql))
        await conn.execute(
            text(
                """
                UPDATE spool
                SET location_id = (
                    SELECT l.id FROM locations l
                    WHERE l.name_key = LOWER(TRIM(spool.storage_location))
                    LIMIT 1
                )
                WHERE TRIM(COALESCE(storage_location, '')) != ''
                  AND location_id IS NULL
                """
            )
        )

    # Sanity check: any spools that still have a free-text storage_location
    # but no location_id link mean a row slipped through the dedup INSERT
    # (most likely a pre-existing manually-inserted locations row with a
    # hostile name shape that the UNIQUE(name) check tripped on). Surface
    # the count so ops can investigate — the user won't see those spools in
    # location-filtered queries until they're manually linked or re-saved.
    orphan_count_row = await conn.execute(
        text("SELECT COUNT(*) FROM spool WHERE TRIM(COALESCE(storage_location, '')) != '' AND location_id IS NULL")
    )
    orphan_count = orphan_count_row.scalar() or 0
    if orphan_count:
        logger.warning(
            "Storage-location migration left %d spool(s) with free-text storage_location "
            "but no location_id link. Re-save those spools or merge the orphaned location "
            "names manually.",
            orphan_count,
        )

    # Migration: Add on_ai_failure_detection column to notification_providers (#1794).
    # Splits Obico AI failure detection out of the multiplexed on_printer_error
    # event so users can subscribe to spaghetti alerts independently of HMS
    # hardware-error alerts. Postgres rejects `DEFAULT 0` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(
            conn,
            "ALTER TABLE notification_providers ADD COLUMN on_ai_failure_detection BOOLEAN DEFAULT 0",
        )
    else:
        await _safe_execute(
            conn,
            "ALTER TABLE notification_providers ADD COLUMN on_ai_failure_detection BOOLEAN DEFAULT false",
        )

    # Migration: Add gate_acknowledged column to print_queue (#1818). Cleared
    # by the per-printer "Resume after failure" action so the scheduler's
    # `_check_previous_success` lookback skips this row. Postgres rejects
    # `DEFAULT 0` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN gate_acknowledged BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN gate_acknowledged BOOLEAN DEFAULT false")

    # Migration: Add is_autologin column to oidc_providers (#1589). Postgres
    # rejects ``DEFAULT 0`` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN is_autologin BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN is_autologin BOOLEAN DEFAULT false")

    # Migration: Add dispatch_attempts to print_queue (#2555). Counts the times
    # the start-watchdog reverted the row from 'printing' back to 'pending' so a
    # printer that never actually starts stops being retried forever. INTEGER
    # DEFAULT 0 is spelled identically on SQLite and Postgres — no dialect branch.
    # Verified on both dialects: ADD COLUMN ... DEFAULT 0 backfills existing rows,
    # so no separate UPDATE is needed (and _safe_execute is DDL-only — see its
    # docstring). The scheduler reads it as `(item.dispatch_attempts or 0) + 1`
    # regardless, so even a NULL row could not disable the retry cap.
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN dispatch_attempts INTEGER DEFAULT 0")

    # Backfill: copy the selected plate from linked queue rows onto their archives
    # (#2603). Recovers the plate for archives created before print_archives had a
    # plate_id column, wherever the queue row still points at the archive and
    # carries a plate. Runs here — after every print_queue column migration
    # (plate_id, archive_id) — because it reads print_queue.plate_id, which is
    # added far earlier in this function but must exist before this DML runs on a
    # first-ever migration pass. Correlated-subquery form so the DML is identical
    # on SQLite and Postgres; the WHERE plate_id IS NULL guard makes it idempotent
    # and keeps it from clobbering values set on later runs.
    async with conn.begin_nested():
        # Only do any work (and, on SQLite, the FTS rebuild below) when there is
        # actually a plate to recover — so this is a one-off cost on the upgrade
        # boot, not an every-boot tax once every archive is backfilled.
        has_work = (
            await conn.execute(
                text(
                    "SELECT 1 FROM print_archives a "
                    "JOIN print_queue q ON q.archive_id = a.id "
                    "WHERE a.plate_id IS NULL AND q.plate_id IS NOT NULL "
                    "LIMIT 1"
                )
            )
        ).first() is not None
        if has_work:
            # SQLite: print_archives has an external-content FTS index (archive_fts,
            # created above) whose AFTER UPDATE trigger issues an FTS 'delete' for
            # the row. Archives created before that table existed were never indexed
            # (its creation runs no rebuild), and updating an un-indexed row trips
            # "database disk image is malformed". plate_id isn't even an FTS column,
            # so the trigger's re-index is pointless here — but it still fires. Rebuild
            # the index from the content table first so every row is present and the
            # trigger's 'delete' is well-defined. Postgres has no such FTS table.
            if is_sqlite():
                await conn.execute(text("INSERT INTO archive_fts(archive_fts) VALUES('rebuild')"))
            await conn.execute(
                text(
                    "UPDATE print_archives "
                    "SET plate_id = ("
                    "  SELECT pq.plate_id FROM print_queue pq "
                    "  WHERE pq.archive_id = print_archives.id AND pq.plate_id IS NOT NULL "
                    "  LIMIT 1"
                    ") "
                    "WHERE plate_id IS NULL "
                    "AND EXISTS ("
                    "  SELECT 1 FROM print_queue pq "
                    "  WHERE pq.archive_id = print_archives.id AND pq.plate_id IS NOT NULL"
                    ")"
                )
            )

    # Migration: Disambiguate the four ``user_print_*`` notification template
    # names by appending " Email" (#1792). See ``_migrate_rename_user_print_template_names``.
    await _migrate_rename_user_print_template_names(conn)

    # Migration: Add barcode column to spool for scan-to-add lookups. Stores
    # the canonicalized (no leading zeros) UPC/EAN of a scanned spool so a
    # later scan of the same barcode resolves from the user's own inventory
    # before falling back to the Open Filament Database.
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN barcode VARCHAR(64)")
    await _safe_execute(conn, "CREATE INDEX IF NOT EXISTS ix_spool_barcode ON spool (barcode)")

    # Migration: backfill the new one-to-many spool_code table from the
    # existing single Spool.barcode column, so multi-code cross-referencing
    # (see _resolve_barcode in routes/inventory.py) has a starting point for
    # spools scanned before this feature shipped. Spool.barcode itself is
    # unchanged and stays the denormalized "primary code" column. The
    # spool_code table itself is created by Base.metadata.create_all() above
    # (new model, not an ALTER), so this only needs to backfill rows.
    await _migrate_backfill_spool_codes(conn)


async def _migrate_backfill_spool_codes(conn) -> None:
    """Backfill spool_code from every Spool.barcode set before this feature shipped.

    Classifies each barcode individually via classify_code instead of assuming
    'gtin' — Spool.barcode can hold an alphanumeric SKU (e.g. a manufacturer
    article number with no UPC/EAN counterpart), and a hardcoded kind would
    mis-classify those rows so they never match on a future scan.

    Also heals rows already backfilled (or written by the multi-code feature
    itself) whose kind disagrees with the current classification — covers
    spools created by an earlier, buggier build of this feature on this
    branch. Matching on (spool_id, code) is unique per the table's constraint,
    so this and the insert are both safe to re-run on every startup.
    """
    from sqlalchemy import text

    from backend.app.schemas.spool import classify_code

    async with conn.begin_nested():
        rows = (await conn.execute(text("SELECT id, barcode FROM spool WHERE barcode IS NOT NULL"))).all()
        for spool_id, barcode in rows:
            code, kind = classify_code(barcode)
            existing = (
                await conn.execute(
                    text("SELECT kind FROM spool_code WHERE spool_id = :spool_id AND code = :code"),
                    {"spool_id": spool_id, "code": code},
                )
            ).first()
            if existing is None:
                await conn.execute(
                    text(
                        "INSERT INTO spool_code (spool_id, code, kind, is_refill, is_primary) "
                        "VALUES (:spool_id, :code, :kind, false, true)"
                    ),
                    {"spool_id": spool_id, "code": code, "kind": kind},
                )
            elif existing[0] != kind:
                await conn.execute(
                    text("UPDATE spool_code SET kind = :kind WHERE spool_id = :spool_id AND code = :code"),
                    {"spool_id": spool_id, "code": code, "kind": kind},
                )


_USER_PRINT_TEMPLATE_RENAMES: tuple[tuple[str, str, str], ...] = (
    ("user_print_start", "User Print Started", "User Print Started Email"),
    ("user_print_complete", "User Print Completed", "User Print Completed Email"),
    ("user_print_failed", "User Print Failed", "User Print Failed Email"),
    ("user_print_stopped", "User Print Stopped", "User Print Stopped Email"),
)


async def _migrate_rename_user_print_template_names(conn) -> None:
    """Append " Email" to the four ``user_print_*`` notification template names (#1792).

    The provider-level "Print Completed" and the per-user "User Print Completed"
    rows were visually indistinguishable in the Message Templates list because
    the seed name lacked the suffix that the EVENT_NAMES display map in
    routes/notification_templates.py already uses ("User Print Completed Email").

    Renames only rows where ``name`` is still the old default — admins who
    renamed the template themselves keep their custom name. Standard SQL
    UPDATE works on both SQLite and Postgres.
    """
    from sqlalchemy import text

    async with conn.begin_nested():
        for event_type, old_name, new_name in _USER_PRINT_TEMPLATE_RENAMES:
            await conn.execute(
                text("UPDATE notification_templates SET name = :new WHERE event_type = :et AND name = :old"),
                {"new": new_name, "et": event_type, "old": old_name},
            )


async def seed_notification_templates():
    """Seed default notification templates if they don't exist."""
    from sqlalchemy import select

    from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate

    async with async_session() as session:
        # Get existing template event types
        result = await session.execute(select(NotificationTemplate.event_type))
        existing_types = {row[0] for row in result.fetchall()}

        if not existing_types:
            # No templates exist - insert all defaults
            for template_data in DEFAULT_TEMPLATES:
                template = NotificationTemplate(
                    event_type=template_data["event_type"],
                    name=template_data["name"],
                    title_template=template_data["title_template"],
                    body_template=template_data["body_template"],
                    is_default=True,
                )
                session.add(template)
        else:
            # Templates exist - only add missing ones
            for template_data in DEFAULT_TEMPLATES:
                if template_data["event_type"] not in existing_types:
                    template = NotificationTemplate(
                        event_type=template_data["event_type"],
                        name=template_data["name"],
                        title_template=template_data["title_template"],
                        body_template=template_data["body_template"],
                        is_default=True,
                    )
                    session.add(template)

        await session.commit()


async def seed_default_groups():
    """Seed default groups and migrate existing users to appropriate groups.

    Creates the default system groups (Administrators, Operators, Viewers) if they
    don't exist, then migrates existing users:
    - Users with role='admin' -> Administrators group
    - Users with role='user' -> Operators group

    Also migrates old permissions to new ownership-based permissions (Issue #205).
    """
    import logging

    from sqlalchemy import select

    from backend.app.core.permissions import ALL_PERMISSIONS, DEFAULT_GROUPS
    from backend.app.models.group import Group
    from backend.app.models.user import User

    logger = logging.getLogger(__name__)

    # Map old permissions to new ones for migration
    # Administrators get *_all permissions, Operators get *_own permissions.
    #
    # NOTE on the read-flag asymmetry: write permissions (`update`, `delete`,
    # `reprint`) are removed from the legacy flag and remapped to the OWN/ALL
    # split — the legacy flag is dead on the API side. Read permissions are
    # different: the frontend still gates UI actions (download buttons in
    # ArchivesPage, preview button in FileManagerPage) on the LEGACY
    # `archives:read` / `library:read` / `queue:read` strings. For admin we
    # therefore keep the legacy flag (the `*_all` companion gets added via the
    # backfill block below). For non-admin roles the legacy IS renamed to
    # `_own` — that closes the IDOR (operators with a custom `archives:read`
    # row can no longer read cross-user data) and the UI gates degrade to
    # disabled-button state until the frontend is migrated to also accept
    # `_own` (separate change). See maziggy/bambuddy-security #2.
    PERMISSION_MIGRATION_ALL = {
        "queue:update": "queue:update_all",
        "queue:delete": "queue:delete_all",
        "archives:update": "archives:update_all",
        "archives:delete": "archives:delete_all",
        "archives:reprint": "archives:reprint_all",
        "library:update": "library:update_all",
        "library:delete": "library:delete_all",
    }

    PERMISSION_MIGRATION_OWN = {
        "queue:update": "queue:update_own",
        "queue:delete": "queue:delete_own",
        # Read permissions: any role NOT flagged as Administrator gets
        # ownership-scoped reads. Pre-existing custom roles with the legacy
        # `*:read` flag silently saw every user's items; the OWN variant
        # closes that IDOR. Roles that genuinely need cross-user visibility
        # must be re-granted `*:read_all` explicitly by an administrator
        # after upgrade — fail-closed by default (per CWE-636).
        "queue:read": "queue:read_own",
        "archives:update": "archives:update_own",
        "archives:delete": "archives:delete_own",
        "archives:reprint": "archives:reprint_own",
        "archives:read": "archives:read_own",
        "library:update": "library:update_own",
        "library:delete": "library:delete_own",
        "library:read": "library:read_own",
    }

    async with async_session() as session:
        # Get existing groups
        result = await session.execute(select(Group))
        existing_groups = {group.name: group for group in result.scalars().all()}

        # Create default groups if they don't exist
        groups_created = []
        for group_name, group_config in DEFAULT_GROUPS.items():
            if group_name not in existing_groups:
                group = Group(
                    name=group_name,
                    description=group_config["description"],
                    permissions=group_config["permissions"],
                    is_system=group_config["is_system"],
                )
                session.add(group)
                groups_created.append(group_name)
                logger.info("Created default group: %s", group_name)
            else:
                # Migrate existing group's permissions from old to new format
                group = existing_groups[group_name]
                if group.permissions:
                    updated = False
                    new_permissions = list(group.permissions)

                    # Determine which migration map to use based on group
                    migration_map = (
                        PERMISSION_MIGRATION_ALL if group_name == "Administrators" else PERMISSION_MIGRATION_OWN
                    )

                    for old_perm, new_perm in migration_map.items():
                        if old_perm in new_permissions:
                            new_permissions.remove(old_perm)
                            if new_perm not in new_permissions:
                                new_permissions.append(new_perm)
                            updated = True
                            logger.info(
                                "Migrated permission '%s' to '%s' in group '%s'", old_perm, new_perm, group_name
                            )

                    # For Administrators, also ensure they get *_all permissions if they have any new *_own
                    if group_name == "Administrators":
                        for _own_perm, all_perm in [
                            ("queue:update_own", "queue:update_all"),
                            ("queue:delete_own", "queue:delete_all"),
                            ("queue:read_own", "queue:read_all"),
                            ("archives:update_own", "archives:update_all"),
                            ("archives:delete_own", "archives:delete_all"),
                            ("archives:reprint_own", "archives:reprint_all"),
                            ("archives:read_own", "archives:read_all"),
                            ("library:update_own", "library:update_all"),
                            ("library:delete_own", "library:delete_all"),
                            ("library:read_own", "library:read_all"),
                        ]:
                            # Add *_all if not present
                            if all_perm not in new_permissions:
                                new_permissions.append(all_perm)
                                updated = True

                    if updated:
                        group.permissions = new_permissions

        await session.commit()

        # Migrate new permissions: grant printers:clear_plate to all groups with printers:control
        result = await session.execute(select(Group))
        all_groups = result.scalars().all()
        for group in all_groups:
            if (
                group.permissions
                and "printers:control" in group.permissions
                and "printers:clear_plate" not in group.permissions
            ):
                group.permissions = [*group.permissions, "printers:clear_plate"]
                logger.info("Added printers:clear_plate to group '%s' (has printers:control)", group.name)
        await session.commit()

        # Migrate new permissions for MakerWorld integration: groups that
        # already have library:upload (i.e. can write to the library) are
        # the correct audience for makerworld:view + makerworld:import, and
        # groups that only have library:read get makerworld:view (browse
        # only). Matches the intent of DEFAULT_GROUPS without clobbering
        # any user-customised permission lists.
        result = await session.execute(select(Group))
        for group in result.scalars().all():
            if not group.permissions:
                continue
            perms = list(group.permissions)
            changed = False
            if "library:upload" in perms:
                for new_perm in ("makerworld:view", "makerworld:import"):
                    if new_perm not in perms:
                        perms.append(new_perm)
                        changed = True
                        logger.info("Added %s to group '%s' (has library:upload)", new_perm, group.name)
            elif "library:read" in perms and "makerworld:view" not in perms:
                perms.append("makerworld:view")
                changed = True
                logger.info("Added makerworld:view to group '%s' (has library:read)", group.name)
            if changed:
                group.permissions = perms
        await session.commit()

        # Backfill: sync the Administrators system group to ALL_PERMISSIONS.
        # Administrators' contract is full access to every feature — fresh
        # installs get that via DEFAULT_GROUPS["Administrators"]["permissions"]
        # = ALL_PERMISSIONS. Upgrading installs would otherwise stay frozen at
        # whatever permission set existed when they were first seeded, so a
        # newly-added Permission enum member silently leaves admins gated out
        # of the feature it controls.
        #
        # Generalises the previous one-off admin backfills (library:purge,
        # archives:purge, the OWN/ALL read-flag set + legacy read flags,
        # orca_cloud:auth, printer_sensor_history:read, …): every current
        # Permission enum value is appended to the admin group if missing.
        # Additive only — never removes a permission an operator added by
        # hand. Run AFTER the legacy-rename migration above so the renamed
        # OWN/ALL variants land in the group before the sync sees them.
        result = await session.execute(select(Group).where(Group.name == "Administrators"))
        admin_group = result.scalar_one_or_none()
        if admin_group and admin_group.permissions is not None:
            perms = list(admin_group.permissions)
            added = False
            for new_perm in ALL_PERMISSIONS:
                if new_perm not in perms:
                    perms.append(new_perm)
                    added = True
                    logger.info("Added %s to Administrators group (ALL_PERMISSIONS sync)", new_perm)
            if added:
                admin_group.permissions = perms
        await session.commit()

        # Same OWN-tier backfill for non-admin system groups. Operators and
        # Viewers are seeded with _own on fresh installs (see DEFAULT_GROUPS),
        # but the legacy-rename migration above won't run on a role that
        # didn't carry the legacy `archives:read` flag. Without this block,
        # an existing Operators row whose permissions list lacks the legacy
        # flag would never get archives:read_own and operators would lose
        # read access after upgrade. Re-check by group name so customised
        # rows still get the correct OWN tier on next startup.
        #
        # Operators also get orca_cloud:auth backfilled — fresh installs now
        # include it in the DEFAULT_GROUPS bootstrap, so this keeps upgrades
        # consistent. Viewers do NOT get orca_cloud:auth (read-only role,
        # not expected to author slicer presets / sync to Orca Cloud).
        for non_admin_group_name in ("Operators", "Viewers"):
            grp = (await session.execute(select(Group).where(Group.name == non_admin_group_name))).scalar_one_or_none()
            if grp is None or grp.permissions is None:
                continue
            perms = list(grp.permissions)
            changed = False
            for own_perm in ("archives:read_own", "library:read_own", "queue:read_own"):
                if own_perm not in perms:
                    perms.append(own_perm)
                    changed = True
                    logger.info("Added %s to %s group (backfill)", own_perm, non_admin_group_name)
            if non_admin_group_name == "Operators" and "orca_cloud:auth" not in perms:
                perms.append("orca_cloud:auth")
                changed = True
                logger.info("Added orca_cloud:auth to Operators group (backfill)")
            if changed:
                grp.permissions = perms
        await session.commit()

        # Backfill inventory forecast permissions for existing groups.
        # inventory:forecast_read was added after initial seeding, so groups
        # that already have inventory:read (or inventory:update) need it added.
        # inventory:forecast_write goes to any group with inventory:update.
        result = await session.execute(select(Group))
        for group in result.scalars().all():
            if not group.permissions:
                continue
            perms = list(group.permissions)
            changed = False
            if "inventory:read" in perms and "inventory:forecast_read" not in perms:
                perms.append("inventory:forecast_read")
                changed = True
                logger.info("Added inventory:forecast_read to group '%s' (backfill)", group.name)
            if "inventory:update" in perms and "inventory:forecast_write" not in perms:
                perms.append("inventory:forecast_write")
                changed = True
                logger.info("Added inventory:forecast_write to group '%s' (backfill)", group.name)
            if changed:
                group.permissions = perms
        await session.commit()

        # Backfill pipeline permissions (#1425) for non-admin groups.
        # Administrators is handled by the ALL_PERMISSIONS sync above.
        #   - Operators: all three (matches fresh-install DEFAULT_GROUPS)
        #   - Any other group with library:read_own or settings:read:
        #     pipelines:read only
        result = await session.execute(select(Group))
        for group in result.scalars().all():
            if not group.permissions or group.name == "Administrators":
                continue
            perms = list(group.permissions)
            changed = False
            if group.name == "Operators":
                for new_perm in ("pipelines:read", "pipelines:write", "pipelines:run"):
                    if new_perm not in perms:
                        perms.append(new_perm)
                        changed = True
                        logger.info("Added %s to Operators group (backfill)", new_perm)
            elif "pipelines:read" not in perms and ("library:read_own" in perms or "settings:read" in perms):
                perms.append("pipelines:read")
                changed = True
                logger.info("Added pipelines:read to group '%s' (backfill)", group.name)
            if changed:
                group.permissions = perms
        await session.commit()

        # Migrate existing users to groups if they're not already in any group
        if groups_created:
            # Refresh to get newly created groups
            admin_result = await session.execute(select(Group).where(Group.name == "Administrators"))
            admin_group = admin_result.scalar_one_or_none()

            operators_result = await session.execute(select(Group).where(Group.name == "Operators"))
            operators_group = operators_result.scalar_one_or_none()

            # Get all users
            users_result = await session.execute(select(User))
            users = users_result.scalars().all()

            for user in users:
                # Skip if user already has groups
                if user.groups:
                    continue

                if user.role == "admin" and admin_group:
                    user.groups.append(admin_group)
                    logger.info("Migrated admin user '%s' to Administrators group", user.username)
                elif operators_group:
                    user.groups.append(operators_group)
                    logger.info("Migrated user '%s' to Operators group", user.username)

            await session.commit()


async def seed_spool_catalog():
    """Seed the spool catalog with default entries if empty."""
    import logging

    from sqlalchemy import func, select

    from backend.app.core.catalog_defaults import DEFAULT_SPOOL_CATALOG
    from backend.app.models.spool_catalog import SpoolCatalogEntry

    logger = logging.getLogger(__name__)

    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(SpoolCatalogEntry))
        count = result.scalar() or 0
        if count > 0:
            return  # Already seeded

        for name, weight in DEFAULT_SPOOL_CATALOG:
            session.add(SpoolCatalogEntry(name=name, weight=weight, is_default=True))
        await session.commit()
        logger.info("Seeded %d default spool catalog entries", len(DEFAULT_SPOOL_CATALOG))


async def seed_color_catalog():
    """Seed the color catalog with default entries if empty."""
    import logging

    from sqlalchemy import func, select

    from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG
    from backend.app.models.color_catalog import ColorCatalogEntry

    logger = logging.getLogger(__name__)

    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(ColorCatalogEntry))
        count = result.scalar() or 0
        if count > 0:
            return  # Already seeded

        for manufacturer, color_name, hex_color, material in DEFAULT_COLOR_CATALOG:
            session.add(
                ColorCatalogEntry(
                    manufacturer=manufacturer,
                    color_name=color_name,
                    hex_color=hex_color,
                    material=material,
                    is_default=True,
                )
            )
        await session.commit()
        logger.info("Seeded %d default color catalog entries", len(DEFAULT_COLOR_CATALOG))
