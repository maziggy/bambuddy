"""Downloads get a deadline that fits the file, and take turns on a printer (#2957).

Two things about ``ftp_timeout``. It is passed as *both* the socket inactivity
timeout and the whole-transfer deadline, so its 30 s default is really a cap on
how big a file a printer is allowed to serve: the reporter measured the same
5.4 MB 3MF at 45 s off a worn P1S SD card and 25 s off a new one, and a 15.15 MB
3MF at 105 s. None of those transfers were unhealthy. And it bounded nothing
about concurrency -- he watched Bambu Studio lose its own connection to the
printer while two Bambuddy downloads for the same file ran against it at once.

So the total deadline now follows the size the printer reports, and a printer
serves one Bambuddy download at a time. Both are deliberately soft: the
extension is granted only once SIZE has been answered (so a dead printer still
fails on schedule, and the queue wait #2572 capped is untouched), and a download
that cannot have the gate goes anyway rather than letting a print lose its 3MF
to queueing.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import backend.app.services.bambu_ftp as ftp_mod
from backend.app.services.bambu_ftp import (
    _DOWNLOAD_FLOOR_BYTES_PER_SEC,
    _DOWNLOAD_MAX_TIMEOUT,
    _download_extension,
    _serialized_download,
    download_file_async,
    download_file_try_paths_async,
)


class _FakeClient:
    """Enough of ``BambuFTPClient`` for the async wrappers to drive it."""

    _mode_cache: dict[str, str] = {}
    A1_MODELS = ()

    def __init__(self, *a, **kw):
        pass

    @classmethod
    def cache_mode(cls, ip_address, mode):
        pass

    def connect(self):
        return True

    def disconnect(self):
        pass


class TestTheDeadlineFollowsTheFile:
    def test_a_15mb_3mf_gets_far_more_than_the_30s_default(self):
        """The reporter's file. 105 s measured, 30 s allowed."""
        assert _download_extension(15_150_000, 30.0) > 105.0

    def test_an_unknown_size_extends_nothing(self):
        """No SIZE reply means no transfer got under way. A printer that is not
        answering must still fail on the base deadline."""
        assert _download_extension(None, 30.0) == 0.0
        assert _download_extension(0, 30.0) == 0.0

    def test_a_small_file_that_already_fits_extends_nothing(self):
        assert _download_extension(64 * 1024, 30.0) == 0.0

    def test_it_is_capped(self):
        """``on_print_start`` holds a pooled DB connection across the whole 3MF
        hunt, so an unbounded deadline is a connection leak with extra steps."""
        assert _download_extension(10 * 1024 * 1024 * 1024, 30.0) == _DOWNLOAD_MAX_TIMEOUT - 30.0

    def test_the_floor_is_pessimistic_not_the_measured_rate(self):
        """25 KB/s. The reporter's P1S managed ~145 KB/s on its bad day, so the
        allowance is several times what a real slow link needs."""
        assert _DOWNLOAD_FLOOR_BYTES_PER_SEC == 25 * 1024


@pytest.mark.asyncio
class TestASlowTransferSurvivesItsDeadline:
    async def test_a_transfer_that_reports_its_size_is_given_the_time(self, tmp_path):
        """The whole point, at 1/1000 scale: a deadline the transfer blows past,
        and a printer that answered SIZE. 1 MB at the 25 KB/s floor buys ~39 s,
        so a transfer that takes 0.4 s finishes instead of being declared dead
        at 0.05 s. Nothing is patched here but the socket."""
        payload = b"x" * 4096

        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path, *, size_callback=None, cancel_event=None, **kwargs):
                size_callback(1_000_000)
                # Honouring the cancel flag is what makes this a real test: the
                # expired-deadline path sets it, and a transfer that ignored it
                # would be salvaged by the #1014 grace and prove nothing.
                for _ in range(40):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ftp_mod.DownloadCancelled(remote_path)
                    time.sleep(0.01)
                local_path.write_bytes(payload)
                return True

        with patch.object(ftp_mod, "BambuFTPClient", _Client):
            ok = await download_file_async("10.0.0.1", "x", "/f.3mf", tmp_path / "f.3mf", timeout=0.05)

        assert ok is True
        assert (tmp_path / "f.3mf").read_bytes() == payload

    async def test_a_transfer_that_blows_even_the_size_deadline_is_not_retried(self, tmp_path):
        """Otherwise the retry loop spends the whole stretched deadline again to
        reach the same conclusion -- four times, holding a pooled database
        connection, because ``on_print_start`` never lets go of one. Same reason
        ``UploadCancelled`` has been non-retryable since #2529."""
        attempts = {"n": 0}

        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path, *, size_callback=None, cancel_event=None, **kwargs):
                attempts["n"] += 1
                size_callback(1_000_000)
                for _ in range(200):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ftp_mod.DownloadCancelled(remote_path)
                    time.sleep(0.01)
                return False

        with (
            patch.object(ftp_mod, "BambuFTPClient", _Client),
            patch.object(ftp_mod, "_download_extension", lambda size, base: 0.2 if size else 0.0),
            pytest.raises(ftp_mod.DownloadDeadlineExceeded),
        ):
            await ftp_mod.with_ftp_retry(
                download_file_async,
                "10.0.0.11",
                "x",
                "/f.3mf",
                tmp_path / "f.3mf",
                timeout=0.05,
                max_retries=3,
                retry_delay=0,
            )

        assert attempts["n"] == 1, "a transfer that already had its full size-derived deadline was retried"

    async def test_an_ordinary_timeout_is_still_an_ordinary_retryable_miss(self, tmp_path):
        """No SIZE, no extension, no new exception -- the pre-existing contract."""

        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path, **kwargs):
                time.sleep(0.6)
                return False

        with patch.object(ftp_mod, "BambuFTPClient", _Client):
            assert await download_file_async("10.0.0.12", "x", "/f.3mf", tmp_path / "f.3mf", timeout=0.05) is False

    async def test_a_printer_that_never_answers_size_still_fails_on_time(self, tmp_path):
        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path, **kwargs):
                time.sleep(1.5)
                return False

        started = time.monotonic()
        with patch.object(ftp_mod, "BambuFTPClient", _Client):
            ok = await download_file_async("10.0.0.2", "x", "/f.3mf", tmp_path / "f.3mf", timeout=0.2)
        elapsed = time.monotonic() - started

        assert ok is False
        assert elapsed < 5.0, "an unknown size must not buy a transfer any extra time"


@pytest.mark.asyncio
class TestOnlyOneDownloadPerPrinter:
    async def test_the_second_download_waits_for_the_first(self):
        order: list[str] = []

        async def _hold(tag: str, seconds: float):
            async with _serialized_download("10.0.0.3", tag) as held:
                order.append(f"{tag}:in:{held}")
                await asyncio.sleep(seconds)
                order.append(f"{tag}:out")

        await asyncio.gather(_hold("a", 0.15), _hold("b", 0.01))

        assert order == ["a:in:True", "a:out", "b:in:True", "b:out"]

    async def test_a_waiter_that_gives_up_goes_anyway(self):
        """The gate is contention relief, not a correctness control. A print
        that lost its 3MF because a thumbnail held the printer would be a worse
        bug than the contention."""
        with patch.object(ftp_mod, "_DOWNLOAD_GATE_WAIT_SECONDS", 0.05):

            async def _holder():
                async with _serialized_download("10.0.0.4", "holder"):
                    await asyncio.sleep(0.3)

            async def _waiter():
                async with _serialized_download("10.0.0.4", "waiter") as held:
                    return held

            holder = asyncio.create_task(_holder())
            await asyncio.sleep(0.01)
            went_anyway = await _waiter()
            await holder

        assert went_anyway is False

    async def test_the_gate_is_released_when_the_body_raises(self):
        with pytest.raises(RuntimeError):
            async with _serialized_download("10.0.0.5", "boom"):
                raise RuntimeError("boom")

        async with _serialized_download("10.0.0.5", "after") as held:
            assert held is True

    async def test_a_real_download_takes_the_gate(self, tmp_path):
        """Not just the helper: the two entry points every download goes
        through have to be the ones holding it."""
        concurrent = {"max": 0, "now": 0}
        lock = threading.Lock()

        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path: Path, **kwargs):
                with lock:
                    concurrent["now"] += 1
                    concurrent["max"] = max(concurrent["max"], concurrent["now"])
                time.sleep(0.1)
                with lock:
                    concurrent["now"] -= 1
                local_path.write_bytes(b"data")
                return True

        with patch.object(ftp_mod, "BambuFTPClient", _Client):
            await asyncio.gather(
                download_file_async("10.0.0.6", "x", "/a.3mf", tmp_path / "a.3mf", timeout=30),
                download_file_try_paths_async("10.0.0.6", "x", ["/b.3mf"], tmp_path / "b.3mf", timeout=30),
            )

        assert concurrent["max"] == 1, "two downloads ran against one printer at the same time"

    async def test_the_file_browser_stays_outside_the_gate(self, tmp_path):
        """``printer_media`` documented itself lock-free before this gate
        existed, in both directions: a 3MF preview must not wait out somebody
        else's ten-gigabyte selection, and that selection must not hold the
        printer for the twenty minutes it legitimately takes."""
        overlapped = asyncio.Event()

        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path: Path, **kwargs):
                time.sleep(0.15)
                local_path.write_bytes(b"data")
                return True

        async def _holder():
            async with _serialized_download("10.0.0.10", "holder"):
                overlapped.set()
                await asyncio.sleep(0.3)

        with patch.object(ftp_mod, "BambuFTPClient", _Client):
            holder = asyncio.create_task(_holder())
            await overlapped.wait()
            started = time.monotonic()
            ok = await download_file_async(
                "10.0.0.10", "x", "/big.mp4", tmp_path / "big.mp4", timeout=30, serialize=False
            )
            elapsed = time.monotonic() - started
            await holder

        assert ok is True
        assert elapsed < 1.0, "an opted-out download queued behind the gate anyway"

    async def test_different_printers_do_not_queue_behind_each_other(self):
        started = asyncio.Event()

        async def _slow():
            async with _serialized_download("10.0.0.7", "slow"):
                started.set()
                await asyncio.sleep(0.3)

        task = asyncio.create_task(_slow())
        await started.wait()
        async with _serialized_download("10.0.0.8", "other") as held:
            assert held is True
        await task


@pytest.mark.asyncio
class TestTheCapNoLongerLeavesAWorkerOnTheSocket:
    async def test_a_capped_path_walk_stops_its_worker(self, tmp_path):
        """``asyncio.wait_for`` cannot cancel an executor thread, so the cap used
        to return while the worker kept walking the remaining paths -- still
        holding the printer's FTP socket. The reporter's log has one of those
        still going as the archive flow's own download landed."""
        cancelled = threading.Event()
        walked: list[str] = []

        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path, *, cancel_event=None, **kwargs):
                walked.append(remote_path)
                for _ in range(60):
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled.set()
                        raise ftp_mod.DownloadCancelled(remote_path)
                    time.sleep(0.01)
                return False

        with patch.object(ftp_mod, "BambuFTPClient", _Client):
            hit = await download_file_try_paths_async(
                "10.0.0.9", "x", ["/1.3mf", "/2.3mf", "/3.3mf"], tmp_path / "f.3mf", timeout=0.1
            )

        assert hit is None
        assert cancelled.is_set(), "the capped worker was left running on the printer's socket"
        assert walked == ["/1.3mf"], "the worker kept walking paths after its caller had given up"

    async def test_a_late_transport_error_is_not_logged_as_loop_noise(self, tmp_path):
        """Shielding the worker so the cap can wait it out means nobody is left
        to read what it raised, and asyncio reports that as a bare
        ``Future exception was never retrieved`` ERROR with a traceback -- after
        the caller has already logged the real failure. Precisely the class of
        noise #2968 was about, so it must not come back in through this door."""
        loop_errors: list[str] = []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, ctx: loop_errors.append(ctx.get("message", "")))

        class _Client(_FakeClient):
            def download_to_file(self, remote_path, local_path, **kwargs):
                time.sleep(0.3)
                raise OSError("late transport failure")

        with patch.object(ftp_mod, "BambuFTPClient", _Client):
            assert await download_file_try_paths_async("10.0.0.13", "x", ["/a"], tmp_path / "a", timeout=0.05) is None

        await asyncio.sleep(0.6)
        gc.collect()
        await asyncio.sleep(0)

        assert loop_errors == [], f"the shielded worker leaked its failure into the log: {loop_errors}"
