"""An eMMC dispatch is not proof the file is out of reach (#2856).

``print_storage`` reads the ``project_file`` URL: ``brtc://emmc/<name>`` says
the printer put the sliced file on internal storage, and #2780 turned that into
"skip the FTPS lookup entirely". On an H2D with a card in the slot that is
wrong. The reporter's log has ``"url": "brtc://emmc/test.gcode.3mf"`` and, a
second later, ``Downloaded: /cache/test.gcode.3mf`` -- every one of his prints
back to 08-12, 19 MB included, until the skip landed and two days of archives
came out as name-only fallbacks.

So the URL earns a *bounded probe* rather than a skip. It names the exact file,
which is one connection walking five directories instead of the sweep's ~110,
and the printer answers the question instead of a guess about its model. These
tests pin both halves: the probe runs and its hit is archived normally, and a
miss still ends in #2780's cheap fallback with its reason intact.
"""

import logging
from pathlib import Path
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
from backend.app.services.print_storage import (
    ftp_probe_paths,
    print_file_reachable_over_ftp,
    probe_filename_from_url,
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


class TestProbeFilename:
    """What the probe asks for. The dispatch's name is the authoritative one --
    the sweep's guesses are built from ``subtask_name``, which is normalized,
    truncated and occasionally a plate behind."""

    def test_the_reported_case(self):
        assert probe_filename_from_url("brtc://emmc/test.gcode.3mf") == "test.gcode.3mf"

    def test_a_name_with_the_punctuation_users_actually_use(self):
        """Straight from the reporter's log -- ampersands, parentheses, plus."""
        url = "brtc://emmc/H2D_&_H2S_poop_chute+4_buckets_(no_magnets_&_glue).gcode.3mf"
        assert probe_filename_from_url(url) == "H2D_&_H2S_poop_chute+4_buckets_(no_magnets_&_glue).gcode.3mf"

    def test_a_non_ascii_name(self):
        assert probe_filename_from_url("brtc://emmc/小船.gcode.3mf") == "小船.gcode.3mf"

    def test_an_internal_file_path_keeps_only_the_name(self):
        """The model cache is not reachable at that path, but the same file may
        well be sitting in /cache under its bare name."""
        assert probe_filename_from_url("file:///userdata/model/history/Cube.gcode.3mf") == "Cube.gcode.3mf"

    @pytest.mark.parametrize(
        "url",
        [
            None,
            "",
            "Benchy.gcode.3mf",  # no scheme -- not a dispatch URL at all
            "brtc://emmc/",
            "brtc://emmc/Benchy.gcode",  # a gcode job has no 3MF at any path
            "brtc://emmc/.",
            "brtc://emmc/..",
            12345,
        ],
    )
    def test_nothing_to_probe_with(self, url):
        assert probe_filename_from_url(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "brtc://emmc/..\\..\\evil.3mf",  # backslash is a separator on the host
            "brtc://emmc/sub\\dir\\Cube.3mf",
            "brtc://emmc/Cube\n.3mf",  # control characters
            "brtc://emmc/" + "C" * 256 + ".3mf",
        ],
    )
    def test_a_name_that_could_steer_a_path_is_refused_not_cleaned(self, url):
        """The name comes off the wire and becomes both a remote path and a
        local temp filename, so a name that could point at either somewhere
        else is declined outright -- the print falls back to the archive it
        would have got anyway."""
        assert probe_filename_from_url(url) is None


class TestProbePaths:
    def test_root_first_then_cache(self):
        """Order is the sweep's own: root is where A1/P1 uploads land (#972),
        /cache is where the H2D keeps its copy (#2856)."""
        assert ftp_probe_paths("Cube.gcode.3mf")[:2] == ["/Cube.gcode.3mf", "/cache/Cube.gcode.3mf"]

    def test_one_filename_five_paths(self):
        assert len(ftp_probe_paths("Cube.gcode.3mf")) == 5


class TestFindRemoteFile:
    """The lookup the connection diagnostic runs. It must not transfer the
    file -- the thing it is asking about can be tens of megabytes and the
    answer is a yes or a no."""

    @staticmethod
    def _client(listings):
        client = MagicMock()
        client.connect.return_value = True
        client.list_files.side_effect = lambda directory: [
            {"name": name, "is_directory": False} for name in listings.get(directory, [])
        ]
        return client

    @pytest.mark.asyncio
    async def test_finds_the_file_in_cache_and_stops_there(self):
        client = self._client({"/": ["other.3mf"], "/cache": ["test.gcode.3mf"]})

        with patch("backend.app.services.bambu_ftp.BambuFTPClient", return_value=client):
            from backend.app.services.bambu_ftp import find_remote_file_async

            found = await find_remote_file_async("1.2.3.4", "code", ftp_probe_paths("test.gcode.3mf"))

        assert found == "/cache/test.gcode.3mf"
        # One connection, and the directories after the hit are never listed.
        client.connect.assert_called_once()
        assert [call.args[0] for call in client.list_files.call_args_list] == ["/", "/cache"]
        client.download_to_file.assert_not_called()
        client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_directory_is_listed_once_however_many_candidates_share_it(self):
        client = self._client({})

        with patch("backend.app.services.bambu_ftp.BambuFTPClient", return_value=client):
            from backend.app.services.bambu_ftp import find_remote_file_async

            found = await find_remote_file_async("1.2.3.4", "code", ["/a.3mf", "/b.3mf", "/cache/a.3mf"])

        assert found is None
        assert [call.args[0] for call in client.list_files.call_args_list] == ["/", "/cache"]

    @pytest.mark.asyncio
    async def test_a_directory_of_the_same_name_is_not_the_file(self):
        client = MagicMock()
        client.connect.return_value = True
        client.list_files.return_value = [{"name": "test.gcode.3mf", "is_directory": True}]

        with patch("backend.app.services.bambu_ftp.BambuFTPClient", return_value=client):
            from backend.app.services.bambu_ftp import find_remote_file_async

            assert await find_remote_file_async("1.2.3.4", "code", ftp_probe_paths("test.gcode.3mf")) is None

    @pytest.mark.asyncio
    async def test_a_refused_connection_is_not_an_answer(self):
        client = MagicMock()
        client.connect.return_value = False

        with patch("backend.app.services.bambu_ftp.BambuFTPClient", return_value=client):
            from backend.app.services.bambu_ftp import find_remote_file_async

            assert await find_remote_file_async("1.2.3.4", "code", ftp_probe_paths("test.gcode.3mf")) is None

        client.list_files.assert_not_called()


class TestVerdictCarriesTheName:
    def test_an_internal_dispatch_carries_a_probe_name(self):
        state = MagicMock(current_project_url="brtc://emmc/test.gcode.3mf", sdcard=True, sdcard_reported=True)
        verdict = print_file_reachable_over_ftp(state)

        assert verdict.reachable is False
        assert verdict.reason == "internal_storage"
        assert verdict.probe_filename == "test.gcode.3mf"

    def test_an_external_dispatch_needs_no_probe(self):
        """It sweeps as it always did; a probe name here would only invite a
        caller to shorten a search that is already working."""
        state = MagicMock(current_project_url="ftp://test.gcode.3mf", sdcard=True, sdcard_reported=True)
        verdict = print_file_reachable_over_ftp(state)

        assert verdict.reachable is True
        assert verdict.probe_filename is None

    def test_an_empty_slot_has_nothing_to_probe(self):
        """No URL and no card: there is no name, and no storage to look on."""
        state = MagicMock(current_project_url=None, sdcard=False, sdcard_reported=True)
        verdict = print_file_reachable_over_ftp(state)

        assert verdict.reason == "no_external_storage"
        assert verdict.probe_filename is None

    def test_an_empty_slot_is_not_probed_even_with_a_name(self):
        """#2780's H2C ran for three weeks with the toggle on and nothing in
        the slot. There is no card for a copy to be on, so the name is not
        worth a connection -- the printer has already answered."""
        state = MagicMock(current_project_url="brtc://emmc/test.gcode.3mf", sdcard=False, sdcard_reported=True)
        verdict = print_file_reachable_over_ftp(state)

        assert verdict.reason == "internal_storage"
        assert verdict.probe_filename is None

    def test_a_printer_that_never_mentions_its_card_is_still_probed(self):
        """`sdcard` defaults to False, so acting on the default would drop the
        probe for every printer that simply does not publish the field."""
        state = MagicMock(current_project_url="brtc://emmc/test.gcode.3mf", sdcard=False, sdcard_reported=False)

        assert print_file_reachable_over_ftp(state).probe_filename == "test.gcode.3mf"


def _printer():
    printer = MagicMock()
    printer.id = 1
    printer.auto_archive = True
    printer.external_camera_enabled = False
    printer.external_camera_url = None
    # Every unset MagicMock attribute is truthy, and a truthy one here runs the
    # plate-detection camera grab against a printer that does not exist.
    printer.plate_detection_enabled = False
    printer.name = "H2D"
    printer.model = "H2D"
    printer.ip_address = "192.168.1.211"
    printer.access_code = "12345678"
    return printer


async def _run_print_start(url, *, probe_hit, added, handshake_blocked=False):
    """Drive on_print_start for an eMMC dispatch, returning the probe mock and
    the ArchiveService the success path would have used.

    ``probe_hit`` is the path the probe serves the file from, or None for a
    miss — the download helper returns the winning path rather than a flag so
    the hit can be logged by directory (#1820)."""
    printer = _printer()
    state = MagicMock(current_project_url=url, sdcard=True, sdcard_reported=True)

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

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_router)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock(side_effect=added.append)

    probe = AsyncMock(return_value=probe_hit)
    archive_service = MagicMock()
    archive_service.archive_print = AsyncMock(return_value=MagicMock(id=20, print_name="test", status="printing"))

    with (
        patch("backend.app.main.async_session") as session_maker,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.download_file_try_paths_async", new=probe),
        patch("backend.app.main.download_file_async", new=AsyncMock(return_value=False)),
        patch("backend.app.main.with_ftp_retry", new=AsyncMock(return_value=False)),
        patch("backend.app.main.get_cached_3mf", return_value=None),
        patch("backend.app.main.cache_3mf_download") as cache,
        patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=[])),
        patch("backend.app.main.ftps_handshake_blocked", return_value=handshake_blocked),
        patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30))),
        patch("backend.app.main.ArchiveService", return_value=archive_service),
        patch("backend.app.main.peek_plate_index_in_3mf", return_value=None),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
    ):
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_created = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        relay.on_print_start = AsyncMock()
        relay.on_archive_created = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_client = MagicMock(return_value=None)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST2856"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "/data/Metadata/plate_1.gcode", "subtask_name": "test"})

    return probe, archive_service, cache


def _fallback(added):
    for row in added:
        extra = getattr(row, "extra_data", None)
        if isinstance(extra, dict) and extra.get("no_3mf_available"):
            return row
    return None


class TestPrintStart:
    @pytest.mark.asyncio
    async def test_a_file_the_printer_serves_anyway_is_archived_in_full(self):
        """The reported regression, end to end: eMMC dispatch, file present on
        the card, and the archive gets the real 3MF instead of a name."""
        added = []

        probe, service, cache = await _run_print_start(
            "brtc://emmc/test.gcode.3mf", probe_hit="/cache/test.gcode.3mf", added=added
        )

        probe.assert_awaited_once()
        assert probe.await_args.args[2] == ftp_probe_paths("test.gcode.3mf")
        service.archive_print.assert_awaited_once()
        assert Path(service.archive_print.await_args.kwargs["source_file"]).name == "test.gcode.3mf"
        assert _fallback(added) is None, "a fallback archive here is the bug"

    @pytest.mark.asyncio
    async def test_the_hit_names_the_directory_that_served_it(self, caplog):
        """#1820: a printer that keeps uploads for weeks can serve a same-named
        copy of an earlier slice. Logging only the filename made that mismatch
        invisible; the directory is what makes it diagnosable."""
        added = []

        with caplog.at_level(logging.INFO, logger="backend.app.main"):
            await _run_print_start("brtc://emmc/test.gcode.3mf", probe_hit="/cache/test.gcode.3mf", added=added)

        found = [r.getMessage() for r in caplog.records if "even though the printer reported" in r.getMessage()]
        assert found, "the probe hit is not logged at all"
        assert "/cache/test.gcode.3mf" in found[0]

    @pytest.mark.asyncio
    async def test_the_probed_file_is_shared_with_the_cover_endpoint(self):
        """Same 3MF, one transfer. The cover endpoint runs seconds later while
        the frontend opens the card, and re-fetching 19 MB over the printer's
        single FTP socket is what produced #972's 425 storm."""
        added = []

        _probe, _service, cache = await _run_print_start(
            "brtc://emmc/test.gcode.3mf", probe_hit="/cache/test.gcode.3mf", added=added
        )

        cache.assert_called_once()
        assert cache.call_args.args[1] == "test.gcode.3mf"

    @pytest.mark.asyncio
    async def test_a_miss_still_ends_in_the_cheap_fallback(self):
        """#2780's printers are still out there: when the probe finds nothing,
        the reason has to survive to the archive card, which is what stops the
        banner telling an H2C owner to switch on a setting that is already on.
        """
        added = []

        probe, service, _cache = await _run_print_start("brtc://emmc/test.gcode.3mf", probe_hit=None, added=added)

        probe.assert_awaited_once()
        service.archive_print.assert_not_awaited()
        assert _fallback(added).extra_data["no_3mf_reason"] == "internal_storage"

    @pytest.mark.asyncio
    async def test_no_probe_while_the_file_service_is_in_cool_off(self):
        """The handshake is failing below the path level, so the probe would
        only re-run the failure that put the printer in cool-off (#2780)."""
        added = []

        probe, _service, _cache = await _run_print_start(
            "brtc://emmc/test.gcode.3mf", probe_hit="/cache/test.gcode.3mf", added=added, handshake_blocked=True
        )

        probe.assert_not_awaited()
        assert _fallback(added).extra_data["no_3mf_reason"] == "internal_storage"

    @pytest.mark.asyncio
    async def test_a_cool_off_on_an_emmc_job_schedules_no_retry(self):
        """#2957 added a retry for a cool-off give-up, because that one clears
        in minutes with the file still on the printer. This is not that: the
        file is on internal eMMC and will not appear at any FTPS path however
        long we wait, so the retry must not be scheduled here."""
        from backend.app.main import _fallback_3mf_retry_tasks

        added = []
        before = dict(_fallback_3mf_retry_tasks)

        with patch("backend.app.main._schedule_fallback_3mf_retry") as schedule:
            await _run_print_start("brtc://emmc/test.gcode.3mf", probe_hit=None, added=added, handshake_blocked=True)

        schedule.assert_not_called()
        assert _fallback_3mf_retry_tasks == before

    @pytest.mark.asyncio
    async def test_a_gcode_job_is_not_probed_for(self):
        """Nothing names a 3MF, so there is no name to ask about and the skip
        stays exactly as cheap as #2780 made it."""
        added = []

        probe, _service, _cache = await _run_print_start(
            "brtc://emmc/plate_1.gcode", probe_hit="/cache/test.gcode.3mf", added=added
        )

        probe.assert_not_awaited()
        assert _fallback(added) is not None
