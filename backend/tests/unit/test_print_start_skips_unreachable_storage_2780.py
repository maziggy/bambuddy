"""The 3MF sweep is skipped when it cannot succeed, and the card says why (#2780).

The unit tests around ``print_storage`` pin the decision; this pins that
``on_print_start`` actually acts on it. Two things have to hold and neither is
visible from the helper alone:

* No FTP is attempted. The sweep is six filename variants across five
  directories with up to four retries, and on the reporter's P2S every one of
  those failed -- 1813 failures in a day against a printer that, on the leading
  theory, was refusing precisely because of the connection volume.
* The fallback archive records *which* reason applied. Without it the archives
  banner falls back to its original wording, which tells the user to switch on
  a setting that in this case is already on and would not have helped.

The regression case matters as much: a printer whose file service is fine must
still sweep exactly as before.
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


@pytest.fixture(autouse=True)
def _clear_dicts():
    for d in (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
        _timelapse_baselines,
    ):
        d.clear()
    yield
    for d in (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
        _timelapse_baselines,
    ):
        d.clear()


def _printer():
    printer = MagicMock()
    printer.id = 1
    printer.auto_archive = True
    printer.external_camera_enabled = False
    printer.external_camera_url = None
    # Spell this one out: every unset MagicMock attribute is truthy, so leaving
    # it implicit runs the plate-detection camera grab and each test waits out
    # its 10s timeout against a printer that does not exist.
    printer.plate_detection_enabled = False
    printer.name = "H2C"
    printer.model = "H2C"
    printer.ip_address = "192.168.1.210"
    printer.access_code = "12345678"
    return printer


def _state(current_project_url, sdcard=True, sdcard_reported=True):
    return MagicMock(
        current_project_url=current_project_url,
        sdcard=sdcard,
        sdcard_reported=sdcard_reported,
    )


async def _run_print_start(state, added):
    """Drive on_print_start for a print with no matching archive, capturing
    whatever rows it adds and whether it reached the FTP layer.
    """
    printer = _printer()

    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from printers" in sql or "from printer " in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=printer),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[printer]))),
            )
        # No existing / expected archive anywhere: the fallback path is the
        # one under test.
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_router)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock(side_effect=added.append)

    download = AsyncMock(return_value=False)

    with (
        patch("backend.app.main.async_session") as session_maker,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.download_file_async", new=download),
        patch("backend.app.main.with_ftp_retry", new=AsyncMock(return_value=False)) as retry,
        patch("backend.app.main.get_cached_3mf", return_value=None),
        # Imported inside the function, so it has to be patched at its source
        # or the directory-search fallback opens real sockets to a printer
        # that is not there and the test hangs on connect timeouts.
        patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=[])),
        patch("backend.app.main.ftps_handshake_blocked", return_value=False),
        patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30))),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
    ):
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        relay.on_print_start = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST2780"))

        from backend.app.main import on_print_start

        await on_print_start(
            1,
            {"filename": "/data/Metadata/plate_1.gcode", "subtask_name": "Halterung"},
        )

    return download, retry


def _fallback(added):
    """The archive row the fallback path created, if any."""
    for row in added:
        extra = getattr(row, "extra_data", None)
        if isinstance(extra, dict) and extra.get("no_3mf_available"):
            return row
    return None


@pytest.mark.asyncio
async def test_a_print_on_internal_storage_never_touches_ftp():
    added = []

    download, retry = await _run_print_start(_state("brtc://emmc/Halterung.gcode.3mf"), added)

    download.assert_not_called()
    retry.assert_not_called()

    archive = _fallback(added)
    assert archive is not None, "the print still has to be archived, just without slice data"
    assert archive.extra_data["no_3mf_reason"] == "internal_storage"


@pytest.mark.asyncio
async def test_an_empty_slot_never_touches_ftp():
    added = []

    download, retry = await _run_print_start(_state(None, sdcard=False, sdcard_reported=True), added)

    download.assert_not_called()
    retry.assert_not_called()
    assert _fallback(added).extra_data["no_3mf_reason"] == "no_external_storage"


@pytest.mark.asyncio
async def test_a_print_on_external_storage_still_sweeps():
    """The regression guard: every install whose archives work today reaches
    the download exactly as it did before the gate existed."""
    added = []

    download, _ = await _run_print_start(_state("ftp://Halterung.gcode.3mf"), added)

    download.assert_called()


@pytest.mark.asyncio
async def test_a_printer_that_said_nothing_still_sweeps():
    """Silence is not evidence. A printer whose broker refuses the request
    topic never gives us a URL, and its firmware may never publish `sdcard`
    either -- that install must behave exactly as before."""
    added = []

    download, _ = await _run_print_start(_state(None, sdcard=False, sdcard_reported=False), added)

    download.assert_called()


@pytest.mark.asyncio
async def test_a_sweep_that_simply_found_nothing_records_no_reason():
    """The original cause -- the slicer left no file on a card that is present
    and working -- is still reported with the original wording, because that
    advice is right for it."""
    added = []

    await _run_print_start(_state("ftp://Halterung.gcode.3mf"), added)

    assert _fallback(added).extra_data["no_3mf_reason"] is None
