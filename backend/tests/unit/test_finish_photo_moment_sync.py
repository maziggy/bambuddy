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
    main_module._inprint_frame_bank.clear()
    main_module._inprint_frame_bank_ts.clear()
    yield
    main_module._stage22_finish_in_flight.clear()
    main_module._stage22_finish_frames.clear()
    main_module._inprint_frame_bank.clear()
    main_module._inprint_frame_bank_ts.clear()


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


async def test_finish_state_prefers_banked_frame(patched_env, monkeypatch):
    """#1867: on the FINISH-state fallback (stage-22-less firmware, e.g. A1
    Mini) a live grab captures the post-swap plate. When a banked in-print
    frame exists it must be used instead, and the live grab must not run."""
    main_module._inprint_frame_bank[patched_env.id] = b"\xff\xd8banked"

    live_called = {"n": 0}

    async def _live(**_kwargs):
        live_called["n"] += 1
        return b"\xff\xd8live-post-swap"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8banked"
    assert live_called["n"] == 0


async def test_finish_state_falls_back_to_live_when_no_bank(patched_env, monkeypatch):
    """No banked frame (feature just enabled, tiny print, capture failures) —
    the FINISH-state path still live-grabs so we degrade to the old behaviour
    rather than sending a text-only notification."""

    async def _live(**_kwargs):
        return b"\xff\xd8live"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8live"


async def test_last_layer_trigger_ignores_bank(patched_env, monkeypatch):
    """The `last_layer` trigger fires before the swap and gives cleaner
    (parked-toolhead) framing via a live grab — the bank is only for the
    post-swap `finish_state` fallback, so it must be ignored here."""
    main_module._inprint_frame_bank[patched_env.id] = b"\xff\xd8banked"

    async def _live(**_kwargs):
        return b"\xff\xd8live"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "last_layer"})

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8live"


# --- #1867 banking helper (_maybe_bank_inprint_frame) --------------------


def _bank_env(monkeypatch, *, state="RUNNING", sub_stage=0, total_layers=10, printer=object()):
    """Wire printer_manager.get_client + the snapshot capture for the bank
    helper. Capture returns a distinct frame per call so updates are visible."""
    client = SimpleNamespace(
        state=SimpleNamespace(state=state, mc_print_sub_stage=sub_stage, total_layers=total_layers)
    )
    monkeypatch.setattr(main_module.printer_manager, "get_client", lambda _pid: client)
    monkeypatch.setattr(main_module, "async_session", lambda: _fake_session(printer))

    counter = {"n": 0}

    async def _capture(_pid, _printer, _logger):
        counter["n"] += 1
        return f"frame-{counter['n']}".encode()

    monkeypatch.setattr(main_module, "_capture_snapshot_for_notification", _capture)
    return counter


async def test_bank_stores_frame_while_printing(monkeypatch):
    _bank_env(monkeypatch)
    await main_module._maybe_bank_inprint_frame(3, 5)
    assert main_module._inprint_frame_bank[3] == b"frame-1"


async def test_bank_throttles_within_interval(monkeypatch):
    counter = _bank_env(monkeypatch)
    await main_module._maybe_bank_inprint_frame(3, 5)  # banks frame-1
    await main_module._maybe_bank_inprint_frame(3, 6)  # within 25s -> skipped
    assert counter["n"] == 1
    assert main_module._inprint_frame_bank[3] == b"frame-1"


async def test_bank_always_refreshes_on_last_layer(monkeypatch):
    counter = _bank_env(monkeypatch, total_layers=10)
    await main_module._maybe_bank_inprint_frame(3, 5)  # banks frame-1
    # Last layer bypasses the throttle for the best final framing.
    await main_module._maybe_bank_inprint_frame(3, 10)
    assert counter["n"] == 2
    assert main_module._inprint_frame_bank[3] == b"frame-2"


async def test_bank_skips_when_not_running(monkeypatch):
    """End G-code (plate swap) runs after RUNNING ends — the bank must not
    update then, which is what freezes it on the finished print."""
    _bank_env(monkeypatch, state="FINISH")
    await main_module._maybe_bank_inprint_frame(3, 10)
    assert 3 not in main_module._inprint_frame_bank


async def test_bank_skips_during_calibration_substage(monkeypatch):
    """layer_num ticks during pre-print calibration (non-zero sub-stage) —
    banking then would capture an empty bed."""
    _bank_env(monkeypatch, sub_stage=14)
    await main_module._maybe_bank_inprint_frame(3, 2)
    assert 3 not in main_module._inprint_frame_bank
