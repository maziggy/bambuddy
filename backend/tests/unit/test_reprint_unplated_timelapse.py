"""A reprint of a single-plate archive does not leave the old video behind.

A video attached by hand, or before the plate column existed, has no plate on
record. On a reprint it was always kept, because on a Send All archive it can
be another plate's only copy; on an archive with one plate there is no other
plate, so it was just an orphan file per reprint. It is now deleted there, and
still kept when the archive's 3MF has several plates or cannot be read.
"""

import zipfile
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.main import register_expected_print
from backend.tests.unit.test_reprint_clears_stale_timelapse import (  # noqa: F401
    _build_mocks,
    _clear_dicts,
    _patches,
)

pytestmark = pytest.mark.asyncio


def _three_mf(path, plates: int | None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if plates is None:
        path.write_bytes(b"not a zip")
        return
    body = "".join(f'<plate><metadata key="index" value="{n}"/></plate>' for n in range(1, plates + 1))
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", f"<config>{body}</config>")


async def _reprint(tmp_path, plates):
    relpath = "archives/42/timelapse/by-hand.mp4"
    video = tmp_path / relpath
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"old video")
    _three_mf(tmp_path / "archives/42/MyModel.3mf", plates)

    printer = MagicMock(id=1, auto_archive=True, external_camera_enabled=False, external_camera_url=None)
    archive = MagicMock(
        id=42,
        filename="MyModel.3mf",
        subtask_id=None,
        print_time_seconds=None,
        created_by_id=None,
        printer_id=1,
        print_name="MyModel",
        status="archived",
        file_path="archives/42/MyModel.3mf",
        energy_start_kwh=None,
        timelapse_path=relpath,
        timelapse_plate_id=None,  # not on record
    )
    register_expected_print(1, "MyModel.3mf", archive_id=42, ams_mapping=None, plate_id=1)
    session = _build_mocks(printer, archive)
    patches = _patches()
    with ExitStack() as stack:
        maker, notif, plug, ws, pm, relay = (stack.enter_context(p) for p in patches[:6])
        for extra in patches[6:]:
            stack.enter_context(extra)
        stack.enter_context(patch.object(app_settings, "base_dir", tmp_path))
        # The mock printer has no camera to reach.
        stack.enter_context(
            patch("backend.app.services.camera.capture_camera_frame_bytes", AsyncMock(return_value=None))
        )
        maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        relay.on_print_start = AsyncMock()
        pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))
        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "MyModel.3mf", "subtask_name": "MyModel"})
    return video, archive


async def test_on_a_single_plate_archive_it_is_deleted(tmp_path):
    video, archive = await _reprint(tmp_path, plates=1)

    assert archive.timelapse_path is None
    assert not video.exists()


@pytest.mark.parametrize("plates", [2, None], ids=["several-plates", "unreadable-3mf"])
async def test_where_it_could_be_another_plates_it_is_kept(tmp_path, plates):
    video, archive = await _reprint(tmp_path, plates=plates)

    assert archive.timelapse_path is None
    assert video.exists()
