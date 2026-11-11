"""A read cancelled by a leaving viewer must not hang its connection (#3515).

A camera viewer leaving cancels its stream with AnyIO's level cancellation. A
read cancelled halfway makes SQLAlchemy discard the connection; when that close
is cancelled too, it calls aiosqlite's ``stop()`` while the close still runs.
aiosqlite 0.22.1 then never finished the close (omnilib/aiosqlite#403), the
session's exit waited on it forever, and the cancelled wait spun a CPU core.
"""

import asyncio
import os
import subprocess
import sys
import textwrap
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import anyio
import pytest

from backend.app.core import auth, database  # noqa: F401 - database applies the aiosqlite fix
from backend.app.services import camera_light

pytestmark = pytest.mark.unit


class TestAiosqliteStop:
    async def test_a_stop_during_a_close_lets_the_close_finish(self):
        # omnilib/aiosqlite#403's reproduction: the worker exits at the stop
        # requested from outside, before the close's own stop is served.
        gate = threading.Event()
        conn = await aiosqlite.connect(":memory:")
        await conn.create_function("hold", 0, lambda: gate.wait() and 1)
        busy = asyncio.create_task(conn.execute("SELECT hold()"))
        await asyncio.sleep(0.05)

        closing = asyncio.create_task(conn.close())
        await asyncio.sleep(0.05)
        conn.stop()
        gate.set()
        await busy

        await asyncio.wait_for(closing, timeout=5)

    async def test_a_second_stop_returns_the_first_ones_future(self):
        conn = await aiosqlite.connect(":memory:")
        first = conn.stop()

        assert conn.stop() is first
        await asyncio.wait_for(first, timeout=5)


# Many short reads, cancelled at once by an AnyIO task group as a leaving viewer
# cancels its stream, against the engine Bambuddy creates. Run in a process of
# its own: a hung close can't be cancelled, so the test would hang with it.
_BURST = textwrap.dedent(
    """
    import asyncio, sys

    import anyio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.core.database import _create_engine


    async def main(url):
        engine = _create_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.execute(text("create table settings (key text primary key, value text)"))
            await conn.execute(text("insert into settings values ('camera_light_mode', 'off')"))

        async def read():
            try:
                async with sessions() as session:
                    await session.execute(text("select value from settings"))
                    await session.execute(text("select value from settings"))
            except Exception:
                pass

        for i in range(40):
            async with anyio.create_task_group() as group:
                for _ in range(8):
                    group.start_soon(read)
                await asyncio.sleep((i % 10) * 0.0002)
                group.cancel_scope.cancel()
        await engine.dispose()
        await asyncio.sleep(1)
        left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        print("pending", len(left))


    anyio.run(main, sys.argv[1], backend="asyncio")
    """
)


def test_cancelled_reads_leave_no_connection_behind(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'burst.db'}"
    # Its own DATABASE_URL: never the repo's .env.
    env = {**os.environ, "DATABASE_URL": url}
    try:
        result = subprocess.run(
            [sys.executable, "-c", _BURST, url],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("a cancelled read left its connection's close hanging")

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "pending 0"


class _Session:
    """A session whose every step yields to the loop, so a cancel lands."""

    def __init__(self):
        self.closed = False

    async def __aenter__(self):
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc):
        await asyncio.sleep(0)
        self.closed = True


class TestTheStreamReadsRunToTheEnd:
    """Both run inside a camera stream; its viewer leaving must not cut them off."""

    async def test_the_camera_light_settings(self):
        session = _Session()
        values = {"camera_light_mode": "all", "camera_light_delay": "3"}

        async def get_setting(_db, key):
            await asyncio.sleep(0)
            return values[key]

        result = None
        with (
            patch.object(database, "async_session", lambda: session),
            patch("backend.app.api.routes.settings.get_setting", get_setting),
            anyio.CancelScope() as viewer_left,
        ):
            viewer_left.cancel()
            result = await camera_light._read_settings()

        assert result == ("all", 3.0)
        assert session.closed

    async def test_the_stream_re_check(self):
        session = _Session()

        @asynccontextmanager
        async def factory():
            async with session:
                yield session

        result = None
        with (
            patch.object(auth, "async_session", factory),
            patch.object(auth, "is_auth_enabled", AsyncMock(return_value=False)),
            anyio.CancelScope() as viewer_left,
        ):
            viewer_left.cancel()
            result = await auth.stream_opener_still_allowed(auth.StreamOpener(auth_on=False), printer_id=1)

        assert result is True
        assert session.closed


class TestOlderAiosqlite:
    def test_a_version_without_stop_is_left_alone(self, monkeypatch):
        # Before 0.22.1 aiosqlite has no stop(), and SQLAlchemy calls none. The
        # in-app update keeps such a version, so it must not fail the import.
        class Connection:
            pass

        monkeypatch.setattr("aiosqlite.core.Connection", Connection)

        database._make_aiosqlite_stop_idempotent()

        assert not hasattr(Connection, "stop")


class TestALeavingViewerDuringASlowReCheck:
    async def test_the_retry_does_not_keep_the_stream(self, monkeypatch):
        # Every database connection busy: the shielded re-check waits for one
        # and times out. A viewer who left meanwhile must not be held through
        # the immediate retry's own shielded wait as well.
        from backend.app.api.routes import camera

        monkeypatch.setattr(camera, "STREAM_RECHECK_SECONDS", 0.0)
        monkeypatch.setattr(camera, "STREAM_RECHECK_RETRY_SECONDS", 0.0)
        calls = 0

        async def slow_failure(_opener, _printer_id):
            nonlocal calls
            calls += 1
            with anyio.CancelScope(shield=True):
                await asyncio.sleep(0.2)
            raise TimeoutError("pool")

        monkeypatch.setattr(camera, "stream_opener_still_allowed", slow_failure)

        async def frames():
            while True:
                yield b"frame"
                await asyncio.sleep(0)

        async def watch():
            async for _ in camera._while_allowed(frames(), auth.StreamOpener(auth_on=False), 1):
                pass

        async with anyio.create_task_group() as group:
            group.start_soon(watch)
            await asyncio.sleep(0.05)
            group.cancel_scope.cancel()

        assert calls == 1
