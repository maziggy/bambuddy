"""Regression tests for the #1790 producer-consumer synchronization.

`on_finish_photo_moment` (producer) and `_background_finish_photo`
(consumer) are dispatched back-to-back on the FINISH-state fallback path
(`bambu_mqtt.py:3258-3297`). Before #1790, the consumer ran a single
`pop()` on `_stage22_finish_frames` with no wait — racing past the
producer with an empty result, then doing its own RTSP grab that
collided with the producer's still-in-flight grab (Bambu printers allow
one RTSP client). Net result: a captured frame was logged, the cache
was populated ~1s later, but the notification went text-only.

The fix is an `asyncio.Event` per printer registered in
`_stage22_finish_in_flight` by the producer and awaited (with timeout)
by the consumer. These tests pin the producer side of that contract.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app import main as main_module
from backend.app.main import on_finish_photo_moment


@asynccontextmanager
async def _fake_session(printer):
    """Async-session stub that returns `printer` from scalar_one_or_none()."""
    result = SimpleNamespace(scalar_one_or_none=lambda: printer)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    yield session


@pytest.fixture
def fake_printer():
    return SimpleNamespace(
        id=7,
        ip_address="192.0.2.7",
        access_code="x",
        model="X1C",
        external_camera_enabled=False,
        external_camera_url=None,
        external_camera_type=None,
        external_camera_snapshot_url=None,
    )


@pytest.fixture(autouse=True)
def _clean_state():
    """Don't leak event/cache dict entries across tests."""
    main_module._stage22_finish_in_flight.clear()
    main_module._stage22_finish_frames.clear()
    yield
    main_module._stage22_finish_in_flight.clear()
    main_module._stage22_finish_frames.clear()


@pytest.fixture
def patched_env(fake_printer, monkeypatch):
    monkeypatch.setattr(main_module, "async_session", lambda: _fake_session(fake_printer))

    async def _get_setting(_db, key):
        if key == "capture_finish_photo":
            return "true"
        return None

    monkeypatch.setattr(
        "backend.app.api.routes.settings.get_setting",
        _get_setting,
    )
    monkeypatch.setattr(
        "backend.app.api.routes.camera.get_buffered_frame",
        lambda _pid: None,
    )
    return fake_printer


async def test_event_registered_before_first_await(patched_env, monkeypatch):
    """The consumer needs to find the event the moment it polls — that
    means registration must complete BEFORE any `await` yields control
    back to the loop."""
    # Slow the first await (DB session entry) so we can observe the dict
    # before the producer makes any real progress.
    seen_during_capture = {}

    async def _slow_capture(**_kwargs):
        seen_during_capture["registered"] = patched_env.id in main_module._stage22_finish_in_flight
        await asyncio.sleep(0)
        return b"\xff\xd8frame"

    monkeypatch.setattr(
        "backend.app.services.camera.capture_camera_frame_bytes",
        _slow_capture,
    )

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert seen_during_capture["registered"] is True


async def test_event_set_after_successful_capture(patched_env, monkeypatch):
    async def _capture(**_kwargs):
        return b"\xff\xd8frame"

    monkeypatch.setattr(
        "backend.app.services.camera.capture_camera_frame_bytes",
        _capture,
    )

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    event = main_module._stage22_finish_in_flight[patched_env.id]
    assert event.is_set()
    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8frame"


async def test_event_set_when_capture_returns_no_frame(patched_env, monkeypatch):
    """Producer gives up (RTSP timeout, no buffered frame, no external
    camera) — consumer must NOT wait the full 20s for nothing."""

    async def _capture(**_kwargs):
        return None

    monkeypatch.setattr(
        "backend.app.services.camera.capture_camera_frame_bytes",
        _capture,
    )

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    event = main_module._stage22_finish_in_flight[patched_env.id]
    assert event.is_set()
    assert patched_env.id not in main_module._stage22_finish_frames


async def test_event_set_even_when_capture_raises(patched_env, monkeypatch):
    """Producer hit a bug or network error — `finally` still has to
    release the consumer."""

    async def _capture(**_kwargs):
        raise RuntimeError("camera went away")

    monkeypatch.setattr(
        "backend.app.services.camera.capture_camera_frame_bytes",
        _capture,
    )

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    event = main_module._stage22_finish_in_flight[patched_env.id]
    assert event.is_set()


async def test_no_event_when_timelapse_was_active(patched_env):
    """On the timelapse-on path the consumer takes the
    `_capture_finish_photo_from_timelapse` branch and shouldn't be
    blocked by a producer wait — the producer doesn't enter the
    lifecycle."""
    await on_finish_photo_moment(
        patched_env.id,
        {"trigger": "stage_22", "timelapse_was_active": True},
    )

    assert patched_env.id not in main_module._stage22_finish_in_flight


async def test_event_set_when_capture_setting_disabled(patched_env, monkeypatch):
    """Even on the early-return-before-capture path, the event must be
    released so the consumer doesn't hang on a no-op producer."""

    async def _disabled_setting(_db, _key):
        return "false"

    monkeypatch.setattr(
        "backend.app.api.routes.settings.get_setting",
        _disabled_setting,
    )

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    event = main_module._stage22_finish_in_flight[patched_env.id]
    assert event.is_set()


async def test_consumer_wait_unblocked_when_producer_completes(patched_env, monkeypatch):
    """End-to-end sync check: a consumer-style waiter awaiting the
    event finishes promptly once the producer's finally fires."""

    async def _capture(**_kwargs):
        await asyncio.sleep(0.05)
        return b"\xff\xd8frame"

    monkeypatch.setattr(
        "backend.app.services.camera.capture_camera_frame_bytes",
        _capture,
    )

    producer = asyncio.create_task(on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"}))

    await asyncio.sleep(0)  # let the producer register

    event = main_module._stage22_finish_in_flight[patched_env.id]
    await asyncio.wait_for(event.wait(), timeout=1.0)

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8frame"
    await producer
