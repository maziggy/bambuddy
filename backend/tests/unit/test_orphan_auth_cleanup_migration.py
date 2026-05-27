"""Regression test for the orphan OIDC/MFA cleanup migration (#1285).

On SQLite (PRAGMA foreign_keys=OFF by default), the ON DELETE CASCADE
declared on user_oidc_links.user_id / user_totp.user_id /
user_otp_codes.user_id is NOT enforced. Users deleted via the API before
the fix (PR for #1285) left orphan rows pointing to non-existent users.
The OIDC callback would then find the orphan UserOIDCLink, fail to load
the deleted user, and redirect to ``account_inactive`` instead of running
auto_create_users.

run_migrations now sweeps orphans on every startup; this test verifies it
on all three tables and proves idempotency + no-op behaviour on fresh DBs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Pin the SQLite branch in run_migrations regardless of env."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_all_models():
    """Import the models package so every Base.metadata table is registered.

    Previously this listed each submodule by hand and silently drifted from
    backend/app/models/__init__.py (#1295 review nit). Importing the package
    triggers __init__.py which covers most of the schema automatically.

    A handful of submodules are NOT re-exported from __init__.py yet but are
    required by run_migrations (they touch tables that don't appear in any
    re-exported model). Those are imported by submodule below so the test
    engine has the full schema available. Keep this list in sync with the
    set conftest.py imports for test_engine.
    """
    import backend.app.models  # noqa: F401

    # Submodules whose tables are touched by run_migrations but which are
    # not re-exported from __init__.py.
    from backend.app.models import (  # noqa: F401
        external_link,
        print_queue,
        project_bom,
        slot_preset,
        spoolman_k_profile,
        spoolman_slot_assignment,
        virtual_printer,
    )


@pytest.fixture
async def engine_with_full_schema():
    """In-memory SQLite with the full schema via create_all (no manual SQL)."""
    from backend.app.core.database import Base

    _register_all_models()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


# -----------------------------------------------------------------------------
# Per-table orphan cleanup
# -----------------------------------------------------------------------------


async def test_migration_deletes_orphan_user_oidc_links(engine_with_full_schema):
    """Orphan rows in user_oidc_links must be removed; rows pointing at a real
    user must stay."""
    async with engine_with_full_schema.begin() as conn:
        # One real user, one nonexistent referenced by an OIDC link
        await conn.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_active, created_at, updated_at, "
                "role, auth_source) VALUES (1, 'survivor', 'h', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'user', 'local')"
            )
        )
        # Provider (any provider — the link only requires existence)
        await conn.execute(
            text(
                "INSERT INTO oidc_providers (id, name, issuer_url, client_id, client_secret, "
                "scopes, is_enabled, auto_create_users, auto_link_existing_accounts, email_claim, "
                "require_email_verified, created_at, updated_at) VALUES (1, 'p', 'https://x', 'c', "
                "'s', 'openid', 1, 1, 0, 'email', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # Valid link
        await conn.execute(
            text(
                "INSERT INTO user_oidc_links (id, user_id, provider_id, provider_user_id, created_at) "
                "VALUES (10, 1, 1, 'sub-real', CURRENT_TIMESTAMP)"
            )
        )
        # Orphan link — user_id=999 does not exist
        await conn.execute(
            text(
                "INSERT INTO user_oidc_links (id, user_id, provider_id, provider_user_id, created_at) "
                "VALUES (11, 999, 1, 'sub-orphan', CURRENT_TIMESTAMP)"
            )
        )

    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)

    async with engine_with_full_schema.begin() as conn:
        ids = [row[0] for row in (await conn.execute(text("SELECT id FROM user_oidc_links ORDER BY id"))).all()]
        assert ids == [10], f"Expected only the valid link to survive, got {ids}"


async def test_migration_deletes_orphan_user_totp(engine_with_full_schema):
    """Orphan rows in user_totp must be removed; rows for real users must stay."""
    async with engine_with_full_schema.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_active, created_at, updated_at, "
                "role, auth_source) VALUES (1, 'survivor', 'h', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'user', 'local')"
            )
        )
        # Valid TOTP
        await conn.execute(
            text(
                "INSERT INTO user_totp (id, user_id, secret, is_enabled, created_at, updated_at) "
                "VALUES (10, 1, 'enc', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # Orphan TOTP — user_id=999 does not exist (would never happen with FK on,
        # but SQLite tolerates it because PRAGMA foreign_keys=OFF)
        await conn.execute(
            text(
                "INSERT INTO user_totp (id, user_id, secret, is_enabled, created_at, updated_at) "
                "VALUES (11, 999, 'orphan_enc', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)

    async with engine_with_full_schema.begin() as conn:
        ids = [row[0] for row in (await conn.execute(text("SELECT id FROM user_totp ORDER BY id"))).all()]
        assert ids == [10], f"Expected only the valid TOTP row to survive, got {ids}"


async def test_migration_deletes_orphan_user_otp_codes(engine_with_full_schema):
    """Orphan rows in user_otp_codes must be removed; rows for real users must stay."""
    exp = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    async with engine_with_full_schema.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_active, created_at, updated_at, "
                "role, auth_source) VALUES (1, 'survivor', 'h', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'user', 'local')"
            )
        )
        # Valid OTP code
        await conn.execute(
            text(
                "INSERT INTO user_otp_codes (id, user_id, code_hash, attempts, used, expires_at, created_at) "
                "VALUES (10, 1, '$h$', 0, 0, :exp, CURRENT_TIMESTAMP)"
            ),
            {"exp": exp},
        )
        # Two orphan OTP codes
        await conn.execute(
            text(
                "INSERT INTO user_otp_codes (id, user_id, code_hash, attempts, used, expires_at, created_at) "
                "VALUES (11, 999, '$h$', 0, 0, :exp, CURRENT_TIMESTAMP)"
            ),
            {"exp": exp},
        )
        await conn.execute(
            text(
                "INSERT INTO user_otp_codes (id, user_id, code_hash, attempts, used, expires_at, created_at) "
                "VALUES (12, 1000, '$h$', 0, 0, :exp, CURRENT_TIMESTAMP)"
            ),
            {"exp": exp},
        )

    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)

    async with engine_with_full_schema.begin() as conn:
        ids = [row[0] for row in (await conn.execute(text("SELECT id FROM user_otp_codes ORDER BY id"))).all()]
        assert ids == [10], f"Expected only the valid OTP row to survive, got {ids}"


async def test_migration_deletes_orphan_long_lived_tokens(engine_with_full_schema):
    """Orphan rows in long_lived_tokens must be removed; rows for real users must stay.

    Camera-stream tokens whose secret_hash is still valid would otherwise be
    matchable by verify() via lookup_prefix even after the owning user is gone
    (#1295 review feedback extended #1285).
    """
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    async with engine_with_full_schema.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_active, created_at, updated_at, "
                "role, auth_source) VALUES (1, 'survivor', 'h', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'user', 'local')"
            )
        )
        # Valid token for the real user
        await conn.execute(
            text(
                "INSERT INTO long_lived_tokens (id, user_id, name, lookup_prefix, secret_hash, "
                "scope, expires_at, created_at) VALUES (10, 1, 'real', 'aaaa1111', '$2b$h', "
                "'camera_stream', :exp, CURRENT_TIMESTAMP)"
            ),
            {"exp": exp},
        )
        # Orphan token — user_id=999 does not exist
        await conn.execute(
            text(
                "INSERT INTO long_lived_tokens (id, user_id, name, lookup_prefix, secret_hash, "
                "scope, expires_at, created_at) VALUES (11, 999, 'orphan', 'bbbb2222', '$2b$h', "
                "'camera_stream', :exp, CURRENT_TIMESTAMP)"
            ),
            {"exp": exp},
        )

    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)

    async with engine_with_full_schema.begin() as conn:
        ids = [row[0] for row in (await conn.execute(text("SELECT id FROM long_lived_tokens ORDER BY id"))).all()]
        assert ids == [10], f"Expected only the valid long-lived token to survive, got {ids}"


# -----------------------------------------------------------------------------
# No-op and idempotency
# -----------------------------------------------------------------------------


async def test_migration_is_noop_on_fresh_install(engine_with_full_schema):
    """A fresh DB with empty users + auth tables must not raise and must not
    modify anything."""
    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)
        await run_migrations(conn)  # second run, still fine

    # Static queries (one per table) instead of an f-string interpolated loop:
    # Bandit B608 flags f"... FROM {tbl}" as a possible SQL-injection vector
    # even when ``tbl`` is bound to a tuple of literals. Spelling out each
    # table name makes the intent clear and silences the false-positive
    # without resorting to a noqa marker. See PR #1295 CodeQL alert #798.
    async with engine_with_full_schema.begin() as conn:
        oidc_count = (await conn.execute(text("SELECT COUNT(*) FROM user_oidc_links"))).scalar_one()
        totp_count = (await conn.execute(text("SELECT COUNT(*) FROM user_totp"))).scalar_one()
        otp_count = (await conn.execute(text("SELECT COUNT(*) FROM user_otp_codes"))).scalar_one()
        llt_count = (await conn.execute(text("SELECT COUNT(*) FROM long_lived_tokens"))).scalar_one()
        assert oidc_count == 0
        assert totp_count == 0
        assert otp_count == 0
        assert llt_count == 0


async def test_migration_is_idempotent(engine_with_full_schema):
    """Running the migration twice on data with orphans cleans them once, the
    second run finds nothing left and is a no-op."""
    async with engine_with_full_schema.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_active, created_at, updated_at, "
                "role, auth_source) VALUES (1, 'u', 'h', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'user', 'local')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO oidc_providers (id, name, issuer_url, client_id, client_secret, "
                "scopes, is_enabled, auto_create_users, auto_link_existing_accounts, email_claim, "
                "require_email_verified, created_at, updated_at) VALUES (1, 'p', 'https://x', 'c', "
                "'s', 'openid', 1, 1, 0, 'email', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO user_oidc_links (id, user_id, provider_id, provider_user_id, created_at) "
                "VALUES (1, 999, 1, 'orphan', CURRENT_TIMESTAMP)"
            )
        )

    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)
    # Second run must not crash, must not double-touch anything
    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)

    async with engine_with_full_schema.begin() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM user_oidc_links"))).scalar_one()
        assert count == 0


async def test_migration_keeps_rows_for_existing_users(engine_with_full_schema):
    """Belt-and-braces: rows for real users must never be touched even when
    other tables have orphans being cleaned at the same time."""
    exp = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    async with engine_with_full_schema.begin() as conn:
        for uid in (1, 2):
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, is_active, created_at, updated_at, "
                    "role, auth_source) VALUES (:id, :name, 'h', 1, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, 'user', 'local')"
                ),
                {"id": uid, "name": f"u{uid}"},
            )
        await conn.execute(
            text(
                "INSERT INTO oidc_providers (id, name, issuer_url, client_id, client_secret, "
                "scopes, is_enabled, auto_create_users, auto_link_existing_accounts, email_claim, "
                "require_email_verified, created_at, updated_at) VALUES (1, 'p', 'https://x', 'c', "
                "'s', 'openid', 1, 1, 0, 'email', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # Mix: valid + orphan in each table
        await conn.execute(
            text(
                "INSERT INTO user_oidc_links (id, user_id, provider_id, provider_user_id, created_at) "
                "VALUES (1, 1, 1, 'real', CURRENT_TIMESTAMP), "
                "(2, 999, 1, 'orphan', CURRENT_TIMESTAMP)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO user_totp (id, user_id, secret, is_enabled, created_at, updated_at) "
                "VALUES (1, 2, 'enc', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "(2, 998, 'orphan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO user_otp_codes (id, user_id, code_hash, attempts, used, expires_at, "
                "created_at) VALUES (1, 1, '$h$', 0, 0, :exp, CURRENT_TIMESTAMP), "
                "(2, 997, '$h$', 0, 0, :exp, CURRENT_TIMESTAMP)"
            ),
            {"exp": exp},
        )
        llt_exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await conn.execute(
            text(
                "INSERT INTO long_lived_tokens (id, user_id, name, lookup_prefix, secret_hash, "
                "scope, expires_at, created_at) VALUES (1, 2, 'real', 'aaaa1111', '$h', "
                "'camera_stream', :exp, CURRENT_TIMESTAMP), (2, 996, 'orphan', 'bbbb2222', '$h', "
                "'camera_stream', :exp, CURRENT_TIMESTAMP)"
            ),
            {"exp": llt_exp},
        )

    async with engine_with_full_schema.begin() as conn:
        await run_migrations(conn)

    async with engine_with_full_schema.begin() as conn:
        links = [
            row[0] for row in (await conn.execute(text("SELECT user_id FROM user_oidc_links ORDER BY user_id"))).all()
        ]
        totps = [row[0] for row in (await conn.execute(text("SELECT user_id FROM user_totp ORDER BY user_id"))).all()]
        otps = [
            row[0] for row in (await conn.execute(text("SELECT user_id FROM user_otp_codes ORDER BY user_id"))).all()
        ]
        llts = [
            row[0] for row in (await conn.execute(text("SELECT user_id FROM long_lived_tokens ORDER BY user_id"))).all()
        ]
        assert links == [1], f"Expected only user_id=1 to survive in user_oidc_links, got {links}"
        assert totps == [2], f"Expected only user_id=2 to survive in user_totp, got {totps}"
        assert otps == [1], f"Expected only user_id=1 to survive in user_otp_codes, got {otps}"
        assert llts == [2], f"Expected only user_id=2 to survive in long_lived_tokens, got {llts}"
