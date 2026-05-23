"""Regression for #1485 follow-up: timelapse baseline on restart-recovery.

When Bambuddy restarts mid-print, the first MQTT push has
``_previous_gcode_state = None`` which the #1304 guard treats as "first push
after Bambuddy startup, don't fire on_print_start" — avoiding duplicate
archive creation. But that path is also where ``_capture_timelapse_baseline_at_start``
lives, so without a separate hook the baseline is never captured. The
completion-time scan then falls into its "take baseline now" fallback
that snapshots the SD card AFTER the in-flight MP4 has landed, the new
file ends up in the baseline set, and no diff ever matches.

bambu_mqtt.py:_process_message now fires a sibling ``on_print_running_observed``
callback in this case. main.py wires it to ``on_print_running_observed``
which captures the baseline. These tests verify that handler.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import _timelapse_baselines


@pytest.fixture(autouse=True)
def _clear_baselines():
    _timelapse_baselines.clear()
    yield
    _timelapse_baselines.clear()


@pytest.mark.asyncio
async def test_running_observed_captures_baseline_on_restart_recovery():
    """The handler must capture the printer's existing-videos snapshot so
    the completion-time scan has something to set-diff against. This is
    the case the in-the-field pwostran report (#1485) hits: pre-reboot
    baseline of 7 files lost on restart, post-reboot fallback baseline
    sees the 8 files (including the just-uploaded one) → no new file."""
    mock_printer = MagicMock()
    mock_printer.id = 1
    mock_printer.name = "TestP1S"
    mock_printer.ip_address = "192.168.1.100"
    mock_printer.access_code = "12345678"
    mock_printer.model = "P1S"

    existing_videos = [
        {"name": "earlier_a.mp4", "is_directory": False, "path": "/timelapse/earlier_a.mp4"},
        {"name": "earlier_b.mp4", "is_directory": False, "path": "/timelapse/earlier_b.mp4"},
        {"name": "earlier_c.mp4", "is_directory": False, "path": "/timelapse/earlier_c.mp4"},
    ]

    def execute_router(stmt, *args, **kwargs):
        return MagicMock(scalar_one_or_none=MagicMock(return_value=mock_printer))

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_router)

    with (
        patch("backend.app.main.async_session") as mock_session_maker,
        patch(
            "backend.app.main._list_timelapse_videos",
            new=AsyncMock(return_value=(existing_videos, "/timelapse")),
        ),
    ):
        mock_session_maker.return_value = mock_session

        from backend.app.main import on_print_running_observed

        await on_print_running_observed(
            1,
            {
                "filename": "/data/Metadata/test_print.gcode",
                "subtask_name": "Test_Print",
                "remaining_time": 3600,
                "raw_data": {},
                "ams_mapping": None,
            },
        )

    assert _timelapse_baselines.get(1) == {"earlier_a.mp4", "earlier_b.mp4", "earlier_c.mp4"}, (
        "restart-recovery handler must capture the printer's existing-videos "
        "baseline so the completion-time scan can set-diff to find the new file"
    )


@pytest.mark.asyncio
async def test_running_observed_skips_when_baseline_already_present():
    """If on_print_start already ran in this Bambuddy process for the same
    printer (the realistic same-session race), a second capture would
    overwrite the correct pre-print baseline with one taken later — which
    could include the in-flight MP4. Skip when a baseline exists."""
    _timelapse_baselines[1] = {"pre_existing_a.mp4", "pre_existing_b.mp4"}

    with (
        patch("backend.app.main.async_session") as mock_session_maker,
        patch("backend.app.main._list_timelapse_videos", new=AsyncMock()) as mock_list,
    ):
        from backend.app.main import on_print_running_observed

        await on_print_running_observed(
            1,
            {
                "filename": "/data/Metadata/test_print.gcode",
                "subtask_name": "Test_Print",
                "remaining_time": 3600,
                "raw_data": {},
                "ams_mapping": None,
            },
        )

        # Neither the DB lookup nor the FTP scan should have run.
        mock_session_maker.assert_not_called()
        mock_list.assert_not_called()

    # Original baseline preserved.
    assert _timelapse_baselines[1] == {"pre_existing_a.mp4", "pre_existing_b.mp4"}


@pytest.mark.asyncio
async def test_running_observed_skips_when_printer_row_missing():
    """If the printer was deleted between the MQTT push and this handler
    running, we can't capture anything — log and return without raising."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    with (
        patch("backend.app.main.async_session") as mock_session_maker,
        patch("backend.app.main._list_timelapse_videos", new=AsyncMock()) as mock_list,
    ):
        mock_session_maker.return_value = mock_session

        from backend.app.main import on_print_running_observed

        # Should not raise.
        await on_print_running_observed(
            999,
            {
                "filename": "/data/Metadata/test_print.gcode",
                "subtask_name": "Test_Print",
                "remaining_time": 3600,
                "raw_data": {},
                "ams_mapping": None,
            },
        )

        # FTP scan must not run if the printer row didn't resolve.
        mock_list.assert_not_called()

    assert 999 not in _timelapse_baselines
