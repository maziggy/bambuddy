"""The timelapse editor's re-encode runs as a job: started, polled, cancelled."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.services import timelapse_jobs
from backend.app.services.timelapse_processor import TimelapseProcessor

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _no_jobs():
    timelapse_jobs._jobs.clear()
    yield
    timelapse_jobs._jobs.clear()


@pytest.fixture
def data_dir(tmp_path):
    from backend.app.api.routes import archives

    with (
        patch.object(archives.settings, "base_dir", tmp_path),
        patch("backend.app.services.timelapse_processor.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
    ):
        yield tmp_path


async def _archive(printer_factory, archive_factory, data_dir: Path):
    printer = await printer_factory()
    video = data_dir / "archive" / "1" / "timelapse.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"original")
    return await archive_factory(printer.id, timelapse_path="archive/1/timelapse.mp4")


async def _settled(client: AsyncClient, archive_id: int) -> dict:
    for _ in range(100):
        body = (await client.get(f"/api/v1/archives/{archive_id}/timelapse/job")).json()
        if body["status"] != "running":
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("job still running")


def _encoder(gate: asyncio.Event | None = None, ok: bool = True):
    async def process(self, output_path, on_progress=None, **kwargs):
        on_progress(0.4)
        if gate is not None:
            await gate.wait()
        output_path.write_bytes(b"edited")
        return ok

    return patch.object(TimelapseProcessor, "process", process)


async def test_the_request_returns_before_the_encode_and_the_job_reports_how_it_went(
    async_client: AsyncClient, printer_factory, archive_factory, data_dir
):
    archive = await _archive(printer_factory, archive_factory, data_dir)
    gate = asyncio.Event()

    with _encoder(gate):
        started = await async_client.post(
            f"/api/v1/archives/{archive.id}/timelapse/process", data={"save_mode": "replace", "speed": "2"}
        )
        assert started.status_code == 202, started.text
        assert started.json()["status"] == "running"
        await asyncio.sleep(0.05)
        running = (await async_client.get(f"/api/v1/archives/{archive.id}/timelapse/job")).json()
        gate.set()
        done = await _settled(async_client, archive.id)

    assert (running["status"], running["progress"]) == ("running", 0.4)
    assert done["status"] == "completed"
    assert done["message"] == "Timelapse replaced successfully"
    assert (data_dir / "archive/1/timelapse.mp4").read_bytes() == b"edited"


async def test_a_second_start_while_one_runs_is_refused(
    async_client: AsyncClient, printer_factory, archive_factory, data_dir
):
    archive = await _archive(printer_factory, archive_factory, data_dir)
    gate = asyncio.Event()

    with _encoder(gate):
        first = await async_client.post(
            f"/api/v1/archives/{archive.id}/timelapse/process", data={"save_mode": "replace"}
        )
        second = await async_client.post(
            f"/api/v1/archives/{archive.id}/timelapse/process", data={"save_mode": "replace"}
        )
        gate.set()
        await _settled(async_client, archive.id)

    assert (first.status_code, second.status_code) == (202, 409)


async def test_cancelling_keeps_the_original_and_removes_the_temporary_files(
    async_client: AsyncClient, printer_factory, archive_factory, data_dir
):
    import tempfile

    archive = await _archive(printer_factory, archive_factory, data_dir)
    before = set(Path(tempfile.gettempdir()).glob(f"*_{archive.id}_*"))

    with _encoder(asyncio.Event()):
        await async_client.post(
            f"/api/v1/archives/{archive.id}/timelapse/process",
            data={"save_mode": "replace"},
            files={"audio": ("music.mp3", b"ID3", "audio/mpeg")},
        )
        cancelled = await async_client.delete(f"/api/v1/archives/{archive.id}/timelapse/job")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert (data_dir / "archive/1/timelapse.mp4").read_bytes() == b"original"
    assert set(Path(tempfile.gettempdir()).glob(f"*_{archive.id}_*")) == before
    assert (await async_client.delete(f"/api/v1/archives/{archive.id}/timelapse/job")).status_code == 404


async def test_a_failed_encode_is_reported(async_client: AsyncClient, printer_factory, archive_factory, data_dir):
    import tempfile

    archive = await _archive(printer_factory, archive_factory, data_dir)
    before = set(Path(tempfile.gettempdir()).glob(f"*_{archive.id}_*"))

    with _encoder(ok=False):
        await async_client.post(f"/api/v1/archives/{archive.id}/timelapse/process", data={"save_mode": "replace"})
        done = await _settled(async_client, archive.id)

    assert (done["status"], done["error"]) == ("failed", "Video processing failed")
    assert (data_dir / "archive/1/timelapse.mp4").read_bytes() == b"original"
    # The half-written output goes too.
    assert set(Path(tempfile.gettempdir()).glob(f"*_{archive.id}_*")) == before


async def test_no_job_yet(async_client: AsyncClient, printer_factory, archive_factory, data_dir):
    archive = await _archive(printer_factory, archive_factory, data_dir)

    assert (await async_client.get(f"/api/v1/archives/{archive.id}/timelapse/job")).status_code == 404
    assert (await async_client.get("/api/v1/archives/999999/timelapse/job")).status_code == 404


async def test_the_music_volume_reaches_the_encode(
    async_client: AsyncClient, printer_factory, archive_factory, data_dir
):
    archive = await _archive(printer_factory, archive_factory, data_dir)
    seen = {}

    async def process(self, output_path, on_progress=None, **kwargs):
        seen.update(kwargs)
        output_path.write_bytes(b"edited")
        return True

    with patch.object(TimelapseProcessor, "process", process):
        await async_client.post(
            f"/api/v1/archives/{archive.id}/timelapse/process",
            data={"save_mode": "replace", "audio_volume": "0.3"},
            files={"audio": ("music.mp3", b"ID3", "audio/mpeg")},
        )
        await _settled(async_client, archive.id)

    assert seen["audio_volume"] == 0.3


@pytest.mark.parametrize("volume", ["-0.1", "1.5"])
async def test_a_volume_out_of_range_is_refused(
    async_client: AsyncClient, printer_factory, archive_factory, data_dir, volume
):
    archive = await _archive(printer_factory, archive_factory, data_dir)

    response = await async_client.post(
        f"/api/v1/archives/{archive.id}/timelapse/process", data={"save_mode": "replace", "audio_volume": volume}
    )

    assert response.status_code == 400
    assert timelapse_jobs.get_job(archive.id) is None


@pytest.mark.parametrize("change", ["removed", "replaced"])
async def test_a_timelapse_changed_during_the_edit_is_not_overwritten(
    async_client: AsyncClient, printer_factory, archive_factory, data_dir, db_session, change
):
    archive = await _archive(printer_factory, archive_factory, data_dir)
    video = data_dir / "archive/1/timelapse.mp4"
    gate = asyncio.Event()

    with _encoder(gate):
        await async_client.post(f"/api/v1/archives/{archive.id}/timelapse/process", data={"save_mode": "replace"})
        if change == "removed":
            assert (await async_client.delete(f"/api/v1/archives/{archive.id}/timelapse")).status_code == 200
        else:
            video.unlink()
            video.write_bytes(b"a newer video")
        gate.set()
        done = await _settled(async_client, archive.id)

    assert done["status"] == "failed"
    assert "removed or replaced" in done["error"]
    if change == "removed":
        assert not video.exists()
    else:
        assert video.read_bytes() == b"a newer video"


async def test_two_saves_racing_get_one_job_and_a_409(
    async_client: AsyncClient, printer_factory, archive_factory, data_dir
):
    """The second passed the early check while the first one's audio upload was read."""
    import tempfile

    archive = await _archive(printer_factory, archive_factory, data_dir)
    before = set(Path(tempfile.gettempdir()).glob(f"audio_{archive.id}_*"))
    real_start = timelapse_jobs.start_job

    async def other_request(job):
        await asyncio.Event().wait()
        return "", ""

    def raced(archive_id, run):
        real_start(archive_id, other_request)
        return real_start(archive_id, run)

    with _encoder(asyncio.Event()), patch.object(timelapse_jobs, "start_job", raced):
        response = await async_client.post(
            f"/api/v1/archives/{archive.id}/timelapse/process",
            data={"save_mode": "replace"},
            files={"audio": ("music.mp3", b"ID3", "audio/mpeg")},
        )
    await timelapse_jobs.cancel_all()

    assert response.status_code == 409
    assert set(Path(tempfile.gettempdir()).glob(f"audio_{archive.id}_*")) == before
