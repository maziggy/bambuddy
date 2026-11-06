"""The cloud filament-ID map is built once per account at a time, without a
request holding a database connection (RC-C11).

When the 5-minute cache expired, every K-profile view asked at once and each
ran the full 1 + N Bambu Cloud fetch while its request held a database
connection idle in transaction.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes import cloud

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _empty():
    cloud.forget_all_cloud_presets()
    yield
    cloud.forget_all_cloud_presets()


def _slow_cloud(release: asyncio.Event):
    async def settings():
        await release.wait()
        return {"filament": {"private": [{"setting_id": "S1"}]}}

    return SimpleNamespace(
        is_authenticated=True,
        get_slicer_settings=AsyncMock(side_effect=settings),
        get_setting_detail=AsyncMock(return_value={"filament_id": "P1", "name": "Mine @Bambu Lab X1C"}),
        close=AsyncMock(),
    )


@asynccontextmanager
async def _own_session():
    yield "own session"


async def test_views_arriving_together_share_one_build():
    release = asyncio.Event()
    built: list = []

    async def build(db, user):
        # A real token read awaits the database.
        await asyncio.sleep(0.001)
        built.append(db)
        return _slow_cloud(release)

    user = SimpleNamespace(id=7)
    with (
        patch.object(cloud, "build_authenticated_cloud", new=build),
        patch("backend.app.core.database.async_session", new=_own_session),
    ):
        calls = [asyncio.create_task(cloud.get_filament_id_map(current_user=user)) for _ in range(3)]
        await asyncio.sleep(0.05)
        release.set()
        results = await asyncio.gather(*calls)

    assert results == [{"P1": "Mine"}] * 3
    assert built == ["own session"], "every view built the map itself, or in its request's session"


async def test_a_build_running_when_the_account_signs_out_is_not_cached():
    release = asyncio.Event()

    async def build(db, user):
        return _slow_cloud(release)

    with (
        patch.object(cloud, "build_authenticated_cloud", new=build),
        patch("backend.app.core.database.async_session", new=_own_session),
    ):
        call = asyncio.create_task(cloud.get_filament_id_map(current_user=SimpleNamespace(id=7)))
        await asyncio.sleep(0.05)
        cloud._forget_account_presets(7)
        release.set()
        await call

    assert 7 not in cloud._filament_id_name_cache
