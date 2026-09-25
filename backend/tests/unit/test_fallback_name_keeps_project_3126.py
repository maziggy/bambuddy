"""Rejecting the wrong plate must not cost the archive its name (#3126).

The reporter's X2D was sent a print from Bambu Studio, which filed it on
internal eMMC (``"url": "brtc://emmc/..."``). FTPS cannot serve that, but the
bounded probe found a *same-named* file at the card's root -- an earlier slice
of the same project, plate 4, while the running print was plate 1. #1204's
guard caught the contradiction and refused the file, which is right: archiving
plate 4's thumbnail, filament and cost against this print is the swap #2957
removed.

What it then did with the name was not. The guard asks ``swap_plate_suffix``
for a corrected name and blanked ``subtask_name`` whenever it came back None --
but None covers two unrelated cases, and only one of them is a name that could
mislead. ``防雨防虫_模块化_排气口（50_75_80_100管可用）`` carries no
``- Plate N`` suffix at all, so it holds no stale plate number to be wrong
about; blanking it dropped the project name too and the row fell through to the
gcode_file path, titled ``plate_1``. #1204's own premise is consecutive plates
*of the same model*, so the project part of a lagging name is right either way.

Fixing that is only half of it, and the other half is why the title lives in its
own variable. The name is kept for *display*; every lookup still disowns it,
``_active_prints`` included. Key the row under a name the guard just watched
fetch the wrong plate and the cover endpoint -- which downloads that very name
for the running print's thumbnail -- hands the bytes to the recovery path, which
checks a candidate is a readable 3MF and never which plate it holds. The row
would be filled in with the file this branch had just deleted.

Pinned here: a name without a plate suffix survives the rejection intact as the
title, a name with a stale one still gets its number corrected, and the rejected
name keys nothing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    _timelapse_baselines,
)

pytestmark = pytest.mark.unit

DISPATCH = "/data/Metadata/plate_1.gcode"
# The reporter's own subtask_name. Non-ASCII and parenthesised, with no plate
# suffix anywhere in it -- exactly the shape that used to be thrown away.
PROJECT = "防雨防虫_模块化_排气口（50_75_80_100管可用）"


@pytest.fixture(autouse=True)
def _clear_dicts():
    dicts = (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
        _timelapse_baselines,
    )
    for d in dicts:
        d.clear()
    yield
    for d in dicts:
        d.clear()


def _printer():
    printer = MagicMock()
    printer.id = 1
    printer.auto_archive = True
    printer.external_camera_enabled = False
    printer.external_camera_url = None
    # Every unset MagicMock attribute is truthy, and leaving this one implicit
    # runs the plate-detection camera grab against a printer that is not there.
    printer.plate_detection_enabled = False
    printer.name = "X2D"
    printer.model = "X2D"
    printer.ip_address = "172.25.12.149"
    printer.access_code = "12345678"
    return printer


async def _run_print_start(subtask: str):
    """Drive on_print_start to the wrong-plate rejection and return the row.

    Every download succeeds and every 3MF peeks as plate 4, while the dispatch
    says plate 1 -- so the initial fetch is rejected and no re-download can
    satisfy the guard either, which is the reporter's sequence.
    """
    printer = _printer()

    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from printers" in sql or "from printer " in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=printer),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[printer]))),
            )
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    added: list = []
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_router)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock(side_effect=added.append)

    state = MagicMock(
        current_project_url=f"ftp://{subtask}.gcode.3mf",
        sdcard=True,
        sdcard_reported=True,
    )

    with (
        patch("backend.app.main.async_session") as session_maker,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.download_file_async", new=AsyncMock(return_value=True)),
        patch("backend.app.main.download_file_try_paths_async", new=AsyncMock(return_value=None)),
        patch("backend.app.main.get_cached_3mf", return_value=None),
        patch("backend.app.main.cache_3mf_download"),
        # Plate 4 on the card, plate 1 on the printer -- the mismatch itself.
        patch("backend.app.main.peek_plate_index_in_3mf", return_value=4),
        # Imported inside the function, so patching it anywhere else lets the
        # directory walk open real sockets and the test hangs on connect.
        patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=[])),
        patch("backend.app.main.ftps_handshake_blocked", return_value=False),
        patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30))),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
        # Real, it would spawn a task that outlives the test by a minute.
        patch("backend.app.main._schedule_fallback_3mf_retry", new=MagicMock()),
    ):
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        # Awaited between creating the fallback row and the rest of the
        # handler: a plain MagicMock raises, and the handler swallows it.
        ws.send_archive_created = AsyncMock()
        relay.on_print_start = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST3126"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": DISPATCH, "subtask_name": subtask})

    # Snapshot before the autouse fixture clears it in teardown.
    keys = {name for (_pid, name) in _active_prints}
    for row in added:
        extra = getattr(row, "extra_data", None)
        if isinstance(extra, dict) and extra.get("no_3mf_available"):
            return row, keys
    return None, keys


class TestANameWithNoPlateSuffixSurvives:
    @pytest.mark.asyncio
    async def test_the_reported_case_keeps_the_project_name(self):
        """The regression. This row used to be titled ``plate_1``."""
        row, _keys = await _run_print_start(PROJECT)

        assert row is not None
        assert row.print_name == PROJECT

    @pytest.mark.asyncio
    async def test_the_original_subtask_is_still_recorded(self):
        """Nothing reads this field today -- it is there for support, and a
        row that records the dispatch path under both its name and its
        subtask tells whoever reads the bundle nothing about the print."""
        row, _keys = await _run_print_start(PROJECT)

        assert row.extra_data["original_subtask"] == PROJECT

    @pytest.mark.asyncio
    async def test_an_ascii_name_too(self):
        """Nothing here is about the encoding -- any single-plate project name
        reaches the same branch."""
        row, _keys = await _run_print_start("Fan_Shroud")

        assert row.print_name == "Fan_Shroud"


class TestAStalePlateSuffixIsStillCorrected:
    """#1204's actual fix, which the change above must not undo."""

    @pytest.mark.asyncio
    async def test_the_spaced_form_gets_the_running_plate(self):
        row, _keys = await _run_print_start("Fan_Shroud - Plate 4")

        assert row.print_name == "Fan_Shroud - Plate 1"

    @pytest.mark.asyncio
    async def test_the_underscored_form_too(self):
        row, _keys = await _run_print_start("Fan_Shroud_plate_4")

        assert row.print_name == "Fan_Shroud_plate_1"


class TestTheDisownedNameStillFindsNoFiles:
    """The other half, and the reason the name is kept in its own variable.

    A name the guard just watched fetch another plate's 3MF must not key
    ``_active_prints``. The cover endpoint downloads that same name for the
    running print's thumbnail and offers the bytes to
    ``try_recover_fallback_archive``, which matches on those keys and hands
    whatever it gets to ``_recover_fallback_archive`` -- and that checks a
    candidate is a readable 3MF, never which plate it holds. Key the row under
    the rejected name and the cover endpoint fills it in with the exact file
    this branch just deleted, which is #2957's swap coming back in through a
    different door.
    """

    @pytest.mark.asyncio
    async def test_the_rejected_name_is_not_registered(self):
        row, keys = await _run_print_start(PROJECT)

        assert row.print_name == PROJECT, "the title is the whole point of the fix"
        assert PROJECT not in keys
        assert f"{PROJECT}.3mf" not in keys

    @pytest.mark.asyncio
    async def test_the_dispatch_path_still_is(self):
        """Disowning the subtask name must not leave the archive unfindable at
        print completion -- the gcode_file key is what matches it there."""
        _row, keys = await _run_print_start(PROJECT)

        assert DISPATCH in keys

    @pytest.mark.asyncio
    async def test_a_corrected_name_is_registered(self):
        """#1204's case is different: the swapped name points at the plate that
        really is running, so a file found under it is the right file."""
        _row, keys = await _run_print_start("Fan_Shroud - Plate 4")

        assert "Fan_Shroud - Plate 1" in keys
        assert "Fan_Shroud - Plate 4" not in keys
