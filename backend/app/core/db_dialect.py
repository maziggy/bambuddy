"""Database dialect helpers for SQLite/PostgreSQL dual support.

Bambuddy defaults to SQLite (zero-config). When DATABASE_URL points to PostgreSQL,
these helpers ensure dialect-specific operations use the correct SQL.
"""

from sqlalchemy import func, text


def is_postgres() -> bool:
    """Check if using PostgreSQL based on DATABASE_URL."""
    from backend.app.core.config import settings

    return settings.database_url.startswith("postgresql")


def is_sqlite() -> bool:
    """Check if using SQLite based on DATABASE_URL."""
    from backend.app.core.config import settings

    return settings.database_url.startswith("sqlite")


async def upsert_setting(db, model, key: str, value: str):
    """Dialect-aware INSERT ... ON CONFLICT UPDATE for the Settings table."""
    if is_postgres():
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(model).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_at": func.now()},
        )
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(model).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_at": func.now()},
        )
    await db.execute(stmt)


async def run_pragma(conn, pragma_sql: str):
    """Run a PRAGMA statement only on SQLite (no-op on PostgreSQL)."""
    if is_sqlite():
        await conn.execute(text(pragma_sql))
