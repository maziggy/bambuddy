"""The post-print SD cleanup never deletes the file the printer is printing (#3009).

The cleanup works out what to delete from the finished archive. When
reconciliation closes an old archive because the printer has started a new
job, and that job is the same file reprinted from the printer's screen, the
cleanup deleted the file of the print that was running.
"""

import logging
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import _cleanup_sd_card_after_print, _sd_files_in_use
from backend.app.services.bambu_ftp import DeleteResult


def _state(state="RUNNING", gcode_file="", subtask_name=""):
    return SimpleNamespace(state=state, gcode_file=gcode_file, subtask_name=subtask_name)


class TestFilesInUse:
    @pytest.mark.parametrize("state", ["IDLE", "FINISH", "FAILED", "", "unknown"])
    def test_nothing_is_held_back_when_the_printer_is_not_busy(self, state):
        assert _sd_files_in_use(_state(state, "cube.3mf", "cube")) == set()

    def test_no_state(self):
        assert _sd_files_in_use(None) == set()

    @pytest.mark.parametrize("state", ["RUNNING", "PAUSE", "PREPARE", "SLICING"])
    def test_busy_states(self, state):
        assert "cube.3mf" in _sd_files_in_use(_state(state, "cube.3mf"))

    @pytest.mark.parametrize(
        "gcode_file",
        [
            "Cube_Part.3mf",
            "/Cube_Part.3mf",
            "/sdcard/Cube_Part.3mf",
            "ftp://Cube_Part.3mf",
            "file:///sdcard/Cube_Part.3mf",
        ],
    )
    def test_gcode_file_spellings(self, gcode_file):
        assert "cube_part.3mf" in _sd_files_in_use(_state(gcode_file=gcode_file))

    @pytest.mark.parametrize("name", ["Part #2.3mf", "What?.3mf", "ftp://[odd.3mf"])
    def test_awkward_names_are_kept_whole(self, name):
        assert PurePosixPath(name.split("://", 1)[-1]).name.lower() in _sd_files_in_use(_state(gcode_file=name))

    def test_unexpected_values_do_not_raise(self):
        assert _sd_files_in_use(SimpleNamespace(state="RUNNING", gcode_file=None, subtask_name=None)) == set()
        assert _sd_files_in_use(SimpleNamespace(state=MagicMock(), gcode_file=MagicMock(), subtask_name=1)) == set()

    def test_job_name_covers_firmware_that_reports_only_the_plate_gcode(self):
        in_use = _sd_files_in_use(_state(gcode_file="/data/Metadata/plate_1.gcode", subtask_name="My Cube"))
        assert {"my cube.3mf", "my_cube.3mf", "my cube.gcode", "my_cube.gcode"} <= in_use


async def _run_cleanup(state, *, archive_filename, subtask_name):
    printer = SimpleNamespace(id=1, name="A1", ip_address="192.0.2.10", access_code="12345678", model="A1")
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=printer)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=archive_filename)),
        ]
    )
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=db)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    manager = MagicMock()
    manager.get_status = MagicMock(return_value=state)
    delete = AsyncMock(return_value=DeleteResult.DELETED)
    with (
        patch("backend.app.main.async_session", MagicMock(return_value=session_ctx)),
        patch("backend.app.main.printer_manager", manager),
        patch("backend.app.services.bambu_ftp.delete_file_async", delete),
    ):
        await _cleanup_sd_card_after_print(1, subtask_name, 342, logging.getLogger("test"))
    return [call.args[2] for call in delete.await_args_list]


@pytest.mark.asyncio
async def test_reprint_from_the_printer_keeps_its_file():
    """The reporter's case: the A1 reprints the file from its screen, startup
    reconciliation closes the old archive and runs the cleanup."""
    state = _state("RUNNING", "A1-Siraya_Tech_TPU-64D-18JAN26.3mf", "A1-Siraya Tech TPU-64D-18JAN26")
    deleted = await _run_cleanup(
        state,
        archive_filename="A1-Siraya Tech TPU-64D-18JAN26.gcode.3mf",
        subtask_name="A1-Siraya Tech TPU-64D-18JAN26",
    )
    assert "/A1-Siraya_Tech_TPU-64D-18JAN26.3mf" not in deleted


@pytest.mark.asyncio
async def test_a_finished_print_is_still_deleted():
    state = _state("FINISH", "cube.3mf", "cube")
    deleted = await _run_cleanup(state, archive_filename="cube.gcode.3mf", subtask_name="cube")
    assert "/cube.3mf" in deleted


@pytest.mark.asyncio
async def test_a_different_running_job_does_not_protect_the_finished_file():
    state = _state("RUNNING", "other.3mf", "other")
    deleted = await _run_cleanup(state, archive_filename="cube.gcode.3mf", subtask_name="cube")
    assert "/cube.3mf" in deleted
