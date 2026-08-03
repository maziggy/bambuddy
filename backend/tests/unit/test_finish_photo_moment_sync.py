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
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app import main as main_module
from backend.app.main import on_finish_photo_moment
from backend.app.services import print_dispatch_context


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
    print_dispatch_context.clear(7)
    yield
    main_module._stage22_finish_in_flight.clear()
    main_module._stage22_finish_frames.clear()
    main_module._inprint_frame_bank.clear()
    main_module._inprint_frame_bank_ts.clear()
    print_dispatch_context.clear(7)


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

    # #2547: default the plate restore to "print height unknown", so tests that
    # aren't about the restore never reach the G-code path. Tests that ARE about
    # it override these two.
    async def _no_height(_printer_id, _data, _logger):
        return None

    async def _not_blocked(_printer_id):
        return False

    monkeypatch.setattr(main_module, "_max_z_for_current_print", _no_height)
    monkeypatch.setattr(main_module, "_plate_restore_is_blocked_by_queue", _not_blocked)
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


async def test_finish_state_prefers_banked_frame_when_end_gcode_was_injected(patched_env, monkeypatch):
    """#1867: when Bambuddy injected End G-code, a SwapMod snippet may already
    have ejected the plate by FINISH — so the banked in-print frame is used and
    the live grab must not run."""
    print_dispatch_context.mark_pending(patched_env.id)
    print_dispatch_context.adopt(patched_env.id)
    main_module._inprint_frame_bank[patched_env.id] = b"\xff\xd8banked"

    live_called = {"n": 0}

    async def _live(**_kwargs):
        live_called["n"] += 1
        return b"\xff\xd8live-post-swap"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8banked"
    assert live_called["n"] == 0


async def test_finish_state_grabs_live_when_no_end_gcode_was_injected(patched_env, monkeypatch):
    """#2547: the ordinary case. Nothing moved the plate, the toolhead is
    parked, and the print is still sitting there — so the live frame is the
    finished print, and a banked mid-print frame must NOT win over it.

    Preferring the bank here unconditionally, which is what this code used to
    do, is how the H2C shipped a photo with the toolhead over the part."""
    main_module._inprint_frame_bank[patched_env.id] = b"\xff\xd8banked-midprint"

    async def _live(**_kwargs):
        return b"\xff\xd8live-finished-print"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8live-finished-print"


async def test_finish_state_falls_back_to_live_when_bank_is_empty(patched_env, monkeypatch):
    """End G-code was injected but nothing was ever banked (feature just
    enabled, tiny print, capture failures). Degrade to a live grab rather than
    sending a text-only notification."""
    print_dispatch_context.mark_pending(patched_env.id)
    print_dispatch_context.adopt(patched_env.id)

    async def _live(**_kwargs):
        return b"\xff\xd8live"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8live"


async def test_stage_22_trigger_ignores_bank(patched_env, monkeypatch):
    """The `stage_22` trigger fires before any End G-code and gives cleaner
    (parked-toolhead, plate-still-up) framing via a live grab — the bank is only
    for the post-swap `finish_state` path, so it must be ignored here."""
    print_dispatch_context.mark_pending(patched_env.id)
    print_dispatch_context.adopt(patched_env.id)
    main_module._inprint_frame_bank[patched_env.id] = b"\xff\xd8banked"

    async def _live(**_kwargs):
        return b"\xff\xd8live"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "stage_22"})

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


async def test_bank_throttles_on_the_last_layer_too(monkeypatch):
    """#2547: the last layer used to bypass the throttle so it always got a
    fresh frame. Now that progress advances also drive banking, that exemption
    would fire a camera grab on every percent tick of a multi-minute last layer
    — and each grab contends with the live view for the single RTSP slot."""
    counter = _bank_env(monkeypatch, total_layers=10)
    await main_module._maybe_bank_inprint_frame(3, 5)  # banks frame-1
    await main_module._maybe_bank_inprint_frame(3, 10)  # last layer, within 25s
    assert counter["n"] == 1
    assert main_module._inprint_frame_bank[3] == b"frame-1"


async def test_bank_refreshes_on_the_last_layer_once_the_throttle_elapses(monkeypatch):
    """The point of banking on progress: a three-minute last layer keeps
    refreshing instead of freezing at the moment that layer began."""
    counter = _bank_env(monkeypatch, total_layers=10)
    await main_module._maybe_bank_inprint_frame(3, 10)  # banks frame-1
    # Pretend the throttle window has passed, as it does mid-last-layer.
    main_module._inprint_frame_bank_ts[3] -= main_module._INPRINT_BANK_MIN_INTERVAL + 1
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


class TestStage22CacheHoldsExactlyOneRotation:
    """#2708. `_stage22_finish_frames` is fed from two kinds of source: live
    grabs, which are raw, and the #1867 in-print bank, whose bytes came from
    `_capture_snapshot_for_notification` and are therefore ALREADY rotated.
    The consumer cannot tell them apart, so the producer normalises: every
    entry in the cache has had the rotation applied exactly once.

    Rotating on the consumer side instead put two rotations on the banked
    path — at 180 degrees that is the reported bug reproduced exactly, and at
    90/270 it lands the photo 180 degrees out.
    """

    @staticmethod
    def _jpeg(width, height):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (width, height), (0, 0, 255)).save(buf, format="JPEG")
        return buf.getvalue()

    @staticmethod
    def _size(data):
        import io

        from PIL import Image

        return Image.open(io.BytesIO(data)).size

    async def test_a_live_grab_is_rotated_before_caching(self, patched_env, monkeypatch):
        monkeypatch.setattr(patched_env, "camera_rotation", 90, raising=False)
        raw = self._jpeg(64, 32)

        async def _capture(**_kwargs):
            return raw

        monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _capture)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        cached = main_module._stage22_finish_frames[patched_env.id]
        assert self._size(cached) == (32, 64)

    async def test_the_banked_frame_is_cached_verbatim(self, patched_env, monkeypatch):
        """The bank is filled by `_capture_snapshot_for_notification`, which
        rotates before it returns — so the producer must pass those bytes
        through untouched rather than rotating them a second time.

        Note this pins the invariant forward; it does not on its own prove the
        bug fixed, because the old producer didn't rotate anything either. The
        pair that discriminates is `test_a_live_grab_is_rotated_before_caching`
        (producer now rotates) plus the source guard below (consumer no longer
        does).
        """
        monkeypatch.setattr(patched_env, "camera_rotation", 90, raising=False)
        # #2547: the bank is only preferred when End G-code was injected.
        print_dispatch_context.mark_pending(patched_env.id)
        print_dispatch_context.adopt(patched_env.id)
        already_rotated = self._jpeg(32, 64)  # what one rotation of a 64x32 frame looks like
        main_module._inprint_frame_bank[patched_env.id] = already_rotated

        async def _capture(**_kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("the banked frame should have been preferred")

        monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _capture)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        cached = main_module._stage22_finish_frames[patched_env.id]
        assert cached is already_rotated
        assert self._size(cached) == (32, 64)

    async def test_a_stage22_grab_is_rotated_even_though_the_bank_is_full(self, patched_env, monkeypatch):
        """Only the `finish_state` trigger reads the bank. The `stage_22` and
        `last_layer` triggers take a live grab, which still needs rotating —
        a shared "did we use the bank" flag must not latch on the bank merely
        existing."""
        monkeypatch.setattr(patched_env, "camera_rotation", 90, raising=False)
        main_module._inprint_frame_bank[patched_env.id] = self._jpeg(999, 1)

        async def _capture(**_kwargs):
            return self._jpeg(64, 32)

        monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _capture)

        await on_finish_photo_moment(patched_env.id, {"trigger": "stage_22"})

        cached = main_module._stage22_finish_frames[patched_env.id]
        assert self._size(cached) == (32, 64)

    async def test_no_rotation_configured_caches_the_bytes_as_captured(self, patched_env, monkeypatch):
        raw = self._jpeg(64, 32)

        async def _capture(**_kwargs):
            return raw

        monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _capture)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert main_module._stage22_finish_frames[patched_env.id] is raw


def test_the_consumer_does_not_rotate_the_cached_frame():
    """The other half of the #2708 invariant, and the half with no runtime
    harness: `_background_finish_photo` is a closure nested inside
    `on_print_complete`, so nothing can drive its cached-frame branch
    directly. What it must NOT do is rotate what it pops from
    `_stage22_finish_frames` — the producer has already done that, and doing
    it again upside-downs the banked path, which is the bug this fixed.

    Checked against the source because the alternative is no check at all.
    """
    import ast
    from pathlib import Path

    main_py = Path(__file__).resolve().parents[2] / "app" / "main.py"
    assert main_py.exists(), f"guard is looking in the wrong place: {main_py}"
    tree = ast.parse(main_py.read_text())

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_apply_camera_rotation"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "cached_frame"
    ]

    assert not offenders, (
        f"main.py:{offenders} rotates the frame popped from _stage22_finish_frames. "
        "Those bytes are already rotated by on_finish_photo_moment (#2708); rotating "
        "again returns a 180-degree print to upside-down."
    )


class TestPlateRestore:
    """#2547 / #1145 / #1397 / #1565: put the plate back into camera framing.

    Bambu's end G-code drops the plate ~100mm as the last thing it does, so by
    FINISH the finished print sits well below where the camera frames it. The
    restore commands an ABSOLUTE Z back to just above the last printed layer.

    Absolute is the safety argument, and these tests pin it: the target is a
    height the toolhead was physically at seconds earlier, so it is inside the
    travel limits and leaves the nozzle above the part. It is also unambiguous
    across model families — Z is the nozzle-to-bed gap whether the bed moves or
    the toolhead does — so there is no sign to get wrong the way the relative
    bed-jog path had (#1334).
    """

    @pytest.fixture
    def printer_client(self, monkeypatch):
        sent: list[str] = []
        client = SimpleNamespace(
            state=SimpleNamespace(state="FINISH"),
            send_gcode=lambda gcode: (sent.append(gcode), True)[1],
        )
        monkeypatch.setattr(main_module.printer_manager, "get_client", lambda _pid: client)
        monkeypatch.setattr(main_module.asyncio, "sleep", AsyncMock())
        client.sent = sent
        return client

    async def test_commands_an_absolute_move_above_the_print(self, printer_client):
        ok = await main_module._restore_plate_for_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert ok is True
        assert printer_client.sent == ["G90\nG1 Z26.00 F600"]

    async def test_never_touches_m211(self, printer_client):
        """#2579: disabling soft endstops is what let a jog drive the nozzle
        into the bed. This path must not reintroduce it."""
        await main_module._restore_plate_for_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert not any("M211" in line for line in printer_client.sent)

    async def test_skipped_when_the_printer_is_no_longer_in_finish(self, printer_client):
        """The queue dispatches the next job the instant a print completes.
        Commanding a plate move into a starting print is not a race worth
        having, so state is re-read immediately before the move."""
        printer_client.state.state = "RUNNING"

        ok = await main_module._restore_plate_for_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert ok is False
        assert printer_client.sent == []

    async def test_skipped_when_the_printer_is_gone(self, monkeypatch):
        monkeypatch.setattr(main_module.printer_manager, "get_client", lambda _pid: None)

        ok = await main_module._restore_plate_for_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert ok is False

    async def test_reports_failure_when_the_send_fails(self, printer_client, monkeypatch):
        """A failed send means the plate never moved — the caller must not go on
        to owe it a move back down."""
        monkeypatch.setattr(printer_client, "send_gcode", lambda _g: False)

        ok = await main_module._restore_plate_for_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert ok is False

    def test_park_lowers_the_plate_again(self, printer_client):
        main_module._park_plate_after_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert printer_client.sent == ["G90\nG1 Z116.00 F600"]

    def test_park_skipped_once_the_next_print_has_started(self, printer_client):
        printer_client.state.state = "RUNNING"

        main_module._park_plate_after_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert printer_client.sent == []


class TestPlateRestoreWiring:
    """The restore only runs in the one situation it is correct for, and the
    plate always comes back down afterwards."""

    @pytest.fixture
    def restore_env(self, patched_env, monkeypatch):
        calls = {"restore": [], "park": [], "blocked": False, "height": 16.0}

        async def _height(_printer_id, _data, _logger):
            return calls["height"]

        async def _blocked(_printer_id):
            return calls["blocked"]

        async def _restore(printer_id, max_z, _logger):
            calls["restore"].append((printer_id, max_z))
            return True

        def _park(printer_id, max_z, _logger):
            calls["park"].append((printer_id, max_z))

        monkeypatch.setattr(main_module, "_max_z_for_current_print", _height)
        monkeypatch.setattr(main_module, "_plate_restore_is_blocked_by_queue", _blocked)
        monkeypatch.setattr(main_module, "_restore_plate_for_finish_photo", _restore)
        monkeypatch.setattr(main_module, "_park_plate_after_finish_photo", _park)

        async def _live(**_kwargs):
            return b"\xff\xd8live"

        monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)
        return calls

    async def test_restores_then_parks_on_the_finish_state_path(self, patched_env, restore_env):
        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == [(patched_env.id, 16.0)]
        assert restore_env["park"] == [(patched_env.id, 16.0)]

    async def test_not_restored_on_the_stage_22_path(self, patched_env, restore_env):
        """Stage 22 fires before the end G-code drops the plate — it is already
        where we want it, and moving it would only cost the settle delay."""
        await on_finish_photo_moment(patched_env.id, {"trigger": "stage_22"})

        assert restore_env["restore"] == []
        assert restore_env["park"] == []

    async def test_not_restored_when_the_banked_frame_is_used(self, patched_env, restore_env):
        """The plate has been swapped out — no move brings the print back."""
        print_dispatch_context.mark_pending(patched_env.id)
        print_dispatch_context.adopt(patched_env.id)
        main_module._inprint_frame_bank[patched_env.id] = b"\xff\xd8banked"

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == []
        assert restore_env["park"] == []

    async def test_not_restored_when_end_gcode_was_injected_but_the_bank_is_empty(self, patched_env, restore_env):
        """A plate-swap machine may have just ejected its plate. Even with no
        banked frame to fall back on, driving Z into whatever a swap mechanism
        is doing is not worth a photo of a bed we know may be bare."""
        print_dispatch_context.mark_pending(patched_env.id)
        print_dispatch_context.adopt(patched_env.id)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == []
        assert restore_env["park"] == []

    async def test_not_restored_when_the_print_height_is_unknown(self, patched_env, restore_env):
        restore_env["height"] = None

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == []
        assert restore_env["park"] == []

    async def test_not_restored_when_another_job_is_queued(self, patched_env, restore_env):
        restore_env["blocked"] = True

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == []
        assert restore_env["park"] == []

    async def test_not_restored_when_the_setting_is_off(self, patched_env, restore_env, monkeypatch):
        async def _get_setting(_db, key):
            if key == "capture_finish_photo":
                return "true"
            if key == "finish_photo_restore_plate":
                return "false"
            return None

        monkeypatch.setattr("backend.app.api.routes.settings.get_setting", _get_setting)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == []
        assert restore_env["park"] == []

    async def test_plate_is_parked_even_when_the_capture_throws(self, patched_env, restore_env, monkeypatch):
        """We raised it, so we owe the move back down — including when the grab
        between the two fails. Otherwise the user finds the print pinned under
        the nozzle."""

        async def _boom(**_kwargs):
            raise RuntimeError("camera gone")

        monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _boom)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == [(patched_env.id, 16.0)]
        assert restore_env["park"] == [(patched_env.id, 16.0)]

    async def test_producer_event_is_still_set_after_a_restore(self, patched_env, restore_env):
        """#1790: the consumer's bounded wait must be released on every exit,
        and the restore added a new path through the producer."""
        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert main_module._stage22_finish_in_flight[patched_env.id].is_set()


async def test_producer_wait_budget_covers_the_restore():
    """The consumer's wait has to outlast settle + a worst-case RTSP grab, and
    still finish inside the notification's own photo budget — otherwise the
    restore path is cut off by a timeout somewhere above it."""
    assert main_module._FINISH_PHOTO_PRODUCER_WAIT_SECONDS > main_module._PLATE_RESTORE_SETTLE_SECONDS + 15


class TestMaxZResolution:
    """#2547 safety: the height that becomes a Z-move target must provably
    belong to the print that just finished.

    A height from another print is the one failure mode that could drive the
    nozzle into the model — 20mm carried onto a 200mm print commands the plate
    up through the part. So the resolver refuses on every ambiguity rather than
    falling back to "whatever ran last on this printer".
    """

    @staticmethod
    def _archive(**overrides):
        base = {
            "id": 11,
            "file_path": "/data/archive/1/job/job.3mf",
            "plate_id": 1,
            "total_layers": 30,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    @pytest.fixture
    def resolver_env(self, monkeypatch):
        env = {"archive": self._archive(), "reported_layers": 30, "height": 16.0, "where": None}

        @asynccontextmanager
        async def _session():
            async def _execute(stmt):
                env["where"] = str(stmt)
                return SimpleNamespace(scalar_one_or_none=lambda: env["archive"])

            yield SimpleNamespace(execute=_execute)

        monkeypatch.setattr(main_module, "async_session", _session)
        monkeypatch.setattr(
            main_module.printer_manager,
            "get_client",
            lambda _pid: SimpleNamespace(state=SimpleNamespace(total_layers=env["reported_layers"])),
        )
        monkeypatch.setattr(
            "backend.app.utils.threemf_tools.extract_max_z_height_from_3mf",
            lambda _path, _plate: env["height"],
        )
        return env

    async def test_returns_the_height_when_name_and_layers_agree(self, resolver_env):
        height = await main_module._max_z_for_current_print(1, {"subtask_name": "job"}, logging.getLogger(__name__))
        assert height == 16.0

    async def test_refuses_when_the_print_has_no_name_to_match_on(self, resolver_env):
        """Without an identifier there is nothing to bind the archive to, and
        the query would degrade to 'the newest row for this printer'."""
        height = await main_module._max_z_for_current_print(1, {}, logging.getLogger(__name__))

        assert height is None
        assert resolver_env["where"] is None  # refused before touching the DB

    async def test_refuses_when_no_archive_matches_the_name(self, resolver_env):
        resolver_env["archive"] = None

        height = await main_module._max_z_for_current_print(1, {"subtask_name": "job"}, logging.getLogger(__name__))
        assert height is None

    async def test_refuses_when_the_layer_counts_disagree(self, resolver_env):
        """The corroboration check. The archive's layer count comes from the
        3MF; the printer's comes from MQTT. If two independent sources disagree,
        the row is not this print whatever its name says."""
        resolver_env["reported_layers"] = 240

        height = await main_module._max_z_for_current_print(1, {"subtask_name": "job"}, logging.getLogger(__name__))
        assert height is None

    async def test_proceeds_when_a_layer_count_is_simply_unknown(self, resolver_env):
        """Absent is not the same as contradictory — a print Bambuddy has no
        layer count for still gets its height, because the name matched."""
        resolver_env["reported_layers"] = 0
        assert (
            await main_module._max_z_for_current_print(1, {"subtask_name": "job"}, logging.getLogger(__name__)) == 16.0
        )

        resolver_env["reported_layers"] = 30
        resolver_env["archive"] = self._archive(total_layers=None)
        assert (
            await main_module._max_z_for_current_print(1, {"subtask_name": "job"}, logging.getLogger(__name__)) == 16.0
        )

    async def test_matches_by_equality_not_substring(self, resolver_env):
        """`LIKE %name%` would let "Cube" resolve to "Cube v2" — a different
        print, quite possibly a much taller one."""
        await main_module._max_z_for_current_print(1, {"subtask_name": "Cube"}, logging.getLogger(__name__))

        assert "LIKE" not in resolver_env["where"].upper()

    async def test_refuses_when_the_archive_has_no_file(self, resolver_env):
        resolver_env["archive"] = self._archive(file_path=None)

        height = await main_module._max_z_for_current_print(1, {"subtask_name": "job"}, logging.getLogger(__name__))
        assert height is None

    async def test_refuses_when_the_3mf_has_no_height(self, resolver_env):
        resolver_env["height"] = None

        height = await main_module._max_z_for_current_print(1, {"subtask_name": "job"}, logging.getLogger(__name__))
        assert height is None


class TestTimelapsePathPlateRestore:
    """#2547: the timelapse path falls through to a live grab whenever the
    video hasn't landed yet — the documented usual outcome on P1-series.

    `on_finish_photo_moment` returns early for those prints without raising the
    plate, so the photo that actually ships in the notification would be of an
    already-dropped plate. The consumer therefore does the restore itself, but
    only on that path — everywhere else the producer has already done it.
    """

    def test_notification_budget_outlasts_the_video_poll_plus_a_restore(self):
        """The wait has to cover polling for the video AND the restore that
        follows when it doesn't arrive. At the old flat 75s the fallback was
        cut off mid-settle, so the plate would have moved for a photo nobody
        was still waiting for."""
        assert (
            main_module._FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS + main_module._FINISH_PHOTO_PRODUCER_WAIT_SECONDS
            > main_module._FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS + main_module._PLATE_RESTORE_SETTLE_SECONDS + 15
        )

    async def test_producer_skips_the_restore_when_a_timelapse_was_recording(self, patched_env, monkeypatch):
        """The producer returns before any of the restore code — the consumer
        owns it on this path, and doing it in both would move the plate twice."""
        moved = []

        async def _restore(printer_id, max_z, _logger):
            moved.append((printer_id, max_z))
            return True

        async def _height(_printer_id, _data, _logger):
            return 16.0

        monkeypatch.setattr(main_module, "_restore_plate_for_finish_photo", _restore)
        monkeypatch.setattr(main_module, "_max_z_for_current_print", _height)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state", "timelapse_was_active": True})

        assert moved == []
