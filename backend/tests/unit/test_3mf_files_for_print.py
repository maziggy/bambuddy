"""Which file of a printer directory listing is the starting print's 3MF (SN-C4).

When no direct download path works, print start lists the printer's
directories and takes the file named for the print. A file that only contains
the name is another print's ("Cube_large" is not "Cube").
"""

import pytest

from backend.app.main import _3mf_files_for_print


@pytest.mark.parametrize(
    ("listed", "term", "picked"),
    [
        (["Cube_large.gcode.3mf", "Cube.gcode.3mf"], "cube", ["Cube.gcode.3mf"]),
        (["Cube_large.gcode.3mf", "Cube.3mf"], "cube", ["Cube.3mf"]),
        # The printer's underscores for spaces
        (["Cable Clip.gcode.3mf"], "cable_clip", ["Cable Clip.gcode.3mf"]),
        # No file of its own name: one that contains it is taken only alone
        (["Cube_v2.gcode.3mf"], "cube", ["Cube_v2.gcode.3mf"]),
        (["Cube_large.gcode.3mf", "Cube_small.gcode.3mf"], "cube", []),
        (["Cube.gcode", "notes.txt"], "cube", []),
        # Across folders: the print's own file in a later folder wins
        (["/cache/Cube_large.3mf", "/Cube.3mf"], "cube", ["/Cube.3mf"]),
        (["/cache/Cube.3mf", "/model/Cube.gcode.3mf"], "cube", ["/cache/Cube.3mf", "/model/Cube.gcode.3mf"]),
        # A folder named like the print does not make its files the print's
        (["/cube/other.3mf"], "cube", []),
    ],
)
def test_the_print_s_own_file(listed, term, picked):
    assert _3mf_files_for_print(listed, term) == picked


@pytest.mark.asyncio
@pytest.mark.integration
async def test_print_start_downloads_only_the_print_s_own_file(test_engine, printer_factory):
    """Direct paths fail, so print start searches the printer's directories."""
    from unittest.mock import AsyncMock, patch

    from backend.tests.integration.test_external_print_confirmation_1898 import SUBTASK, _drive_print_start

    printer = await printer_factory()
    listing = [{"name": f"{SUBTASK}_large.gcode.3mf"}, {"name": f"{SUBTASK}.gcode.3mf"}]
    listed = patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=listing))

    mocks = await _drive_print_start(test_engine, printer, download_ok=False, extra_patches=[listed])

    requested = [c.args[2] for c in mocks["download_file_async"].await_args_list]
    assert "/cache/" + listing[1]["name"] in requested, "the directory search did not try the print's file"
    assert not [path for path in requested if "_large" in path], requested


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_print_s_own_file_in_a_later_folder_wins(test_engine, printer_factory):
    from unittest.mock import AsyncMock, patch

    from backend.tests.integration.test_external_print_confirmation_1898 import SUBTASK, _drive_print_start

    printer = await printer_factory()

    async def listing(_ip, _code, folder, **_kwargs):
        return {"/cache": [{"name": f"{SUBTASK}_large.gcode.3mf"}], "/": [{"name": f"{SUBTASK}.3mf"}]}.get(folder, [])

    listed = patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(side_effect=listing))

    mocks = await _drive_print_start(test_engine, printer, download_ok=False, extra_patches=[listed])

    requested = [c.args[2] for c in mocks["download_file_async"].await_args_list]
    assert not [path for path in requested if "_large" in path], requested
