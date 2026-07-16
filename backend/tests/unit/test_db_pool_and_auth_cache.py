"""Tests for the DB connection-pool sizing/diagnostics and the auth-enabled
cache added for large printer farms (issue #2572)."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class TestPoolConfiguration:
    """P0: env-configurable, dialect-aware pool sizing."""

    def test_sqlite_defaults_when_unset(self, monkeypatch):
        """SQLite keeps 20 + 200 when no env override is set."""
        from backend.app.core import database

        for attr in ("db_pool_size", "db_max_overflow", "db_pool_timeout", "db_pool_recycle"):
            monkeypatch.setattr(database.settings, attr, None, raising=False)
        monkeypatch.setattr(database, "is_sqlite", lambda: True)

        kwargs = database._resolve_pool_kwargs()
        assert kwargs["pool_size"] == 20
        assert kwargs["max_overflow"] == 200
        # No server-socket recycle/pre-ping for a local file.
        assert "pool_pre_ping" not in kwargs
        assert "pool_recycle" not in kwargs

    def test_postgres_defaults_raise_the_old_limits(self, monkeypatch):
        """Postgres default is now 20 + 80 (was 10 + 20) with pre-ping + recycle."""
        from backend.app.core import database

        for attr in ("db_pool_size", "db_max_overflow", "db_pool_timeout", "db_pool_recycle"):
            monkeypatch.setattr(database.settings, attr, None, raising=False)
        monkeypatch.setattr(database, "is_sqlite", lambda: False)

        kwargs = database._resolve_pool_kwargs()
        assert kwargs["pool_size"] == 20
        assert kwargs["max_overflow"] == 80
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["pool_recycle"] == 1800

    def test_env_overrides_win_on_postgres(self, monkeypatch):
        """DB_POOL_* overrides replace the dialect defaults."""
        from backend.app.core import database

        monkeypatch.setattr(database.settings, "db_pool_size", 100, raising=False)
        monkeypatch.setattr(database.settings, "db_max_overflow", 200, raising=False)
        monkeypatch.setattr(database.settings, "db_pool_timeout", 45, raising=False)
        monkeypatch.setattr(database.settings, "db_pool_recycle", 600, raising=False)
        monkeypatch.setattr(database, "is_sqlite", lambda: False)

        kwargs = database._resolve_pool_kwargs()
        assert kwargs["pool_size"] == 100
        assert kwargs["max_overflow"] == 200
        assert kwargs["pool_timeout"] == 45
        assert kwargs["pool_recycle"] == 600

    @pytest.mark.asyncio
    async def test_concurrent_checkouts_exceed_base_pool_size(self, tmp_path):
        """Regression (#2572): more concurrent sessions than pool_size must all
        complete by drawing from max_overflow — not deadlock or time out.

        This is the failure the farm hit: printer callbacks held every base
        connection, so unrelated requests waited on the pool. With headroom in
        max_overflow, concurrent checkouts beyond pool_size still succeed.

        Uses a file-based SQLite URL so it gets a real queue pool — the
        in-memory URL forces a single-connection StaticPool that ignores
        pool_size/max_overflow entirely.
        """
        db_file = tmp_path / "pool_regression.db"
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}", pool_size=2, max_overflow=10)
        sm = async_sessionmaker(eng)

        async def _one():
            async with sm() as s:
                await s.execute(text("SELECT 1"))
                # Hold the checkout briefly so the calls genuinely overlap and
                # force the pool past its base size of 2.
                await asyncio.sleep(0.05)
                return (await s.execute(text("SELECT 1"))).scalar()

        try:
            results = await asyncio.gather(*[_one() for _ in range(12)])
        finally:
            await eng.dispose()

        assert results == [1] * 12


class TestPoolStatus:
    """P3: diagnostics snapshot."""

    def test_get_pool_status_shape(self):
        from backend.app.core.database import get_pool_status

        status = get_pool_status()
        assert status["dialect"] in ("sqlite", "postgresql")
        for key in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping"):
            assert key in status["config"]
        # Live gauges are present (values are ints on a QueuePool).
        for key in ("current_size", "checked_out", "checked_in", "overflow"):
            assert key in status


class TestAuthEnabledCache:
    """P1: cache the auth-enabled probe, but only ever cache True."""

    class _Setting:
        def __init__(self, value):
            self.value = value

    class _Result:
        def __init__(self, setting):
            self._setting = setting

        def scalar_one_or_none(self):
            return self._setting

    class _CountingDB:
        def __init__(self, value):
            self._value = value
            self.calls = 0

        async def execute(self, *args, **kwargs):
            self.calls += 1
            setting = None if self._value is None else TestAuthEnabledCache._Setting(self._value)
            return TestAuthEnabledCache._Result(setting)

    @pytest.mark.asyncio
    async def test_enabled_true_is_cached(self):
        from backend.app.core import auth as auth_mod

        auth_mod.invalidate_auth_enabled_cache()
        db = self._CountingDB("true")

        assert await auth_mod.is_auth_enabled(db) is True
        assert db.calls == 1
        # Second probe served from cache — no new query.
        assert await auth_mod.is_auth_enabled(db) is True
        assert db.calls == 1

        # Invalidation forces a re-read (e.g. after set_auth_enabled).
        auth_mod.invalidate_auth_enabled_cache()
        assert await auth_mod.is_auth_enabled(db) is True
        assert db.calls == 2
        auth_mod.invalidate_auth_enabled_cache()

    @pytest.mark.asyncio
    async def test_disabled_is_never_cached(self):
        """SECURITY: a disabled result must never be cached, so staleness can
        only ever fail closed (require auth), never open."""
        from backend.app.core import auth as auth_mod

        auth_mod.invalidate_auth_enabled_cache()
        db = self._CountingDB("false")

        assert await auth_mod.is_auth_enabled(db) is False
        assert db.calls == 1
        # Every probe re-reads while disabled.
        assert await auth_mod.is_auth_enabled(db) is False
        assert db.calls == 2
        auth_mod.invalidate_auth_enabled_cache()

    @pytest.mark.asyncio
    async def test_unconfigured_returns_false_and_is_not_cached(self):
        from backend.app.core import auth as auth_mod

        auth_mod.invalidate_auth_enabled_cache()
        db = self._CountingDB(None)

        assert await auth_mod.is_auth_enabled(db) is False
        assert await auth_mod.is_auth_enabled(db) is False
        assert db.calls == 2
        auth_mod.invalidate_auth_enabled_cache()

    @pytest.mark.asyncio
    async def test_db_error_propagates_fail_closed(self):
        """A probe error must propagate (fail closed), not be swallowed."""
        from backend.app.core import auth as auth_mod

        auth_mod.invalidate_auth_enabled_cache()

        class _RaisingDB:
            async def execute(self, *args, **kwargs):
                raise RuntimeError("connection lost")

        with pytest.raises(RuntimeError):
            await auth_mod.is_auth_enabled(_RaisingDB())
        auth_mod.invalidate_auth_enabled_cache()
