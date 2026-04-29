"""T-Gap 6: WeakValueDictionary lock concurrency tests for merge_spool_extra."""

import asyncio

import pytest

from backend.app.services.spoolman import SpoolmanClient


class TestExtraLock:
    """Verify extra_lock uses WeakValueDictionary and same object is returned within scope."""

    def test_extra_lock_same_instance_within_scope(self):
        """Two calls with the same spool_id return the same lock object."""
        client = SpoolmanClient("http://localhost:7912")
        lock_a = client.extra_lock(1)
        lock_b = client.extra_lock(1)
        assert lock_a is lock_b

    def test_extra_lock_different_ids_different_instances(self):
        """Different spool IDs return different locks."""
        client = SpoolmanClient("http://localhost:7912")
        lock_1 = client.extra_lock(1)
        lock_2 = client.extra_lock(2)
        assert lock_1 is not lock_2
        # Keep references alive
        _ = lock_1, lock_2

    def test_extra_lock_released_when_no_reference(self):
        """Lock is garbage-collected once no reference is held (WeakValueDictionary)."""
        import gc
        import weakref

        client = SpoolmanClient("http://localhost:7912")
        lock = client.extra_lock(42)
        ref = weakref.ref(lock)
        del lock
        gc.collect()
        # Lock should have been evicted from the WeakValueDictionary
        assert ref() is None
        assert 42 not in client._extra_locks

    @pytest.mark.asyncio
    async def test_concurrent_calls_serialized(self):
        """Concurrent calls to extra_lock with same spool_id are serialized."""
        client = SpoolmanClient("http://localhost:7912")
        results = []

        async def hold_lock():
            lock = client.extra_lock(99)
            async with lock:
                results.append("enter")
                await asyncio.sleep(0.01)
                results.append("exit")

        await asyncio.gather(hold_lock(), hold_lock())
        # enter/exit pairs must not interleave
        assert results == ["enter", "exit", "enter", "exit"]
