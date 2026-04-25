"""Unit tests for the MJPEG fan-out broadcaster (#1089).

These tests do not touch ffmpeg or any printer — they drive a fake upstream
generator and assert subscriber/pump lifecycle behaviour.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from backend.app.services import camera_fanout
from backend.app.services.camera_fanout import (
    MjpegBroadcaster,
    get_or_create_broadcaster,
    iter_subscriber,
    shutdown_all_broadcasters,
    shutdown_broadcaster,
)

pytestmark = pytest.mark.asyncio


# Speed up grace-window tests so the suite stays fast. The default 5s grace
# is overkill for unit tests; we patch it down to a few ms.
@pytest.fixture(autouse=True)
def _short_grace(monkeypatch):
    monkeypatch.setattr(camera_fanout, "_GRACE_SECONDS", 0.05)


@pytest.fixture(autouse=True)
async def _clean_registry():
    """Reset the global broadcaster registry between tests."""
    await shutdown_all_broadcasters()
    yield
    await shutdown_all_broadcasters()


def _make_factory(
    chunks: list[bytes],
    *,
    delay: float = 0.0,
    pump_started: asyncio.Event | None = None,
    pump_count: list[int] | None = None,
):
    """Build an upstream factory that yields a fixed list of chunks."""

    async def factory(disconnect: asyncio.Event) -> AsyncGenerator[bytes, None]:
        if pump_started is not None:
            pump_started.set()
        if pump_count is not None:
            pump_count[0] += 1
        for chunk in chunks:
            if disconnect.is_set():
                return
            if delay:
                try:
                    await asyncio.wait_for(disconnect.wait(), timeout=delay)
                    return
                except asyncio.TimeoutError:
                    pass
            yield chunk

    return factory


# ---------------------------------------------------------------------------
# Single subscriber
# ---------------------------------------------------------------------------


async def test_single_subscriber_receives_all_frames():
    bc = MjpegBroadcaster("p1", _make_factory([b"a", b"b", b"c"], delay=0.005))
    queue = await bc.subscribe()

    received = []
    for _ in range(3):
        received.append(await asyncio.wait_for(queue.get(), timeout=1.0))

    assert received == [b"a", b"b", b"c"]
    await bc.force_shutdown()


# ---------------------------------------------------------------------------
# Multiple subscribers share one upstream
# ---------------------------------------------------------------------------


async def test_multiple_subscribers_share_single_upstream():
    pump_count = [0]
    bc = MjpegBroadcaster(
        "p1",
        _make_factory([b"f1", b"f2", b"f3"], delay=0.01, pump_count=pump_count),
    )

    q1 = await bc.subscribe()
    q2 = await bc.subscribe()
    q3 = await bc.subscribe()

    # Each subscriber must receive each frame exactly once.
    for q in (q1, q2, q3):
        received = []
        for _ in range(3):
            received.append(await asyncio.wait_for(q.get(), timeout=1.0))
        assert received == [b"f1", b"f2", b"f3"]

    # Only ONE upstream pump ever ran — that is the entire point of the bug fix.
    assert pump_count[0] == 1
    await bc.force_shutdown()


# ---------------------------------------------------------------------------
# Slow subscriber should not block fast subscribers
# ---------------------------------------------------------------------------


async def test_slow_subscriber_does_not_block_others():
    # Generate more frames than the queue depth so a non-draining queue is
    # guaranteed to fill up.
    chunks = [bytes([i % 256]) for i in range(50)]
    bc = MjpegBroadcaster("p1", _make_factory(chunks, delay=0.001))

    slow = await bc.subscribe()
    fast = await bc.subscribe()

    # Drain `fast` quickly; never read from `slow`. The fast subscriber must
    # still get every frame even though `slow` is wedged.
    received_fast = []
    for _ in range(50):
        received_fast.append(await asyncio.wait_for(fast.get(), timeout=2.0))

    assert received_fast == chunks
    # Slow subscriber's queue should be at most _SUBSCRIBER_QUEUE_SIZE — older
    # frames were dropped, not stuffed indefinitely.
    assert slow.qsize() <= camera_fanout._SUBSCRIBER_QUEUE_SIZE
    await bc.force_shutdown()


# ---------------------------------------------------------------------------
# Last-subscriber-leaves grace window
# ---------------------------------------------------------------------------


async def test_pump_torn_down_after_last_subscriber_leaves(monkeypatch):
    monkeypatch.setattr(camera_fanout, "_GRACE_SECONDS", 0.05)
    pump_count = [0]
    # Long upstream so we know it's still running until disconnect signals it.
    bc = MjpegBroadcaster(
        "p1",
        _make_factory([b"x"] * 1000, delay=0.05, pump_count=pump_count),
    )

    queue = await bc.subscribe()
    # Read a couple of frames.
    await asyncio.wait_for(queue.get(), timeout=1.0)
    await bc.unsubscribe(queue)

    # Wait for grace window to elapse + a hair more.
    await asyncio.sleep(0.2)

    assert bc.subscriber_count == 0
    assert bc.stopped is True
    assert pump_count[0] == 1


async def test_grace_window_cancelled_on_rejoin(monkeypatch):
    monkeypatch.setattr(camera_fanout, "_GRACE_SECONDS", 0.1)
    pump_count = [0]
    bc = MjpegBroadcaster(
        "p1",
        _make_factory([b"x"] * 1000, delay=0.02, pump_count=pump_count),
    )

    q1 = await bc.subscribe()
    await asyncio.wait_for(q1.get(), timeout=1.0)
    await bc.unsubscribe(q1)

    # Rejoin BEFORE grace expires — pump should keep running.
    await asyncio.sleep(0.02)
    q2 = await bc.subscribe()
    # Settle past the original grace deadline.
    await asyncio.sleep(0.2)

    # Pump still alive, only one upstream connection ever opened.
    assert bc.stopped is False
    assert pump_count[0] == 1
    # And the second subscriber is still receiving frames.
    await asyncio.wait_for(q2.get(), timeout=1.0)
    await bc.force_shutdown()


# ---------------------------------------------------------------------------
# Force shutdown wakes subscribers
# ---------------------------------------------------------------------------


async def test_force_shutdown_signals_subscribers():
    bc = MjpegBroadcaster("p1", _make_factory([b"x"] * 1000, delay=0.05))
    queue = await bc.subscribe()
    await asyncio.wait_for(queue.get(), timeout=1.0)

    await bc.force_shutdown()

    # Subscriber's queue should contain the upstream-gone sentinel (or be
    # drained); either way a get() must complete promptly.
    sentinel = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert sentinel == camera_fanout._UPSTREAM_GONE
    assert bc.stopped is True


# ---------------------------------------------------------------------------
# iter_subscriber helper exits cleanly on upstream-gone and disconnect
# ---------------------------------------------------------------------------


async def test_iter_subscriber_exits_on_upstream_gone():
    bc = MjpegBroadcaster("p1", _make_factory([b"a", b"b"], delay=0.005))
    queue = await bc.subscribe()

    received = []
    async for chunk in iter_subscriber(bc, queue):
        received.append(chunk)
    # Pump exited after yielding two chunks; iter_subscriber must return.
    assert received == [b"a", b"b"]
    # Helper unsubscribed us on the way out.
    assert bc.subscriber_count == 0


async def test_iter_subscriber_exits_on_client_disconnect():
    bc = MjpegBroadcaster("p1", _make_factory([b"x"] * 1000, delay=0.02))
    queue = await bc.subscribe()

    seen = 0

    async def is_disconnected() -> bool:
        return seen >= 2  # Pretend the client left after 2 frames.

    async for _chunk in iter_subscriber(bc, queue, is_disconnected=is_disconnected):
        seen += 1
        if seen >= 5:  # Defensive cap so a buggy iterator can't run forever.
            break

    assert seen == 2
    assert bc.subscriber_count == 0
    await bc.force_shutdown()


# ---------------------------------------------------------------------------
# Registry: stopped broadcasters get replaced
# ---------------------------------------------------------------------------


async def test_registry_replaces_stopped_broadcaster():
    factory_a = _make_factory([b"a"] * 1000, delay=0.02)
    factory_b = _make_factory([b"b"] * 1000, delay=0.02)

    bc1 = await get_or_create_broadcaster("p1", factory_a)
    q1 = await bc1.subscribe()
    await asyncio.wait_for(q1.get(), timeout=1.0)
    await shutdown_broadcaster("p1")
    assert bc1.stopped is True

    # New subscription must get a fresh broadcaster.
    bc2 = await get_or_create_broadcaster("p1", factory_b)
    assert bc2 is not bc1
    q2 = await bc2.subscribe()
    chunk = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert chunk == b"b"
    await shutdown_broadcaster("p1")


# ---------------------------------------------------------------------------
# Audit findings: subscribe-after-grace-stops contract + unsubscribe count
# ---------------------------------------------------------------------------


async def test_subscribe_to_stopped_raises_so_route_can_retry():
    """Contract: subscribe() raises RuntimeError when called on a stopped
    broadcaster. The route relies on this signal to re-fetch the registry
    entry (which will then mint a fresh broadcaster) instead of subscribing
    to a corpse.
    """
    bc = MjpegBroadcaster("p1", _make_factory([b"x"], delay=0.005))
    await bc.force_shutdown()
    assert bc.stopped is True

    with pytest.raises(RuntimeError):
        await bc.subscribe()


async def test_unsubscribe_returns_remaining_count_atomically():
    """Two subscribers leaving simultaneously must report distinct remaining
    counts (1 then 0), not both report 0 due to a race between unsubscribe
    and reading subscriber_count after the fact.
    """
    bc = MjpegBroadcaster("p1", _make_factory([b"x"] * 1000, delay=0.05))
    q1 = await bc.subscribe()
    q2 = await bc.subscribe()

    # Run both unsubscribes concurrently. Each should return its own
    # post-removal count.
    counts = await asyncio.gather(bc.unsubscribe(q1), bc.unsubscribe(q2))
    assert sorted(counts) == [0, 1], f"expected one unsubscribe to see 1 remaining and the other to see 0, got {counts}"
    await bc.force_shutdown()


async def test_unsubscribe_idempotent_returns_current_count():
    """Double-unsubscribe (e.g. shutdown raced with iter_subscriber finally)
    must not corrupt state; second call returns whatever the count is now.
    """
    bc = MjpegBroadcaster("p1", _make_factory([b"x"] * 1000, delay=0.05))
    q1 = await bc.subscribe()
    await bc.subscribe()  # q2 stays subscribed; we only care about removal of q1

    first = await bc.unsubscribe(q1)
    again = await bc.unsubscribe(q1)  # already gone
    assert first == 1
    assert again == 1  # q2 is still there
    await bc.force_shutdown()


async def test_force_shutdown_then_subscribe_via_registry_works():
    """Simulates the route's retry path: a viewer calls subscribe(), gets
    RuntimeError, calls get_or_create_broadcaster again, and successfully
    subscribes to the fresh broadcaster.
    """
    factory = _make_factory([b"hello"] * 1000, delay=0.02)
    bc1 = await get_or_create_broadcaster("p1", factory)
    # Mark the registered broadcaster stopped to simulate the grace teardown
    # winning the race against a new subscriber.
    await bc1.force_shutdown()

    # First subscribe attempt would raise on bc1; the registry replaces it.
    bc2 = await get_or_create_broadcaster("p1", factory)
    assert bc2 is not bc1
    queue = await bc2.subscribe()
    chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert chunk == b"hello"
    await shutdown_broadcaster("p1")
