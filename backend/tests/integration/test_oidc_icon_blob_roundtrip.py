"""Backup-schema fidelity for the PG→SQLite portable export (#1333, #2526).

Bambuddy's ``create_backup_zip`` rebuilds the SQLite backup schema when the
source database is PostgreSQL. It now uses ``Base.metadata.create_all()``
against a SQLite engine — the same DDL a native SQLite install gets — rather
than a hand-rolled ``name + type`` CREATE TABLE. The old rebuild dropped two
things that these tests pin:

* ``LargeBinary`` fell through to ``TEXT``, corrupting non-UTF8 OIDC icon
  bytes during the round trip (#1333). ``create_all`` renders it as ``BLOB``.
* ``NOT NULL`` / ``DEFAULT`` / FK / ``UNIQUE`` were all dropped, so a
  Postgres→SQLite restore left ``server_default`` columns (e.g.
  ``spoolbuddy_devices.created_at``) with no ``DEFAULT`` — later inserts
  wrote ``NULL`` and 500'd on read (#2526). ``create_all`` emits the default.

The SQLite *source* path is just ``shutil.copy2`` of the live .db file and is
therefore unaffected — these guards only matter for the PostgreSQL branch.
"""

import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import Base
from backend.tests._fixtures.oidc_icon import PNG_BYTES as _PNG_BYTES


def _build_backup_schema(db_path) -> dict[str, dict]:
    """Build the portable SQLite schema exactly as create_backup_zip's
    PostgreSQL branch does, then return ``{table: {col: PRAGMA row}}``.

    PRAGMA table_info rows are ``(cid, name, type, notnull, dflt_value, pk)``.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    conn = sqlite3.connect(str(db_path))
    try:
        schema: dict[str, dict] = {}
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ]
        for table in tables:
            schema[table] = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608
        return schema
    finally:
        conn.close()


class TestBackupSchemaFidelity:
    """The real backup-schema builder (metadata.create_all on SQLite),
    inspected via sqlite_master, keeps the constraints the old name+type
    rebuild dropped."""

    def test_icon_data_column_is_blob(self, tmp_path):
        # #1333 — LargeBinary must render as BLOB, not TEXT, or non-UTF8
        # OIDC icon bytes are corrupted on the PG→SQLite round trip.
        schema = _build_backup_schema(tmp_path / "schema.db")
        assert schema["oidc_providers"]["icon_data"][2] == "BLOB"

    def test_server_default_column_keeps_default(self, tmp_path):
        # #2526 — a server_default=func.now() column must carry a DEFAULT so
        # inserts that omit it (SQLAlchemy does, for server-side defaults)
        # don't write NULL after a Postgres→SQLite restore.
        schema = _build_backup_schema(tmp_path / "schema.db")
        created_at = schema["spoolbuddy_devices"]["created_at"]
        assert created_at[4] is not None, "created_at lost its DEFAULT clause"
        assert "CURRENT_TIMESTAMP" in str(created_at[4]).upper()

    def test_not_null_column_keeps_not_null(self, tmp_path):
        # #2526 — NOT NULL columns must stay NOT NULL. A single-column PK is
        # implicitly NOT NULL, so assert on a non-PK required column.
        schema = _build_backup_schema(tmp_path / "schema.db")
        # notnull flag is index 3 of the PRAGMA row.
        assert schema["spoolbuddy_devices"]["device_id"][3] == 1


class TestSqliteBinaryRoundtrip:
    """SQLite natively stores BLOB without escaping — sanity-check that the
    serialise/deserialise path used by the PG→SQLite backup (``executemany``
    with bytes values) preserves non-UTF8 bytes exactly."""

    def test_binary_value_roundtrips_through_sqlite_blob(self, tmp_path):
        db_path = tmp_path / "roundtrip.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, blob BLOB)")
            # A payload that's deliberately not UTF8-decodable.
            payload = bytes(range(256))
            conn.execute("INSERT INTO t (id, blob) VALUES (?, ?)", (1, payload))
            conn.commit()
            row = conn.execute("SELECT blob FROM t WHERE id = 1").fetchone()
            assert row[0] == payload
        finally:
            conn.close()


class TestIconTripletCheckConstraint:
    """N10 — DB-level enforcement of the icon-cache triplet invariant.

    The CHECK constraint applies on SQLite fresh installs (via
    metadata.create_all) and on PostgreSQL fresh + stale installs (via
    ALTER TABLE ADD CONSTRAINT). Stale SQLite installs do not get it
    (SQLite cannot ADD CONSTRAINT to an existing table) — documented
    trade-off, application layer enforces.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_triplet_accepted(self, db_session: AsyncSession):
        from backend.app.models.oidc_provider import OIDCProvider

        prov = OIDCProvider(
            name="TripletFullProv",
            issuer_url="https://idp.example.com",
            client_id="c",
            scopes="openid",
            is_enabled=True,
        )
        prov.client_secret = "secret"
        prov.icon_data = _PNG_BYTES
        prov.icon_content_type = "image/png"
        prov.icon_etag = hashlib.sha256(_PNG_BYTES).hexdigest()
        db_session.add(prov)
        await db_session.commit()  # must not raise

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_all_null_triplet_accepted(self, db_session: AsyncSession):
        from backend.app.models.oidc_provider import OIDCProvider

        prov = OIDCProvider(
            name="TripletEmptyProv",
            issuer_url="https://idp.example.com",
            client_id="c",
            scopes="openid",
            is_enabled=True,
        )
        prov.client_secret = "secret"
        # All three icon columns left as default None.
        db_session.add(prov)
        await db_session.commit()  # must not raise

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_partial_triplet_rejected_by_check_constraint(self, db_session: AsyncSession):
        """Direct UPDATE that sets only icon_content_type (no icon_data, no
        icon_etag) must violate the CHECK constraint on a fresh SQLite
        install (CHECK constraints fire on SQLite even when foreign keys
        are off). Demonstrates the CHECK is the catch-net for raw-SQL
        maintenance paths that bypass _fetch_icon_or_400.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        from backend.app.models.oidc_provider import OIDCProvider

        prov = OIDCProvider(
            name="TripletPartialProv",
            issuer_url="https://idp.example.com",
            client_id="c",
            scopes="openid",
            is_enabled=True,
        )
        prov.client_secret = "secret"
        db_session.add(prov)
        await db_session.commit()
        pid = prov.id

        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("UPDATE oidc_providers SET icon_content_type = :ct WHERE id = :pid"),
                {"ct": "image/png", "pid": pid},
            )
            await db_session.commit()
