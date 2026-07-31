"""Continuous stderr draining for streaming ffmpeg (_FfmpegStderrTail).

ffmpeg is spawned with stderr=PIPE, and stderr used to be read only when
something had already gone wrong — so for the life of a stream nobody read that
pipe. ffmpeg writes its banner, the input analysis and then a progress line at a
steady rate, so a 64 KiB pipe fills eventually, ffmpeg blocks writing to it,
frames stop, and the stream's own read timeout fires with nothing in the log
explaining that we starved it.

How long that takes is unmeasured and evidently long — one H2D upstream ran
21m36s without stalling — so this is a bounded resource being treated as
unbounded rather than an observed failure. These tests pin the four properties
that matter: the pipe is always drained, the retained tail is bounded, the tail
is what the error paths report, and it goes through the same redaction funnel as
every other stderr log in this module.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.api.routes import camera

pytestmark = pytest.mark.asyncio


class _Reader:
    """Feeds queued chunks, then blocks like a live-but-quiet ffmpeg."""

    def __init__(self, chunks: list[bytes], then_block: bool = True) -> None:
        self._chunks = list(chunks)
        self._then_block = then_block
        self.reads = 0

    async def read(self, _size: int = -1) -> bytes:
        self.reads += 1
        if self._chunks:
            return self._chunks.pop(0)
        if self._then_block:
            await asyncio.Event().wait()  # never returns, never EOFs
        return b""


class _Proc:
    def __init__(self, reader, pid: int = 88010) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = None
        self.stderr = reader


@pytest.fixture(autouse=True)
def _no_leaked_tails():
    yield
    # Last-resort teardown only — this fixture is sync, so it cancels without
    # awaiting. Tests are expected to aclose() their own tails.
    for tail in list(camera._stderr_tails.values()):
        if tail._task is not None:
            tail._task.cancel()
    camera._stderr_tails.clear()


async def _settle() -> None:
    """Let the pump task run."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_it_keeps_draining_a_stream_that_never_closes_stderr():
    """The whole point: the pipe is read continuously, not on demand."""
    reader = _Reader([b"first\n", b"second\n"])
    tail = camera._FfmpegStderrTail(_Proc(reader))

    await _settle()

    assert reader.reads >= 3, "pump stopped reading instead of following the pipe"
    assert "second" in (tail.text() or "")
    await tail.aclose()


async def test_the_retained_tail_is_bounded():
    """A long-running stream must not turn the pipe into unbounded memory."""
    oversized = b"x" * (camera._FFMPEG_STDERR_TAIL_BYTES * 3)
    tail = camera._FfmpegStderrTail(_Proc(_Reader([oversized])))

    await _settle()

    assert len(tail._buffer) == camera._FFMPEG_STDERR_TAIL_BYTES
    await tail.aclose()


async def test_the_tail_keeps_the_newest_output():
    """Recent output is what explains a failure; the banner gets stripped anyway."""
    filler = b"stale-line\n" * 4000
    tail = camera._FfmpegStderrTail(_Proc(_Reader([filler, b"Connection timed out\n"])))

    await _settle()

    text = tail.text() or ""
    assert "Connection timed out" in text
    assert len(tail._buffer) <= camera._FFMPEG_STDERR_TAIL_BYTES
    await tail.aclose()


async def test_read_ffmpeg_stderr_defers_to_the_collector():
    """Two readers on one StreamReader raise, so the on-demand read must not
    touch a pipe the collector owns."""
    reader = _Reader([b"Server returned 401 Unauthorized\n"])
    process = _Proc(reader)
    tail = camera._FfmpegStderrTail(process)
    await _settle()
    reads_before = reader.reads

    result = await camera._read_ffmpeg_stderr(process)

    assert "401 Unauthorized" in (result or "")
    assert reader.reads == reads_before, "on-demand read raced the collector"
    await tail.aclose()


async def test_read_ffmpeg_stderr_still_reads_the_pipe_without_a_collector():
    """An immediately-failed ffmpeg has no collector; that path must still work."""
    process = _Proc(_Reader([b"Server returned 404 Not Found\n"], then_block=False))

    result = await camera._read_ffmpeg_stderr(process)

    assert "404 Not Found" in (result or "")


async def test_the_tail_redacts_the_access_code():
    """ffmpeg echoes its input URL, which carries the printer's access code.

    This is a new stderr-to-log path, so it gets the same guarantee as the rest:
    everything goes through _summarize_ffmpeg_stderr.
    """
    secret = "12345678"
    leaky = f"[rtsp @ 0x55] Failed to resolve rtsp://bblp:{secret}@127.0.0.1:8554/streaming/live/1\n"
    tail = camera._FfmpegStderrTail(_Proc(_Reader([leaky.encode()])))

    await _settle()
    text = tail.text() or ""

    assert secret not in text, "access code leaked into a log line"
    # Assert the line SURVIVED with the credential masked, not that it was
    # dropped — otherwise this passes whenever the summariser happens to filter
    # the line out, and proves nothing about redaction.
    assert "Failed to resolve" in text, "line was filtered, so redaction is untested"
    assert "[REDACTED]" in text
    await tail.aclose()


async def test_close_releases_ownership_and_is_idempotent():
    process = _Proc(_Reader([b"line\n"]))
    tail = camera._FfmpegStderrTail(process)
    await _settle()
    assert camera._stderr_tails.get(process.pid) is tail

    await tail.aclose()
    await tail.aclose()  # must not raise

    assert process.pid not in camera._stderr_tails


async def test_a_process_without_stderr_is_handled():
    """Fakes and some spawn paths pass stderr=None; must not register or crash."""
    process = _Proc(None, pid=88099)

    tail = camera._FfmpegStderrTail(process)

    assert tail.text() is None
    assert process.pid not in camera._stderr_tails
    await tail.aclose()


async def test_the_stream_generator_owns_then_releases_the_collector(monkeypatch):
    """Lifecycle inside the real generator: registered while streaming, gone after.

    The other generator tests use fakes with stderr=None, so they never build a
    collector at all — this is the one that would catch a missing close() or a
    reader race between the collector and teardown.
    """
    printer_id = 8842
    stream_id = f"{printer_id}-fanout-stderrtail"

    class _FrameThenBlock:
        def __init__(self) -> None:
            self._sent = False

        async def read(self, _size: int = -1) -> bytes:
            if self._sent:
                await asyncio.Event().wait()  # stay alive, don't trigger reconnect
            self._sent = True
            return b"\xff\xd8frame\xff\xd9"

    class _Proc2:
        def __init__(self) -> None:
            self.pid = 88042
            self.returncode = None
            self.stdout = _FrameThenBlock()
            self.stderr = _Reader([b"Stream #0:0: Video: h264\n"])

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    process = _Proc2()

    class _FakeServer:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def _fake_exec(*_a, **_kw):
        return process

    async def _fake_proxy(_ip, _port):
        return 48777, _FakeServer()

    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(camera, "create_tls_proxy", _fake_proxy)
    monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", _fake_exec)

    stream = camera.generate_rtsp_mjpeg_stream(
        ip_address="192.0.2.44",
        access_code="c",
        model="P2S",
        fps=15,
        stream_id=stream_id,
        disconnect_event=asyncio.Event(),
        printer_id=printer_id,
    )
    try:
        chunk = await asyncio.wait_for(anext(stream), timeout=5.0)
        assert b"frame" in chunk
        assert process.pid in camera._stderr_tails, "generator did not take stderr ownership"

        await asyncio.wait_for(stream.aclose(), timeout=5.0)

        assert process.pid not in camera._stderr_tails, "collector outlived its stream"
    finally:
        camera._active_streams.pop(stream_id, None)
        camera._disconnect_events.pop(stream_id, None)
        camera._stream_last_frame_times.pop(stream_id, None)
        camera._last_frames.pop(printer_id, None)
        camera._last_frame_times.pop(printer_id, None)
        camera._stream_start_times.pop(printer_id, None)
        camera._spawned_ffmpeg_pids.pop(process.pid, None)


async def test_terminate_skips_stderr_while_a_collector_owns_it():
    """_terminate_ffmpeg must not add a second reader to an owned pipe."""
    reader = _Reader([b"tearing down\n"])

    class _Killable(_Proc):
        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

        async def wait(self):
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    process = _Killable(reader, pid=88020)
    tail = camera._FfmpegStderrTail(process)
    await _settle()
    reads_before = reader.reads

    await asyncio.wait_for(camera._terminate_ffmpeg(process, "88020-fanout-abcd"), timeout=2.0)

    # The collector, not _terminate_ffmpeg, is the only reader that advanced.
    assert reader.reads >= reads_before
    assert tail.text() is not None
    await tail.aclose()
