"""Regression for #1707: Telegram (and any image-bearing) notification on a
reprint from archive showed the *original* print's finish photo because the
expected-archive branch never reset ``archive.timelapse_path``.

The source archive row is reused for reprints. With ``timelapse_path`` still
pointing at the original run's downloaded MP4:
  - ``_scan_for_timelapse_with_retries`` early-returns ("already has timelapse")
    and never downloads the reprint's video.
  - ``_capture_finish_photo_from_timelapse`` reads the stale path, extracts the
    *original* last frame, and ships it as the reprint's finish photo.

The fix clears ``archive.timelapse_path`` (and unlinks the stale file) at
expected-archive promotion so the scan + photo path run fresh.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    _timelapse_baselines,
    register_expected_print,
)


@pytest.fixture(autouse=True)
def _clear_dicts():
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()
    _timelapse_baselines.clear()
    yield
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()
    _timelapse_baselines.clear()


def _patches():
    """Common patches for driving on_print_start without side effects."""
    return (
        patch("backend.app.main.async_session"),
        patch("backend.app.main.notification_service"),
        patch("backend.app.main.smart_plug_manager"),
        patch("backend.app.main.ws_manager"),
        patch("backend.app.main.printer_manager"),
        patch("backend.app.main.mqtt_relay"),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._load_objects_from_archive"),
        patch("backend.app.main._store_spoolman_print_data", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch(
            "backend.app.main._list_timelapse_videos",
            new=AsyncMock(return_value=([], "/timelapse")),
        ),
    )


def _build_mocks(mock_printer, mock_archive):
    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from printers" in sql or "from printer " in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_printer),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_printer]))),
            )
        if "from print_archives" in sql or "from print_archive" in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_archive),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_archive]))),
            )
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_router)
    mock_session.commit = AsyncMock()
    return mock_session


@pytest.mark.asyncio
async def test_reprint_clears_timelapse_path_and_unlinks_stale_file(tmp_path):
    """On reprint promotion, timelapse_path must be reset to None and the old
    on-disk video unlinked, so the completion-time scanner and finish-photo
    extractor don't reuse the original run's frame."""
    mock_printer = MagicMock()
    mock_printer.id = 1
    mock_printer.auto_archive = True
    mock_printer.external_camera_enabled = False
    mock_printer.external_camera_url = None
    mock_printer.name = "TestP2S"

    # Lay down a fake stale timelapse under a tmp base_dir so the unlink
    # actually has a file to remove.
    relpath = "archives/42/timelapse/original.mp4"
    stale_file = tmp_path / relpath
    stale_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.write_bytes(b"old timelapse bytes")
    assert stale_file.exists()

    mock_archive = MagicMock()
    mock_archive.id = 42
    mock_archive.filename = "MyModel.3mf"
    mock_archive.subtask_id = None
    mock_archive.print_time_seconds = None
    mock_archive.created_by_id = None
    mock_archive.printer_id = 1
    mock_archive.print_name = "MyModel"
    mock_archive.status = "archived"
    mock_archive.file_path = "archives/42/MyModel.3mf"
    mock_archive.energy_start_kwh = None
    mock_archive.timelapse_path = relpath  # stale from the original run

    register_expected_print(1, "MyModel.3mf", archive_id=42, ams_mapping=None)

    mock_session = _build_mocks(mock_printer, mock_archive)

    (
        async_session_p,
        notif_p,
        plug_p,
        ws_p,
        pm_p,
        relay_p,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
    ) = _patches()

    with (
        async_session_p as mock_session_maker,
        notif_p as mock_notif,
        plug_p as mock_plug,
        ws_p as mock_ws,
        pm_p as mock_pm,
        relay_p as mock_relay,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
        patch.object(app_settings, "base_dir", tmp_path),
    ):
        mock_session_maker.return_value = mock_session
        mock_notif.on_print_start = AsyncMock()
        mock_plug.on_print_start = AsyncMock()
        mock_ws.send_print_start = AsyncMock()
        mock_ws.send_archive_updated = AsyncMock()
        mock_relay.on_print_start = AsyncMock()
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "MyModel.3mf", "subtask_name": "MyModel"})

    assert mock_archive.timelapse_path is None, (
        "expected-archive branch must clear timelapse_path on reprint so "
        "_scan_for_timelapse_with_retries doesn't early-return and "
        "_capture_finish_photo_from_timelapse doesn't extract the original "
        "run's last frame (#1707)"
    )
    assert not stale_file.exists(), (
        "old timelapse file must be unlinked at reprint promotion to avoid orphans in the archive directory"
    )


@pytest.mark.asyncio
async def test_reprint_with_no_timelapse_path_is_noop(tmp_path):
    """When archive has no prior timelapse_path (first print, or already
    cleared), promotion must still succeed and not raise on the unlink path."""
    mock_printer = MagicMock()
    mock_printer.id = 1
    mock_printer.auto_archive = True
    mock_printer.external_camera_enabled = False
    mock_printer.external_camera_url = None
    mock_printer.name = "TestP2S"

    mock_archive = MagicMock()
    mock_archive.id = 99
    mock_archive.filename = "FreshFile.3mf"
    mock_archive.subtask_id = None
    mock_archive.print_time_seconds = None
    mock_archive.created_by_id = None
    mock_archive.printer_id = 1
    mock_archive.print_name = "FreshFile"
    mock_archive.status = "archived"
    mock_archive.file_path = "archives/99/FreshFile.3mf"
    mock_archive.energy_start_kwh = None
    mock_archive.timelapse_path = None  # nothing to clean up

    register_expected_print(1, "FreshFile.3mf", archive_id=99, ams_mapping=None)

    mock_session = _build_mocks(mock_printer, mock_archive)

    (
        async_session_p,
        notif_p,
        plug_p,
        ws_p,
        pm_p,
        relay_p,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
    ) = _patches()

    with (
        async_session_p as mock_session_maker,
        notif_p as mock_notif,
        plug_p as mock_plug,
        ws_p as mock_ws,
        pm_p as mock_pm,
        relay_p as mock_relay,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
        patch.object(app_settings, "base_dir", tmp_path),
    ):
        mock_session_maker.return_value = mock_session
        mock_notif.on_print_start = AsyncMock()
        mock_plug.on_print_start = AsyncMock()
        mock_ws.send_print_start = AsyncMock()
        mock_ws.send_archive_updated = AsyncMock()
        mock_relay.on_print_start = AsyncMock()
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "FreshFile.3mf", "subtask_name": "FreshFile"})

    assert mock_archive.timelapse_path is None
    assert mock_archive.status == "printing"


@pytest.mark.asyncio
async def test_reprint_with_missing_stale_file_does_not_raise(tmp_path):
    """If the stale file referenced by timelapse_path no longer exists on
    disk (user deleted, archive purge, container rebuilt with bind-mount
    drift), promotion must still clear the field cleanly without raising."""
    mock_printer = MagicMock()
    mock_printer.id = 1
    mock_printer.auto_archive = True
    mock_printer.external_camera_enabled = False
    mock_printer.external_camera_url = None
    mock_printer.name = "TestP2S"

    mock_archive = MagicMock()
    mock_archive.id = 7
    mock_archive.filename = "Ghost.3mf"
    mock_archive.subtask_id = None
    mock_archive.print_time_seconds = None
    mock_archive.created_by_id = None
    mock_archive.printer_id = 1
    mock_archive.print_name = "Ghost"
    mock_archive.status = "archived"
    mock_archive.file_path = "archives/7/Ghost.3mf"
    mock_archive.energy_start_kwh = None
    # Path points at a file that doesn't exist under tmp_path.
    mock_archive.timelapse_path = "archives/7/timelapse/vanished.mp4"

    register_expected_print(1, "Ghost.3mf", archive_id=7, ams_mapping=None)

    mock_session = _build_mocks(mock_printer, mock_archive)

    (
        async_session_p,
        notif_p,
        plug_p,
        ws_p,
        pm_p,
        relay_p,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
    ) = _patches()

    with (
        async_session_p as mock_session_maker,
        notif_p as mock_notif,
        plug_p as mock_plug,
        ws_p as mock_ws,
        pm_p as mock_pm,
        relay_p as mock_relay,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
        patch.object(app_settings, "base_dir", tmp_path),
    ):
        mock_session_maker.return_value = mock_session
        mock_notif.on_print_start = AsyncMock()
        mock_plug.on_print_start = AsyncMock()
        mock_ws.send_print_start = AsyncMock()
        mock_ws.send_archive_updated = AsyncMock()
        mock_relay.on_print_start = AsyncMock()
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "Ghost.3mf", "subtask_name": "Ghost"})

    assert mock_archive.timelapse_path is None
    assert mock_archive.status == "printing"
