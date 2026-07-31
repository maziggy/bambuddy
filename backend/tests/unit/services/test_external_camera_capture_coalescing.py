"""Single-flight coalescing of one-shot external-camera captures (#2705-shape
fix, filed against the external-camera path as a follow-up on #2707).

V4L2 USB devices allow exactly one open handle - the same one-connection
limit #2705 covers for Bambu firmware. The #2707 guards (``is_stream_active``
/ ``try_get_active_buffered_frame``) only keep a one-shot capturer from
competing with the fan-out live view; nothing kept the capturers from
competing with EACH OTHER when no viewer is attached, so an Obico poll and
the in-print frame bank (say) could each open their own connection to the
same USB device and collide.

These tests drive ``capture_frame`` at the public boundary and count how
many times the underlying capture ran, since "how many connections did we
open" is the entire point of the fix. Mirrors
``test_camera_capture_coalescing.py``'s structure for the built-in path.
"""

import asyncio

import pytest

from backend.app.services import external_camera as ec_module
from backend.app.services.external_camera import capture_frame, capture_in_flight

FRAME_A = b"\xff\xd8" + b"a" * 200 + b"\xff\xd9"
FRAME_B = b"\xff\xd8" + b"b" * 200 + b"\xff\xd9"


@pytest.fixture(autouse=True)
def _clear_inflight():
    """The registry is module-global; don't leak tasks between tests."""
    ec_module._inflight_captures.clear()
    yield
    ec_module._inflight_captures.clear()


class RecordingCapture:
    """Stand-in for the real capture, recording each call.

    ``gate`` (when set) holds every capture open until released, which is how
    these tests create the overlap window that used to produce two
    connections.
    """

    def __init__(self, frames=(FRAME_A, FRAME_B), gate: asyncio.Event | None = None):
        self.calls: list[tuple[str, str, str | None, int]] = []
        self._frames = list(frames)
        self._gate = gate
        self.started = asyncio.Event()

    async def __call__(self, url, camera_type, timeout, snapshot_url):
        self.calls.append((url, camera_type, snapshot_url, timeout))
        self.started.set()
        if self._gate is not None:
            await self._gate.wait()
        return self._frames.pop(0) if self._frames else None

    @property
    def count(self) -> int:
        return len(self.calls)


@pytest.fixture
def patch_capture(monkeypatch):
    def _install(capture):
        monkeypatch.setattr(ec_module, "_capture_frame_uncoalesced", capture)
        return capture

    return _install


async def _let_leader_start(capture: RecordingCapture) -> None:
    """Wait until the leader is inside the capture, so the next caller joins it.

    Without this the second caller can reach the registry before the first
    has even been scheduled, which tests a different (and uninteresting) race.
    """
    await asyncio.wait_for(capture.started.wait(), timeout=1)


@pytest.mark.asyncio
async def test_simultaneous_callers_share_one_capture(patch_capture):
    """The reported collision: two consumers, one connection, two frames."""
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    leader = asyncio.create_task(capture_frame("/dev/video1", "usb", timeout=20))
    await _let_leader_start(capture)
    follower = asyncio.create_task(capture_frame("/dev/video1", "usb", timeout=15))
    await asyncio.sleep(0)
    gate.set()

    assert await leader == FRAME_A
    assert await follower == FRAME_A
    assert capture.count == 1


@pytest.mark.asyncio
async def test_five_callers_one_capture(patch_capture):
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    first = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await _let_leader_start(capture)
    rest = [asyncio.create_task(capture_frame("/dev/video1", "usb")) for _ in range(4)]
    await asyncio.sleep(0)
    gate.set()

    assert await asyncio.gather(first, *rest) == [FRAME_A] * 5
    assert capture.count == 1


@pytest.mark.asyncio
async def test_different_cameras_do_not_coalesce(patch_capture):
    """The one-connection limit is per camera, so the key must be too."""
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    one = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await _let_leader_start(capture)
    two = asyncio.create_task(capture_frame("/dev/video2", "usb"))
    await asyncio.sleep(0)
    gate.set()

    assert {await one, await two} == {FRAME_A, FRAME_B}
    assert capture.count == 2
    assert {url for url, *_ in capture.calls} == {"/dev/video1", "/dev/video2"}


@pytest.mark.asyncio
async def test_different_snapshot_url_does_not_coalesce(patch_capture):
    """#1177's snapshot_url override routes to a different endpoint entirely -
    two printers sharing a camera_url but differing only in snapshot_url must
    not share a capture."""
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    one = asyncio.create_task(capture_frame("http://cam/", "mjpeg", snapshot_url="http://cam/frame1.jpg"))
    await _let_leader_start(capture)
    two = asyncio.create_task(capture_frame("http://cam/", "mjpeg", snapshot_url="http://cam/frame2.jpg"))
    await asyncio.sleep(0)
    gate.set()

    assert {await one, await two} == {FRAME_A, FRAME_B}
    assert capture.count == 2


@pytest.mark.asyncio
async def test_coalescing_is_not_caching(patch_capture):
    """Sequential callers each capture fresh.

    Deliberate: plate detection and the finish-photo path decide things about
    a running print from these frames, and #1397 was a finish photo a few
    seconds stale showing the bed already lowered.
    """
    capture = patch_capture(RecordingCapture())

    assert await capture_frame("/dev/video1", "usb") == FRAME_A
    assert await capture_frame("/dev/video1", "usb") == FRAME_B
    assert capture.count == 2


@pytest.mark.asyncio
async def test_registry_is_empty_after_a_capture_finishes(patch_capture):
    """No leak, and nothing left behind for the next caller to join."""
    patch_capture(RecordingCapture())

    await capture_frame("/dev/video1", "usb")
    await asyncio.sleep(0)  # let the done-callback run

    assert ec_module._inflight_captures == {}
    assert capture_in_flight("/dev/video1", "usb") is False


@pytest.mark.asyncio
async def test_failed_leader_does_not_poison_its_followers(patch_capture):
    """A follower that never got its own attempt gets one when the leader fails.

    Safe by then: the leader has finished, so there is no connection to
    compete with. This also covers the follower whose timeout is LONGER than
    the leader's — it isn't cut short by someone else's deadline.
    """
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(frames=(None, FRAME_B), gate=gate))

    leader = asyncio.create_task(capture_frame("/dev/video1", "usb", timeout=10))
    await _let_leader_start(capture)
    follower = asyncio.create_task(capture_frame("/dev/video1", "usb", timeout=20))
    await asyncio.sleep(0)
    gate.set()

    assert await leader is None
    assert await follower == FRAME_B
    assert capture.count == 2


@pytest.mark.asyncio
async def test_two_consecutive_failures_give_up(patch_capture):
    """Bounded retry: a follower doesn't chase failing captures forever.

    Two followers behind a failing leader. The first takes its own turn, the
    second joins THAT capture, and when it fails too the second gives up
    rather than opening a third connection.
    """
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(frames=(None, None), gate=gate))

    leader = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await _let_leader_start(capture)
    first = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await asyncio.sleep(0)
    second = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await asyncio.sleep(0)
    gate.set()

    assert await leader is None
    assert await first is None
    assert await second is None
    # The leader's capture plus one retry — not one per disappointed caller.
    assert capture.count == 2


@pytest.mark.asyncio
async def test_follower_timeout_does_not_sabotage_the_capture(patch_capture):
    """A follower giving up leaves the capture running for everyone else.

    Call sites disagree about the timeout, so a follower must be able to
    abandon a join without cancelling a capture other callers are still
    waiting on.
    """
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    leader = asyncio.create_task(capture_frame("/dev/video1", "usb", timeout=30))
    await _let_leader_start(capture)
    impatient = asyncio.create_task(capture_frame("/dev/video1", "usb", timeout=0.01))
    patient = asyncio.create_task(capture_frame("/dev/video1", "usb", timeout=30))

    assert await impatient is None  # gave up on its own deadline
    gate.set()

    assert await leader == FRAME_A
    assert await patient == FRAME_A  # unaffected by the one that walked away
    assert capture.count == 1


@pytest.mark.asyncio
async def test_cancelled_leader_still_delivers_to_followers(patch_capture):
    """Snapshot/capture requests get cancelled routinely (client navigates
    away mid-request). The follower must not lose the frame because the
    caller that happened to open the connection went away."""
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    leader = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await _let_leader_start(capture)
    follower = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await asyncio.sleep(0)

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    gate.set()

    assert await follower == FRAME_A
    assert capture.count == 1


@pytest.mark.asyncio
async def test_cancelling_a_follower_leaves_the_leader_alone(patch_capture):
    """The mirror case: the follower's cancellation is its own business."""
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    leader = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await _let_leader_start(capture)
    follower = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await asyncio.sleep(0)

    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower
    gate.set()

    assert await leader == FRAME_A
    assert capture.count == 1


@pytest.mark.asyncio
async def test_capture_in_flight_reports_the_window(patch_capture):
    """The predicate a diagnose-style caller would use to know it will join,
    not measure its own connection."""
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(gate=gate))

    assert capture_in_flight("/dev/video1", "usb") is False

    leader = asyncio.create_task(capture_frame("/dev/video1", "usb"))
    await _let_leader_start(capture)

    assert capture_in_flight("/dev/video1", "usb") is True
    assert capture_in_flight("/dev/video2", "usb") is False  # per camera

    gate.set()
    await leader
    await asyncio.sleep(0)

    assert capture_in_flight("/dev/video1", "usb") is False


# ---------------------------------------------------------------------------
# Failure must arrive as None, never as an exception
# ---------------------------------------------------------------------------
#
# `test_failed_leader_does_not_poison_its_followers` above covers a leader that
# RETURNS None. A leader that RAISES is a different path: the wrapper's retry
# loop only catches TimeoutError and CancelledError, so an escaping exception
# would reach every follower at once and none of them would take a turn of
# their own — one caller's failure becoming N. The per-type helpers catch
# narrowly (aiohttp.ClientError / OSError / timeouts), so the guarantee lives
# in _capture_frame_uncoalesced's own blanket catch.


@pytest.mark.asyncio
async def test_an_unexpected_error_is_reported_as_a_failed_capture():
    """Not every failure is an OSError. An IncompleteReadError is an EOFError,
    which none of the per-type helpers catch."""

    async def raising(url, timeout):
        raise asyncio.IncompleteReadError(partial=b"", expected=4)

    import backend.app.services.external_camera as ec

    original = ec._capture_snapshot
    ec._capture_snapshot = raising
    try:
        result = await ec._capture_frame_uncoalesced("http://cam/snap", "snapshot", 5, None)
    finally:
        ec._capture_snapshot = original
    assert result is None


@pytest.mark.asyncio
async def test_a_raising_leader_does_not_take_its_followers_down_with_it(monkeypatch):
    """The whole point of coalescing is that one caller's connection serves
    several. It must not also mean one caller's crash fails several.

    Patches the per-type helper rather than ``_capture_frame_uncoalesced``,
    deliberately: the guarantee lives in that function's blanket catch, so a
    stand-in installed in its place would test the wrapper against a shape the
    wrapper can no longer be handed.
    """
    gate = asyncio.Event()
    attempts: list[str] = []

    async def raise_then_succeed(url, timeout):
        attempts.append(url)
        if len(attempts) == 1:
            await gate.wait()
            raise RuntimeError("ffmpeg died in a way nobody catches")
        return FRAME_B

    monkeypatch.setattr(ec_module, "_capture_rtsp_frame", raise_then_succeed)

    leader = asyncio.create_task(capture_frame("rtsp://cam/1", "rtsp", timeout=5))
    await asyncio.sleep(0)
    follower = asyncio.create_task(capture_frame("rtsp://cam/1", "rtsp", timeout=5))
    await asyncio.sleep(0)
    gate.set()

    leader_result, follower_result = await asyncio.gather(leader, follower, return_exceptions=True)

    assert not isinstance(leader_result, BaseException), f"leader raised {leader_result!r}"
    assert not isinstance(follower_result, BaseException), f"follower raised {follower_result!r}"
    assert leader_result is None, "the leader's own capture failed, so it gets None"
    assert follower_result == FRAME_B, "the follower took its own turn and succeeded"


# ---------------------------------------------------------------------------
# The connection test must not claim a connection it never opened
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_reports_when_it_shared_someone_elses_capture(patch_capture):
    """A test landing while Obico is mid-poll gets that frame back. Reporting a
    bare success would credit a connection this test never made — and forcing
    its own would open the second handle the coalescing exists to prevent."""
    from backend.app.services.external_camera import test_connection

    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(frames=(FRAME_A,), gate=gate))

    other = asyncio.create_task(capture_frame("rtsp://cam/1", "rtsp", timeout=5))
    await _let_leader_start(capture)

    tested = asyncio.create_task(test_connection("rtsp://cam/1", "rtsp"))
    await asyncio.sleep(0)
    gate.set()

    result = await tested
    await other

    assert result["success"] is True
    assert result["coalesced"] is True
    assert capture.count == 1, "no second connection was opened"


@pytest.mark.asyncio
async def test_connection_reports_its_own_capture_as_not_coalesced(patch_capture):
    from backend.app.services.external_camera import test_connection

    capture = patch_capture(RecordingCapture(frames=(FRAME_A,)))
    result = await test_connection("rtsp://cam/1", "rtsp")

    assert result["success"] is True
    assert result["coalesced"] is False
    assert capture.count == 1


@pytest.mark.asyncio
async def test_connection_reports_coalesced_on_the_failure_path_too(patch_capture):
    """The flag describes where the answer came from, not whether it was good."""
    from backend.app.services.external_camera import test_connection

    capture = patch_capture(RecordingCapture(frames=(None,)))
    result = await test_connection("rtsp://cam/1", "rtsp")

    assert result["success"] is False
    assert result["coalesced"] is False
    assert capture.count == 1


# ---------------------------------------------------------------------------
# Credentials must not reach the log
# ---------------------------------------------------------------------------
#
# camera.py's coalescing is keyed by IP address and has nothing to redact.
# These keys carry the camera URL, and an RTSP camera URL routinely embeds
# user:pass@ — which is why every other URL log in the module redacts.

CREDENTIALED_URL = "rtsp://admin:hunter2@192.168.1.50:554/Streaming/Channels/101"


@pytest.mark.asyncio
async def test_the_reuse_log_line_redacts_the_password(patch_capture, caplog):
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(frames=(FRAME_A,), gate=gate))

    with caplog.at_level("DEBUG", logger=ec_module.__name__):
        leader = asyncio.create_task(capture_frame(CREDENTIALED_URL, "rtsp", timeout=5))
        await _let_leader_start(capture)
        follower = asyncio.create_task(capture_frame(CREDENTIALED_URL, "rtsp", timeout=5))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(leader, follower)

    assert not [r.getMessage() for r in caplog.records if "hunter2" in r.getMessage()]


@pytest.mark.asyncio
async def test_the_gave_up_waiting_log_line_redacts_the_password(patch_capture, caplog):
    """This one is a warning, so it shows at the default level and lands in
    support bundles."""
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(frames=(FRAME_A,), gate=gate))

    with caplog.at_level("DEBUG", logger=ec_module.__name__):
        leader = asyncio.create_task(capture_frame(CREDENTIALED_URL, "rtsp", timeout=5))
        await _let_leader_start(capture)
        assert await capture_frame(CREDENTIALED_URL, "rtsp", timeout=0) is None
        gate.set()
        await leader

    messages = [r.getMessage() for r in caplog.records]
    assert any("Gave up waiting" in m for m in messages), "the timeout path did not run"
    assert not [m for m in messages if "hunter2" in m]


@pytest.mark.asyncio
async def test_the_failed_capture_log_line_redacts_the_password(patch_capture, caplog):
    gate = asyncio.Event()
    capture = patch_capture(RecordingCapture(frames=(None, FRAME_B), gate=gate))

    with caplog.at_level("DEBUG", logger=ec_module.__name__):
        leader = asyncio.create_task(capture_frame(CREDENTIALED_URL, "rtsp", timeout=5))
        await _let_leader_start(capture)
        follower = asyncio.create_task(capture_frame(CREDENTIALED_URL, "rtsp", timeout=5))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(leader, follower)

    assert not [r.getMessage() for r in caplog.records if "hunter2" in r.getMessage()]
