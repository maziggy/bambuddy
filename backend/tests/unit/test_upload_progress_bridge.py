"""Throttle contract for the scheduler's upload-progress bridge.

The legacy bg-dispatch path (last seen in
backend/app/services/background_dispatch.py before commit 61c8898b) used:
    - 200 ms time gate
    - 256 KB byte gate
    - always emit on first call and at uploaded >= total

The scheduler-driven dispatch must feel identical to the pre-#1625 path,
so the throttle here mirrors that 1:1.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.services.print_scheduler import _UploadProgressBridge


@pytest.mark.asyncio
async def test_first_call_always_emits(monkeypatch):
    bridge = _UploadProgressBridge(user_id=1, queue_item_id=1)
    bridge._loop = asyncio.get_running_loop()
    calls: list[tuple[int, int]] = []

    def fake_run(coro, loop):  # noqa: ARG001
        coro.close()
        calls.append((1, 1))

    monkeypatch.setattr("backend.app.services.print_scheduler.asyncio.run_coroutine_threadsafe", fake_run)

    # First chunk, tiny payload — must emit so the user sees something
    # even for sub-chunk-size files where the upload finishes inside the
    # very first FTP callback.
    bridge(8192, 16384)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_emit_at_completion_even_under_throttle_gates(monkeypatch):
    bridge = _UploadProgressBridge(user_id=1, queue_item_id=2)
    bridge._loop = asyncio.get_running_loop()
    calls: list[int] = []

    def fake_run(coro, loop):  # noqa: ARG001
        coro.close()
        calls.append(1)

    monkeypatch.setattr("backend.app.services.print_scheduler.asyncio.run_coroutine_threadsafe", fake_run)

    # First call, force pretend-recent emit so neither time nor byte gate fires.
    bridge(50_000, 1_000_000)
    bridge._last_emit_monotonic = float("inf") * 0 + 1e18  # implausibly recent
    bridge._last_emit_bytes = 50_000

    # Mid-upload chunk well under both gates — would normally skip.
    bridge(60_000, 1_000_000)

    # Completion — must always emit so the bar locks at 100%.
    bridge(1_000_000, 1_000_000)

    # First + completion. Mid-upload chunk skipped (last_emit_monotonic is
    # in the future, byte step is only 10 KB).
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_emit_after_256kb_step_even_under_time_gate(monkeypatch):
    bridge = _UploadProgressBridge(user_id=1, queue_item_id=3)
    bridge._loop = asyncio.get_running_loop()
    calls: list[int] = []

    def fake_run(coro, loop):  # noqa: ARG001
        coro.close()
        calls.append(1)

    monkeypatch.setattr("backend.app.services.print_scheduler.asyncio.run_coroutine_threadsafe", fake_run)

    bridge(8192, 10_000_000)  # first emit
    # Pretend time gate not met but byte gate IS met (256 KB further).
    bridge._last_emit_monotonic = 1e18
    bridge._last_emit_bytes = 8192

    bridge(8192 + 256 * 1024 + 1, 10_000_000)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_no_emit_when_total_bytes_zero(monkeypatch):
    bridge = _UploadProgressBridge(user_id=1, queue_item_id=4)
    bridge._loop = asyncio.get_running_loop()
    calls: list[int] = []

    def fake_run(coro, loop):  # noqa: ARG001
        coro.close()
        calls.append(1)

    monkeypatch.setattr("backend.app.services.print_scheduler.asyncio.run_coroutine_threadsafe", fake_run)

    bridge(0, 0)
    bridge(100, 0)

    assert calls == []


def test_silent_when_no_running_loop_captured():
    """Constructed outside an asyncio loop — the bridge captures None and
    every call is a no-op."""
    bridge = _UploadProgressBridge(user_id=1, queue_item_id=5)
    assert bridge._loop is None
    bridge(1, 100)  # must not raise
