"""Type-mapping coverage for the OIDC icon BLOB column (#1333).

Bambuddy's ``create_backup_zip`` rebuilds the SQLite backup schema from
``Base.metadata`` when the source database is PostgreSQL. The column-type
mapping previously fell through to ``TEXT`` for any unknown SQLAlchemy
type — including ``LargeBinary`` / ``BYTEA`` — which corrupts non-UTF8
icon bytes during the PG → SQLite-ZIP round trip.

These tests exercise the extracted ``_sqlalchemy_type_to_sqlite_type``
helper directly so the regression guard doesn't depend on a full backup
pipeline. The SQLite source path is just ``shutil.copy2`` of the live
.db file and is therefore unaffected by the type mapping.
"""

import hashlib
import sqlite3

import pytest
from sqlalchemy import Column, LargeBinary
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import _sqlalchemy_type_to_sqlite_type
from backend.tests._fixtures.oidc_icon import PNG_BYTES as _PNG_BYTES


class TestTypeMapping:
    """Unit-level coverage of the helper that backups use for PG→SQLite."""

    def test_largebinary_maps_to_blob(self):
        # Direct from a SQLAlchemy LargeBinary column — this is exactly
        # what the create_backup_zip loop calls str() on.
        col = Column(LargeBinary)
        assert _sqlalchemy_type_to_sqlite_type(str(col.type)) == "BLOB"

    @pytest.mark.parametrize(
        "type_repr",
        ["BLOB", "BYTEA", "BYTEA(1024)", "VARBINARY", "BINARY", "binary varying"],
    )
    def test_binary_type_strings_map_to_blob(self, type_repr):
        assert _sqlalchemy_type_to_sqlite_type(type_repr) == "BLOB"

    def test_integer_unchanged(self):
        assert _sqlalchemy_type_to_sqlite_type("INTEGER") == "INTEGER"
        assert _sqlalchemy_type_to_sqlite_type("BIGINT") == "INTEGER"

    def test_boolean_unchanged(self):
        assert _sqlalchemy_type_to_sqlite_type("BOOLEAN") == "BOOLEAN"

    def test_unknown_falls_back_to_text(self):
        assert _sqlalchemy_type_to_sqlite_type("VARCHAR(500)") == "TEXT"
        assert _sqlalchemy_type_to_sqlite_type("DATETIME") == "TEXT"


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
