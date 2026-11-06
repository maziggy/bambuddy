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
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app import main as main_module
from backend.app.core.database import Base
from backend.app.main import on_finish_photo_moment
from backend.app.models.archive import PrintArchive
from backend.app.services import print_dispatch_context

HEIGHTS = main_module.PlateHeights(16.0, 114.0)


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
    async def _no_height(_printer_id, _data, _logger, _archive_id, _plate_id=None):
        return None

    async def _not_blocked(_printer_id):
        return False

    monkeypatch.setattr(main_module, "_plate_heights_for_current_print", _no_height)
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

    counter = {"n": 0, "light": []}

    async def _capture(_pid, _printer, _logger, *, light=True):
        counter["n"] += 1
        counter["light"].append(light)
        return f"frame-{counter['n']}".encode()

    monkeypatch.setattr(main_module, "_capture_snapshot_for_notification", _capture)
    return counter


async def test_bank_stores_frame_while_printing(monkeypatch):
    _bank_env(monkeypatch)
    await main_module._maybe_bank_inprint_frame(3, 5)
    assert main_module._inprint_frame_bank[3] == b"frame-1"


async def test_bank_leaves_the_chamber_light_alone(monkeypatch):
    """#1655: the bank grabs a frame every 25 seconds all through a print, so
    holding the chamber light for it would make the light flash."""
    counter = _bank_env(monkeypatch)
    await main_module._maybe_bank_inprint_frame(3, 5)
    assert counter["light"] == [False]


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

    On X1/P1/A1 Bambu's end G-code drops the plate ~100mm as the last thing it
    does, so by FINISH the finished print sits well below where the camera
    frames it. The restore commands an ABSOLUTE Z back to just above the last
    printed layer.

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

    def test_park_returns_the_plate_to_the_end_gcode_position(self, printer_client):
        """The park target is the Z the print's own end G-code left the plate
        at, sent as given. It used to be print top + 100 mm, which on a 238 mm
        H2C print drove the plate to the bottom of its travel: the firmware
        doesn't clamp MQTT G-code (#2579), and Bambu's template had stopped at
        print top + 2 mm for exactly that reason."""
        main_module._park_plate_after_finish_photo(7, 239.8, logging.getLogger(__name__))

        assert printer_client.sent == ["G90\nG1 Z239.80 F600"]

    def test_park_never_touches_m211(self, printer_client):
        main_module._park_plate_after_finish_photo(7, 114.0, logging.getLogger(__name__))

        assert not any("M211" in line for line in printer_client.sent)

    def test_park_skipped_once_the_next_print_has_started(self, printer_client):
        printer_client.state.state = "RUNNING"

        main_module._park_plate_after_finish_photo(7, 16.0, logging.getLogger(__name__))

        assert printer_client.sent == []


class TestPlateRestoreWiring:
    """The restore only runs in the one situation it is correct for, and the
    plate always comes back down afterwards."""

    @pytest.fixture
    def restore_env(self, patched_env, monkeypatch):
        calls = {
            "restore": [],
            "park": [],
            "blocked": False,
            "height": main_module.PlateHeights(16.0, 114.0),
            "bound": [],
        }

        async def _height(_printer_id, _data, _logger, archive_id, plate_id=None):
            calls["bound"].append((archive_id, plate_id))
            return calls["height"]

        async def _blocked(_printer_id):
            return calls["blocked"]

        async def _restore(printer_id, max_z, _logger):
            calls["restore"].append((printer_id, max_z))
            return True

        def _park(printer_id, end_z, _logger):
            calls["park"].append((printer_id, end_z))

        monkeypatch.setattr(main_module, "_plate_heights_for_current_print", _height)
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
        assert restore_env["park"] == [(patched_env.id, 114.0)]

    async def test_not_restored_when_the_end_gcode_left_the_plate_in_view(self, patched_env, restore_env):
        """Archive 454: the H2C end G-code ended 2 mm under the nozzle (Z239.8
        for a 237.8 mm print). Raising "back" to print top + 10 would have
        lowered it, and the park after that sent it to the bottom."""
        restore_env["height"] = main_module.PlateHeights(237.8, 239.8)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert restore_env["restore"] == []
        assert restore_env["park"] == []

    async def test_restored_only_when_the_plate_is_below_the_restore_height(self, patched_env, restore_env):
        for end_z, moved in ((26.0, False), (26.01, True)):
            restore_env["restore"].clear()
            restore_env["height"] = main_module.PlateHeights(16.0, end_z)

            await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

            assert bool(restore_env["restore"]) is moved, end_z

    async def test_reads_the_binding_before_on_print_complete_pops_it(self, patched_env, restore_env, monkeypatch):
        """#3240: on the FINISH-state path on_print_complete is dispatched right
        behind the producer and pops the print's archive binding and plate. The
        producer must have read them first, or the restore has nothing bound to
        move for and never runs."""

        async def _yielding_get_setting(_db, key):
            # A real settings read suspends; this is where on_print_complete
            # gets to run.
            await asyncio.sleep(0)
            return "true" if key == "capture_finish_photo" else None

        monkeypatch.setattr("backend.app.api.routes.settings.get_setting", _yielding_get_setting)
        main_module._active_prints[(patched_env.id, "job.3mf")] = 5
        main_module._print_plate_ids[(patched_env.id, 5)] = 3

        async def _on_print_complete_pops():
            main_module._active_prints.clear()
            main_module._print_plate_ids.clear()

        try:
            data = {"trigger": "finish_state", "filename": "job.3mf", "subtask_name": "job"}
            producer = asyncio.create_task(on_finish_photo_moment(patched_env.id, data))
            completion = asyncio.create_task(_on_print_complete_pops())
            await asyncio.gather(producer, completion)
        finally:
            main_module._active_prints.clear()
            main_module._print_plate_ids.clear()

        assert restore_env["bound"] == [(5, 3)]

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
        assert restore_env["park"] == [(patched_env.id, 114.0)]

    async def test_producer_event_is_still_set_after_a_restore(self, patched_env, restore_env):
        """#1790: the consumer's bounded wait must be released on every exit,
        and the restore added a new path through the producer."""
        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

        assert main_module._stage22_finish_in_flight[patched_env.id].is_set()


class TestRaisePlateDecision:
    """``_raise_plate_for_finish_photo``: the one place both finish-photo paths
    decide whether to move the plate and where to park it afterwards."""

    @pytest.fixture
    def decision_env(self, monkeypatch):
        env = {"heights": HEIGHTS, "blocked": False, "restore": [], "restored": True}

        async def _heights(*_args):
            return env["heights"]

        async def _blocked(_printer_id):
            return env["blocked"]

        async def _restore(printer_id, print_top, _logger):
            env["restore"].append((printer_id, print_top))
            return env["restored"]

        monkeypatch.setattr(main_module, "_plate_heights_for_current_print", _heights)
        monkeypatch.setattr(main_module, "_plate_restore_is_blocked_by_queue", _blocked)
        monkeypatch.setattr(main_module, "_restore_plate_for_finish_photo", _restore)
        return env

    @staticmethod
    async def _decide():
        return await main_module._raise_plate_for_finish_photo(7, {}, logging.getLogger(__name__), 5, 1)

    async def test_raises_and_parks_at_the_end_gcode_position(self, decision_env):
        assert await self._decide() == 114.0
        assert decision_env["restore"] == [(7, 16.0)]

    async def test_no_move_when_the_plate_is_already_in_view(self, decision_env):
        decision_env["heights"] = main_module.PlateHeights(237.8, 239.8)
        assert await self._decide() is None
        assert decision_env["restore"] == []

    async def test_no_move_when_heights_are_unknown(self, decision_env):
        decision_env["heights"] = None
        assert await self._decide() is None
        assert decision_env["restore"] == []

    async def test_no_move_when_the_queue_has_work(self, decision_env):
        decision_env["blocked"] = True
        assert await self._decide() is None
        assert decision_env["restore"] == []

    async def test_nothing_to_park_when_the_restore_did_not_happen(self, decision_env):
        decision_env["restored"] = False
        assert await self._decide() is None


def test_every_plate_raise_goes_through_the_decision():
    """Both finish-photo paths raise the plate. The park target is safe only
    because it comes from ``_raise_plate_for_finish_photo``, so no other code
    may call the raise directly, and nothing may compute a park height."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(main_module))
    callers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_restore_plate_for_finish_photo"
            ):
                callers.add(fn.name)
    assert callers == {"_raise_plate_for_finish_photo"}
    assert not hasattr(main_module, "_PLATE_PARK_DROP_MM")


async def test_producer_wait_budget_covers_the_restore():
    """The consumer's wait has to outlast settle + a worst-case RTSP grab, and
    still finish inside the notification's own photo budget — otherwise the
    restore path is cut off by a timeout somewhere above it."""
    assert main_module._FINISH_PHOTO_PRODUCER_WAIT_SECONDS > main_module._PLATE_RESTORE_SETTLE_SECONDS + 15


def test_producer_wait_budget_covers_the_chamber_light_delay():
    """#1655: the producer's live grab can first wait for the chamber light.
    Settle + the longest light delay + a worst-case grab must still fit, or the
    consumer gives up and its fallback grab races the producer's (#1790)."""
    from backend.app.services.camera_light import MAX_DELAY_SECONDS

    assert (
        main_module._FINISH_PHOTO_PRODUCER_WAIT_SECONDS
        > main_module._PLATE_RESTORE_SETTLE_SECONDS + MAX_DELAY_SECONDS + 15
    )


class TestMaxZResolution:
    """#2547 safety: the height that becomes a Z-move target must provably
    belong to the print that just finished.

    A height from another print is the one failure mode that could drive the
    nozzle into the model — 20mm carried onto a 200mm print commands the plate
    up through the part. So the resolver refuses on every ambiguity rather than
    falling back to "whatever ran last on this printer".

    Run against a real database (#3240): the earlier tests faked the session,
    so neither the query nor the path the archive's file is read from was ever
    exercised, and both were wrong.
    """

    @pytest.fixture
    async def resolver_env(self, monkeypatch, tmp_path):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        env = {"reported_layers": 30, "height": 16.0, "end_z": 114.0, "read": [], "session_maker": session_maker}

        monkeypatch.setattr(main_module, "async_session", session_maker)
        monkeypatch.setattr(main_module.app_settings, "base_dir", tmp_path)
        monkeypatch.setattr(
            main_module.printer_manager,
            "get_client",
            lambda _pid: SimpleNamespace(state=SimpleNamespace(total_layers=env["reported_layers"])),
        )

        def _extract(path, plate):
            env["read"].append((path, plate))
            return env["height"]

        def _extract_end(path, plate):
            env["read_end"] = (path, plate)
            return env["end_z"]

        monkeypatch.setattr("backend.app.utils.threemf_tools.extract_max_z_height_from_3mf", _extract)
        monkeypatch.setattr("backend.app.utils.threemf_tools.extract_end_z_from_3mf", _extract_end)
        try:
            yield env
        finally:
            await engine.dispose()

    @staticmethod
    async def _archive(env, **overrides) -> int:
        values = {
            "printer_id": 1,
            "filename": "job.gcode.3mf",
            "file_path": "archive/1/job/job.gcode.3mf",
            "file_size": 1,
            "print_name": "job",
            "status": "completed",
            "plate_id": 1,
            "total_layers": 30,
        }
        values.update(overrides)
        async with env["session_maker"]() as db:
            archive = PrintArchive(**values)
            db.add(archive)
            await db.commit()
            return archive.id

    @staticmethod
    async def _resolve(name, archive_id, plate_id=None):
        data = {"subtask_name": name} if name is not None else {}
        return await main_module._plate_heights_for_current_print(
            1, data, logging.getLogger(__name__), archive_id, plate_id
        )

    async def test_returns_the_height_when_binding_name_and_layers_agree(self, resolver_env):
        archive_id = await self._archive(resolver_env)
        assert await self._resolve("job", archive_id) == HEIGHTS

    async def test_refuses_a_print_that_is_not_bound_to_an_archive(self, resolver_env):
        """A name alone is never enough: with no binding there is no move, even
        when an archive of exactly that name exists."""
        await self._archive(resolver_env)
        assert await self._resolve("job", None) is None
        assert resolver_env["read"] == []

    async def test_uses_the_bound_archive_not_a_newer_one_of_the_same_name(self, resolver_env):
        """The support-bundle case: a reprint reuses archive 169, while a newer
        archive of the same file (another plate) is 288. The name lookup took
        288; only the layer check stopped it."""
        bound = await self._archive(resolver_env, file_path="archive/reprinted.gcode.3mf", total_layers=125)
        await self._archive(resolver_env, file_path="archive/other-plate.gcode.3mf", total_layers=313)
        resolver_env["reported_layers"] = 125

        assert await self._resolve("job", bound) == HEIGHTS
        assert resolver_env["read"][0][0].name == "reprinted.gcode.3mf"

    async def test_reads_the_plate_the_job_was_dispatched_with(self, resolver_env):
        """A reprint of one plate reuses the archive row without updating its
        plate_id, so the dispatched plate wins."""
        archive_id = await self._archive(resolver_env, plate_id=1)

        await self._resolve("job", archive_id, plate_id=12)
        assert resolver_env["read"][0][1] == 12

        await self._resolve("job", archive_id)
        assert resolver_env["read"][1][1] == 1

    async def test_reads_a_relative_file_path_from_the_data_directory(self, resolver_env, tmp_path):
        """#3240: archive paths are stored relative to the data directory. The
        resolver joined them to a `data_dir` setting that does not exist, so the
        lookup raised, was logged at debug level, and the plate never moved."""
        archive_id = await self._archive(resolver_env, file_path="archive/1/job/job.gcode.3mf", plate_id=2)

        assert await self._resolve("job", archive_id) == HEIGHTS
        assert resolver_env["read"] == [(tmp_path / "archive/1/job/job.gcode.3mf", 2)]

    async def test_an_absolute_file_path_is_read_as_stored(self, resolver_env):
        archive_id = await self._archive(resolver_env, file_path="/srv/bambuddy/archive/job.gcode.3mf")

        assert await self._resolve("job", archive_id) == HEIGHTS
        assert str(resolver_env["read"][0][0]) == "/srv/bambuddy/archive/job.gcode.3mf"

    async def test_matches_a_name_the_printer_echoed_with_underscores(self, resolver_env):
        """#3240: the printer reports "Cube v2" as "Cube_v2"."""
        archive_id = await self._archive(resolver_env, print_name="Cube v2", filename="Cube v2.gcode.3mf")
        assert await self._resolve("Cube_v2", archive_id) == HEIGHTS

    async def test_matches_on_the_filename_with_or_without_its_extension(self, resolver_env):
        archive_id = await self._archive(resolver_env, print_name=None, filename="Bracket Left.gcode.3mf")
        assert await self._resolve("Bracket_Left", archive_id) == HEIGHTS
        assert await self._resolve("Bracket_Left.gcode", archive_id) == HEIGHTS
        assert await self._resolve("Bracket Left.gcode.3mf", archive_id) == HEIGHTS

    async def test_matches_a_file_name_that_ends_in_a_space(self, resolver_env):
        """#3241: "Part .gcode.3mf" is echoed as "Part_"."""
        archive_id = await self._archive(resolver_env, print_name="Part ", filename="Part .gcode.3mf")
        assert await self._resolve("Part_", archive_id) == HEIGHTS

    async def test_refuses_a_bound_archive_with_another_name(self, resolver_env):
        archive_id = await self._archive(resolver_env)
        assert await self._resolve("other", archive_id) is None

    async def test_matches_by_equality_not_substring(self, resolver_env):
        """ "Cube" must never resolve to "Cube v2" — a different print, quite
        possibly a much taller one."""
        archive_id = await self._archive(resolver_env, print_name="Cube v2", filename="Cube v2.gcode.3mf")
        assert await self._resolve("Cube", archive_id) is None

    async def test_a_truncated_echo_does_not_match(self, resolver_env):
        """The completion check accepts the printer's "..." truncation; a Z
        move must not, since the cut-off name could belong to another print."""
        archive_id = await self._archive(resolver_env, print_name="A very long print name indeed")
        assert await self._resolve("A_very_long_print...", archive_id) is None

    async def test_refuses_another_printers_or_a_failed_or_deleted_archive(self, resolver_env):
        for overrides in ({"printer_id": 2}, {"status": "failed"}, {"deleted_at": datetime.now(timezone.utc)}):
            archive_id = await self._archive(resolver_env, **overrides)
            assert await self._resolve("job", archive_id) is None, overrides

    async def test_refuses_when_the_print_has_no_name_to_match_on(self, resolver_env):
        archive_id = await self._archive(resolver_env)
        assert await self._resolve(None, archive_id) is None
        assert resolver_env["read"] == []

    async def test_refuses_when_the_layer_counts_disagree(self, resolver_env):
        """The corroboration check. The archive's layer count comes from the
        3MF; the printer's comes from MQTT. If two independent sources disagree,
        the row is not this print whatever its name says."""
        archive_id = await self._archive(resolver_env)
        resolver_env["reported_layers"] = 240
        assert await self._resolve("job", archive_id) is None

    async def test_proceeds_when_a_layer_count_is_simply_unknown(self, resolver_env):
        """Absent is not the same as contradictory — a print Bambuddy has no
        layer count for still gets its height, because binding and name agree."""
        archive_id = await self._archive(resolver_env)
        resolver_env["reported_layers"] = 0
        assert await self._resolve("job", archive_id) == HEIGHTS

        resolver_env["reported_layers"] = 30
        unknown_layers = await self._archive(resolver_env, total_layers=None)
        assert await self._resolve("job", unknown_layers) == HEIGHTS

    async def test_refuses_when_the_archive_has_no_file(self, resolver_env):
        archive_id = await self._archive(resolver_env, file_path="")
        assert await self._resolve("job", archive_id) is None

    async def test_refuses_when_the_3mf_has_no_height(self, resolver_env):
        archive_id = await self._archive(resolver_env)
        resolver_env["height"] = None
        assert await self._resolve("job", archive_id) is None

    async def test_refuses_when_the_end_gcode_position_is_unknown(self, resolver_env):
        """No end Z, no park target the printer's own G-code chose, so no move
        at all rather than one to a height worked out here."""
        archive_id = await self._archive(resolver_env)
        resolver_env["end_z"] = None
        assert await self._resolve("job", archive_id) is None

    async def test_reads_the_end_position_from_the_same_file_and_plate(self, resolver_env, tmp_path):
        archive_id = await self._archive(resolver_env, plate_id=2)

        await self._resolve("job", archive_id)

        assert resolver_env["read_end"] == (tmp_path / "archive/1/job/job.gcode.3mf", 2)


class TestBoundPrint:
    """The binding the plate restore reads (#3240)."""

    @pytest.fixture(autouse=True)
    def _clean(self):
        main_module._active_prints.clear()
        main_module._print_plate_ids.clear()
        yield
        main_module._active_prints.clear()
        main_module._print_plate_ids.clear()

    def test_finds_the_archive_under_the_keys_on_print_complete_tries(self):
        main_module._active_prints[(7, "Cube v2.gcode.3mf")] = 169
        main_module._active_prints[(7, "Cube_v2.3mf")] = 169
        main_module._print_plate_ids[(7, 169)] = 12

        bound = main_module._bound_print(7, {"filename": "Cube_v2.3mf", "subtask_name": "Cube_v2"})

        assert bound == (169, 12)

    def test_reads_without_consuming(self):
        """on_print_complete still has to find the binding after we looked."""
        main_module._active_prints[(7, "job.3mf")] = 5
        main_module._print_plate_ids[(7, 5)] = 2

        main_module._bound_print(7, {"filename": "job.3mf", "subtask_name": "job"})

        assert main_module._active_prints == {(7, "job.3mf"): 5}
        assert main_module._print_plate_ids == {(7, 5): 2}

    def test_unbound_print_and_other_printers(self):
        main_module._active_prints[(8, "job.3mf")] = 5

        assert main_module._bound_print(7, {"filename": "job.3mf", "subtask_name": "job"}) == (None, None)
        assert main_module._bound_print(7, {}) == (None, None)

    def test_no_plate_recorded(self):
        main_module._active_prints[(7, "job.3mf")] = 5
        assert main_module._bound_print(7, {"filename": "job.3mf", "subtask_name": "job"}) == (5, None)

    def test_same_archive_as_on_print_complete_takes(self):
        """Keys tried in the same order, so the first one present wins in both."""
        main_module._active_prints[(7, "job.gcode.3mf")] = 1
        main_module._active_prints[(7, "job.3mf")] = 2
        keys = main_module._completion_print_keys(7, "job.3mf", "job")
        first = next(main_module._active_prints[k] for k in keys if k in main_module._active_prints)

        assert main_module._bound_print(7, {"filename": "job.3mf", "subtask_name": "job"})[0] == first == 2


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

        async def _height(_printer_id, _data, _logger, _archive_id, _plate_id=None):
            return main_module.PlateHeights(16.0, 114.0)

        monkeypatch.setattr(main_module, "_restore_plate_for_finish_photo", _restore)
        monkeypatch.setattr(main_module, "_plate_heights_for_current_print", _height)

        await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state", "timelapse_was_active": True})

        assert moved == []


# --- #1655 chamber light for the camera ------------------------------------


class _LightClient:
    def __init__(self):
        self.state = SimpleNamespace(chamber_light=False)
        self.calls: list[bool] = []
        self.mode = "off"  # the camera_light_mode setting

    def set_chamber_light(self, on):
        self.calls.append(on)
        self.state.chamber_light = on
        return True


@pytest.fixture
def light_client(monkeypatch):
    from backend.app.services import camera_light as light_svc

    client = _LightClient()
    monkeypatch.setattr(light_svc, "_get_client", lambda _pid: client)

    async def _settings():
        return client.mode, 0.0

    monkeypatch.setattr(light_svc, "_read_settings", _settings)
    monkeypatch.setattr(light_svc, "_settings_cache", None)
    monkeypatch.setattr(light_svc, "_holders", {})
    monkeypatch.setattr(light_svc, "_lit_at", {})
    off_tasks: dict = {}
    monkeypatch.setattr(light_svc, "_off_tasks", off_tasks)
    yield client
    for task in off_tasks.values():
        task.cancel()


async def test_finish_photo_live_grab_is_lit_when_the_light_mode_covers_it(patched_env, monkeypatch, light_client):
    light_client.mode = "all"
    seen = {}

    async def _live(**_kwargs):
        seen["light"] = light_client.state.chamber_light
        return b"\xff\xd8live"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert seen["light"] is True
    assert light_client.calls == [True]  # off follows after the grace


async def test_finish_photo_live_grab_leaves_the_light_alone_by_default(patched_env, monkeypatch, light_client):
    async def _live(**_kwargs):
        return b"\xff\xd8live"

    monkeypatch.setattr("backend.app.services.camera.capture_camera_frame_bytes", _live)

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert light_client.calls == []


async def test_finish_photo_from_the_bank_does_not_turn_the_light_on(patched_env, monkeypatch, light_client):
    """The banked frame is already taken; lighting the printer for it would
    only switch the light on and off for nothing."""
    light_client.mode = "all"
    print_dispatch_context.mark_pending(patched_env.id)
    print_dispatch_context.adopt(patched_env.id)
    main_module._inprint_frame_bank[patched_env.id] = b"\xff\xd8banked"

    await on_finish_photo_moment(patched_env.id, {"trigger": "finish_state"})

    assert main_module._stage22_finish_frames[patched_env.id] == b"\xff\xd8banked"
    assert light_client.calls == []


async def test_notification_snapshot_is_lit_for_a_selected_printer(fake_printer, monkeypatch, light_client):
    light_client.mode = "selected"
    fake_printer.camera_light_auto = True
    seen = {}

    async def _frame(_pid, _printer, _logger):
        seen["light"] = light_client.state.chamber_light
        return b"\xff\xd8snap"

    monkeypatch.setattr(main_module, "_capture_snapshot_frame", _frame)

    assert await main_module._capture_snapshot_for_notification(7, fake_printer, logging.getLogger()) == b"\xff\xd8snap"
    assert seen["light"] is True
    assert light_client.calls == [True]


def test_every_live_capture_in_main_holds_the_light():
    """#1655: the finish photo's last fallback lives in `_background_finish_photo`,
    a closure nothing can drive directly (see the rotation guard above). So the
    source is checked: every live camera grab in main.py runs inside
    `camera_light(...)`, except in `_capture_snapshot_frame`, whose only caller
    is the wrapper that holds the light for it.
    """
    import ast
    from pathlib import Path

    main_py = Path(__file__).resolve().parents[2] / "app" / "main.py"
    tree = ast.parse(main_py.read_text())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    grabs = {"capture_frame", "capture_camera_frame", "capture_camera_frame_bytes", "capture_finish_photo"}

    def _held(node) -> bool:
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_capture_snapshot_frame":
                return True
            if isinstance(node, ast.AsyncWith) and any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None) == "camera_light"
                for item in node.items
            ):
                return True
        return False

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) in grabs
    ]
    assert len(calls) >= 6, "guard found fewer grabs than main.py has; is it parsing the right file?"
    unlit = [node.lineno for node in calls if not _held(node)]
    assert not unlit, f"main.py:{unlit} grabs a camera frame without camera_light(...)"

    wrapper_callers = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_capture_snapshot_frame"
    ]
    assert len(wrapper_callers) == 1, f"_capture_snapshot_frame is called from main.py:{wrapper_callers}"
