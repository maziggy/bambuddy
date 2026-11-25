"""Timelapse editor re-encodes run as background jobs that report progress and can be cancelled."""

import asyncio
import shutil
import stat
import sys
from pathlib import Path

import pytest

from backend.app.services import timelapse_jobs
from backend.app.services.timelapse_processor import TimelapseProcessor, _communicate


@pytest.fixture(autouse=True)
def _no_jobs():
    timelapse_jobs._jobs.clear()
    yield
    timelapse_jobs._jobs.clear()


async def _finished(job: timelapse_jobs.TimelapseJob) -> None:
    assert job.task is not None
    await asyncio.wait_for(asyncio.shield(job.task), 5)


class TestJobs:
    @pytest.mark.asyncio
    async def test_a_finished_job_keeps_its_result(self):
        async def run(job):
            job.set_progress(0.5)
            return "archive/1/out.mp4", "Saved as out.mp4"

        job = timelapse_jobs.start_job(1, run)
        assert job.status == timelapse_jobs.RUNNING
        await _finished(job)

        assert timelapse_jobs.get_job(1) is job
        assert (job.status, job.progress, job.output_path, job.message) == (
            timelapse_jobs.COMPLETED,
            1.0,
            "archive/1/out.mp4",
            "Saved as out.mp4",
        )

    @pytest.mark.asyncio
    async def test_a_failed_job_says_why(self):
        async def run(job):
            raise RuntimeError("Video processing failed")

        job = timelapse_jobs.start_job(1, run)
        await _finished(job)

        assert (job.status, job.error) == (timelapse_jobs.FAILED, "Video processing failed")

    @pytest.mark.asyncio
    async def test_one_job_per_archive_at_a_time(self):
        release = asyncio.Event()

        async def run(job):
            await release.wait()
            return "a", "b"

        job = timelapse_jobs.start_job(1, run)
        with pytest.raises(timelapse_jobs.JobAlreadyRunning):
            timelapse_jobs.start_job(1, run)
        other = timelapse_jobs.start_job(2, run)

        release.set()
        await _finished(job)
        await _finished(other)
        again = timelapse_jobs.start_job(1, run)
        await _finished(again)
        assert again.status == timelapse_jobs.COMPLETED

    @pytest.mark.asyncio
    async def test_cancelling_stops_the_job(self):
        started = asyncio.Event()
        stopped = []

        async def run(job):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stopped.append(True)
                raise
            return "a", "b"

        job = timelapse_jobs.start_job(1, run)
        await started.wait()

        assert await timelapse_jobs.cancel_job(1) is True
        assert job.status == timelapse_jobs.CANCELLED
        assert stopped == [True]
        assert await timelapse_jobs.cancel_job(1) is False

    @pytest.mark.asyncio
    async def test_shutdown_cancels_every_running_job(self):
        async def run(job):
            await asyncio.sleep(30)
            return "a", "b"

        jobs = [timelapse_jobs.start_job(i, run) for i in (1, 2)]
        await asyncio.sleep(0)

        await timelapse_jobs.cancel_all()

        assert [j.status for j in jobs] == [timelapse_jobs.CANCELLED] * 2

    @pytest.mark.asyncio
    async def test_no_job_to_cancel(self):
        assert await timelapse_jobs.cancel_job(7) is False


def _fake_ffmpeg(tmp_path: Path, body: str) -> str:
    """A stand-in ffmpeg: a Python script; the output path is its last argument."""
    script = tmp_path / "ffmpeg"
    script.write_text(f"#!{sys.executable}\nimport sys, time\nout = sys.argv[-1]\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _processor(tmp_path: Path, ffmpeg: str) -> TimelapseProcessor:
    processor = TimelapseProcessor.__new__(TimelapseProcessor)
    processor.input_path = tmp_path / "in.mp4"
    processor.ffmpeg = ffmpeg
    processor.ffprobe = ffmpeg
    return processor


class TestProcessor:
    @pytest.mark.asyncio
    async def test_progress_is_the_share_of_the_output_written(self, tmp_path):
        ffmpeg = _fake_ffmpeg(
            tmp_path,
            "open(out, 'wb').write(b'x')\n"
            "for us in ('N/A', '1000000', '2000000'):\n"
            "    print(f'out_time_us={us}', flush=True)\n"
            "    print('progress=continue', flush=True)",
        )
        seen: list[float] = []

        # 8 s of input at double speed: 4 s of output.
        ok = await _processor(tmp_path, ffmpeg).process(
            tmp_path / "out.mp4", trim_start=2, trim_end=10, speed=2.0, on_progress=seen.append
        )

        assert ok is True
        assert seen == [0.25, 0.5]

    @pytest.mark.asyncio
    async def test_cancelling_stops_ffmpeg_and_drops_the_partial_file(self, tmp_path):
        ffmpeg = _fake_ffmpeg(
            tmp_path,
            "open(out, 'wb').write(b'partial')\nprint('out_time_us=1000000', flush=True)\ntime.sleep(60)",
        )
        output = tmp_path / "out.mp4"
        reported = asyncio.Event()

        task = asyncio.create_task(
            _processor(tmp_path, ffmpeg).process(output, trim_end=10, on_progress=lambda _: reported.set())
        )
        await asyncio.wait_for(reported.wait(), 10)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert not output.exists()

    @pytest.mark.asyncio
    async def test_a_probe_that_hangs_is_killed(self):
        sleep = shutil.which("sleep")
        if sleep is None:
            pytest.skip("no sleep binary")
        process = await asyncio.create_subprocess_exec(
            sleep, "30", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        # Killed, not waited out: well inside the 30 s the process would run.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_communicate(process, 0.2), 5)

        assert process.returncode is not None


@pytest.mark.asyncio
async def test_without_a_known_length_it_encodes_without_progress(tmp_path):
    ffmpeg = _fake_ffmpeg(tmp_path, "open(out, 'wb').write(b'x')\nprint('out_time_us=1000000', flush=True)")
    processor = _processor(tmp_path, ffmpeg)
    processor.ffprobe = "/nonexistent/ffprobe"
    seen: list[float] = []

    ok = await processor.process(tmp_path / "out.mp4", on_progress=seen.append)

    assert ok is True
    assert seen == []
