"""Every print-start path that binds the print's archive also reads its slicer
pauses (#2984), like the skip-objects list beside it."""

import io
import zipfile
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import settings as app_settings
from backend.app.models.archive import PrintArchive
from backend.tests.integration.test_print_start_name_match_ap2 import (  # noqa: F401
    DISPATCH,
    SUBTASK,
    _clear_print_state,
    _start,
    _stuck_archive,
)


async def _start_from_cache(test_engine, printer_id: int, cached) -> None:
    """``_start`` with the 3MF already pulled by the cover endpoint (#972)."""
    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    state = MagicMock(sdcard=True, sdcard_reported=True, subtask_id="222", progress=0.0)
    with ExitStack() as stack:
        for target, value in {
            "backend.app.main.async_session": session_maker,
            "backend.app.core.database.async_session": session_maker,
            "backend.app.main.get_cached_3mf": MagicMock(return_value=cached),
            "backend.app.main.cache_3mf_download": MagicMock(),
            "backend.app.main._record_energy_start": AsyncMock(),
            "backend.app.main._send_print_start_notification": AsyncMock(),
            "backend.app.main._maybe_start_layer_timelapse": MagicMock(),
            "backend.app.main._capture_timelapse_baseline_at_start": AsyncMock(),
            "backend.app.main._store_spoolman_print_data": AsyncMock(),
            "backend.app.main._schedule_archive_cost_estimate": MagicMock(),
            "backend.app.services.usage_tracker.on_print_start": AsyncMock(),
        }.items():
            stack.enter_context(patch(target, value))
        for name in ("notification_service", "smart_plug_manager", "ws_manager", "mqtt_relay"):
            mock = stack.enter_context(patch(f"backend.app.main.{name}"))
            for method in (
                "on_print_start",
                "send_print_start",
                "send_archive_created",
                "send_archive_updated",
                "on_archive_created",
            ):
                setattr(mock, method, AsyncMock())
        pm = stack.enter_context(patch("backend.app.main.printer_manager"))
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TESTAP2"))
        pm.get_client = MagicMock(return_value=None)

        from backend.app.main import on_print_start

        await on_print_start(
            printer_id, {"filename": DISPATCH, "subtask_name": SUBTASK, "raw_data": {"subtask_id": "222"}}
        )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_resumed_on_its_subtask_id(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    archive_id = await _stuck_archive(db_session, printer.id, "222")

    with patch("backend.app.main._load_pauses_from_3mf") as load:
        await _start(test_engine, printer.id, "222")

    archive = await db_session.get(PrintArchive, archive_id)
    load.assert_called_once()
    assert load.call_args.args == (app_settings.base_dir / archive.file_path, printer.id)
    # As a resumption: what was warned about and passed is kept.
    assert set(load.call_args.kwargs) == {"warned_layers", "passed_through_layer"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_resumed_by_name(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    archive_id = await _stuck_archive(db_session, printer.id, None)

    with patch("backend.app.main._load_pauses_from_3mf") as load:
        await _start(test_engine, printer.id, "222")

    archive = await db_session.get(PrintArchive, archive_id)
    load.assert_called_once()
    assert load.call_args.args == (app_settings.base_dir / archive.file_path, printer.id)
    # As a resumption: what was warned about and passed is kept.
    assert set(load.call_args.kwargs) == {"warned_layers", "passed_through_layer"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_new_archive_from_the_downloaded_3mf(test_engine, db_session, printer_factory, tmp_path):
    printer = await printer_factory()
    downloaded = tmp_path / f"{SUBTASK}.gcode.3mf"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", "M73 P0 R10\nM73 L2\nM400 U1\n")
    downloaded.write_bytes(buf.getvalue())

    printer_id = printer.id
    with (
        patch("backend.app.main._load_pauses_from_3mf") as load,
        # The archive is copied under archive_dir; keep it out of the real one.
        patch.object(app_settings, "base_dir", tmp_path),
        patch.object(app_settings, "archive_dir", tmp_path / "archive"),
    ):
        await _start_from_cache(test_engine, printer_id, downloaded)
        expected_base = app_settings.base_dir

    db_session.expire_all()
    archive = (await db_session.execute(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).scalar_one()
    # The archived copy, not the download: that one may be removed meanwhile.
    assert archive.file_path and expected_base / archive.file_path != downloaded
    assert (expected_base / archive.file_path).is_file()
    load.assert_called_once_with(expected_base / archive.file_path, printer_id)
