"""Filament-info cloud lookups single-flight concurrent misses (#2572).

The printer overview mounts one ``/cloud/filament-info`` request per printer
card, so at farm scale several browsers ask for the same uncached preset in the
same instant. Without coalescing each request issued its own Bambu Cloud
round-trip for the same id — a thundering herd against a rate-limited API.
``_resolve_cloud_filament`` makes the first caller the leader and has the rest
await its result.
"""

import asyncio

import pytest

import backend.app.api.routes.cloud as cloud_mod
from backend.app.api.routes.cloud import _resolve_cloud_filament


class _FakeCloud:
    """Counts cloud calls; blocks in get_setting_detail until released."""

    def __init__(self, gate: asyncio.Event, *, fail: bool = False):
        self.calls = 0
        self._gate = gate
        self._fail = fail

    async def get_setting_detail(self, api_setting_id):
        self.calls += 1
        await self._gate.wait()
        if self._fail:
            raise RuntimeError("cloud down")
        return {"name": "PLA Matte", "setting": {"pressure_advance": "0.021"}}


@pytest.fixture(autouse=True)
def _clear_state():
    cloud_mod._filament_cache.clear()
    cloud_mod._filament_inflight.clear()
    yield
    cloud_mod._filament_cache.clear()
    cloud_mod._filament_inflight.clear()


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_cloud_call():
    gate = asyncio.Event()
    cloud = _FakeCloud(gate)

    leader = asyncio.create_task(_resolve_cloud_filament("GFSA00", cloud))
    await asyncio.sleep(0)  # let the leader register its in-flight future
    follower = asyncio.create_task(_resolve_cloud_filament("GFSA00", cloud))
    await asyncio.sleep(0)  # let the follower attach to the leader's future

    gate.set()
    leader_info, follower_info = await asyncio.gather(leader, follower)

    assert cloud.calls == 1, "the two concurrent misses did not coalesce"
    assert leader_info == {"name": "PLA Matte", "k": 0.021}
    assert follower_info == leader_info
    assert cloud_mod._filament_cache["GFSA00"] == leader_info
    assert "GFSA00" not in cloud_mod._filament_inflight  # cleaned up


@pytest.mark.asyncio
async def test_cache_hit_skips_cloud_entirely():
    gate = asyncio.Event()
    gate.set()
    cloud = _FakeCloud(gate)
    cloud_mod._filament_cache["GFSA00"] = {"name": "cached", "k": None}

    info = await _resolve_cloud_filament("GFSA00", cloud)

    assert info == {"name": "cached", "k": None}
    assert cloud.calls == 0


@pytest.mark.asyncio
async def test_failed_fetch_returns_none_and_leaves_no_inflight():
    gate = asyncio.Event()
    gate.set()
    cloud = _FakeCloud(gate, fail=True)

    info = await _resolve_cloud_filament("GFSA00", cloud)

    assert info is None
    assert "GFSA00" not in cloud_mod._filament_cache
    assert "GFSA00" not in cloud_mod._filament_inflight
