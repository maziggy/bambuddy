"""The printer's own calibration runs leave no archive and send no notification.

Auto pressure-advance calibration -- the K-profile line the printer lays down
before a print when flow dynamics calibration is on -- reports over MQTT through
the same print-start event a real print uses, as the subtask name
``auto_pa_line_calib_mode`` with no ``/usr/`` path attached. The only guard
Bambuddy had tested ``filename.startswith("/usr/")``, so the calibration sailed
past it, found no 3MF anywhere on the printer (there is none to find), and left
a no-3MF archive named after itself in the user's history.

The same name is already known to the completion guard: #2829's capture of
queue item 649 has ``auto_pa_line_calib_mode`` arriving as the subtask name of a
completion that had to be refused against a running job.

The manual flow-dynamics run reaches Bambuddy the same way under two further
names -- ``pa_line_calib_mode`` and ``pa_pattern_calib_mode``, the line and the
pattern shape, both without the ``auto_`` prefix -- so the automatic entry never
covered either of them.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.utils.print_jobs import is_internal_printer_job


class TestTheCalibrationIsRecognised:
    def test_the_pressure_advance_line_by_subtask_name(self):
        """How it actually arrives: a bare subtask name, no filename at all."""
        assert is_internal_printer_job("", "auto_pa_line_calib_mode")

    def test_the_pressure_advance_line_by_filename(self):
        """Both fields are tested, because which one carries it is not fixed."""
        assert is_internal_printer_job("auto_pa_line_calib_mode", None)

    def test_the_manual_pressure_advance_pattern_by_subtask_name(self):
        """The same calibration run by hand rather than before a print. It
        prints a pattern where the automatic one prints a line, and carries its
        own name with no ``auto_`` prefix -- so the automatic entry never
        covered it and it left the same no-3MF archive."""
        assert is_internal_printer_job("", "pa_pattern_calib_mode")

    def test_the_manual_pressure_advance_pattern_by_filename(self):
        assert is_internal_printer_job("pa_pattern_calib_mode", None)

    def test_the_manual_pressure_advance_line_by_subtask_name(self):
        """Manual flow dynamics has a line shape as well as a pattern one, and
        it reports as ``pa_line_calib_mode`` -- the automatic name without its
        ``auto_`` prefix, which is close enough to the automatic entry to look
        covered and is not."""
        assert is_internal_printer_job("", "pa_line_calib_mode")

    def test_the_manual_pressure_advance_line_by_filename(self):
        assert is_internal_printer_job("pa_line_calib_mode", None)

    def test_the_levelling_run_by_its_system_path(self):
        assert is_internal_printer_job("/usr/etc/print/auto_cali_for_user.gcode", "auto_cali_for_user")

    def test_the_levelling_run_by_name_alone(self):
        """The /usr/ path is not guaranteed, so the name is listed too."""
        assert is_internal_printer_job(None, "auto_cali_for_user")

    @pytest.mark.parametrize(
        "reported",
        [
            "auto_pa_line_calib_mode",
            "auto_pa_line_calib_mode.gcode",
            "auto_pa_line_calib_mode.3mf",
            "auto_pa_line_calib_mode.gcode.3mf",
            "AUTO_PA_LINE_CALIB_MODE",
            "/data/auto_pa_line_calib_mode.gcode.3mf",
            "pa_pattern_calib_mode",
            "pa_pattern_calib_mode.gcode.3mf",
            "PA_Pattern_Calib_Mode",
            "/data/pa_pattern_calib_mode.gcode",
            "pa_line_calib_mode",
            "pa_line_calib_mode.gcode.3mf",
            "PA_Line_Calib_Mode",
            "/data/pa_line_calib_mode.gcode",
        ],
    )
    def test_however_the_name_is_dressed_up(self, reported):
        """Path, suffix and case all vary between the fields and firmwares."""
        assert is_internal_printer_job(reported, None)

    def test_anything_under_usr_counts(self):
        """Nothing a user can print lives on the read-only system partition."""
        assert is_internal_printer_job("/usr/bin/firmware_test.gcode", "test")


class TestItLeavesRealPrintsAlone:
    """The failure that matters: swallowing somebody's actual print."""

    def test_an_ordinary_print(self):
        assert not is_internal_printer_job("Benchy.gcode.3mf", "Benchy")

    def test_nothing_reported_at_all(self):
        assert not is_internal_printer_job(None, None)
        assert not is_internal_printer_job("", "")

    @pytest.mark.parametrize(
        "reported",
        [
            "auto_pa_line_calib_mode_v2.3mf",
            "my_auto_pa_line_calib_mode.3mf",
            "auto_cali_for_user_test.gcode.3mf",
            "pa_pattern_calib_mode_v2.3mf",
            "my_pa_pattern_calib_mode.3mf",
            "pa_line_calib_mode_v2.3mf",
            "my_pa_line_calib_mode.3mf",
            # The stem the four calibration names share, which is why the set
            # holds literals instead of a ``pa_`` rule.
            "pa_bracket.3mf",
        ],
    )
    def test_a_users_file_that_merely_contains_the_name(self, reported):
        """Exact match after normalising, so no prefix or substring rule can
        eat a file somebody deliberately named after the calibration."""
        assert not is_internal_printer_job(reported, None)

    def test_a_calibration_cube(self):
        """The obvious false positive for any rule built on the word 'calib'."""
        assert not is_internal_printer_job("Calibration_Cube.gcode.3mf", "Calibration Cube")


def _mocked_print_start():
    """Patch set for driving on_print_start without a printer or database."""
    return (
        patch("backend.app.main.async_session"),
        patch("backend.app.main.notification_service"),
        patch("backend.app.main.smart_plug_manager"),
        patch("backend.app.main.ws_manager"),
        patch("backend.app.main.printer_manager"),
        patch("backend.app.main.mqtt_relay"),
    )


class TestPrintStartSkipsTheCalibration:
    @pytest.mark.asyncio
    async def test_no_archive_and_no_notification(self, capture_logs):
        sess, notif, plug, ws, pm, relay = _mocked_print_start()
        with sess as mock_session_maker, notif as mock_notif, plug as mock_plug, ws as mock_ws, pm as mock_pm, relay:
            mock_notif.on_print_start = AsyncMock()
            mock_plug.on_print_start = AsyncMock()
            mock_ws.send_print_start = AsyncMock()
            mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

            mock_printer = MagicMock()
            mock_printer.auto_archive = True
            mock_printer.id = 1

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_printer))
            )
            mock_session_maker.return_value = mock_session

            with patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock) as mock_notify:
                from backend.app.main import on_print_start

                # No filename: exactly what the printer reports for this run,
                # and the reason the old /usr/ prefix test never fired.
                await on_print_start(1, {"filename": "", "subtask_name": "auto_pa_line_calib_mode"})

                mock_notify.assert_not_called()

        skipped = [r for r in capture_logs.records if "internal printer job" in str(r.message)]
        assert skipped, "Should log that the calibration run was skipped"


class TestPrintCompleteStaysQuiet:
    @pytest.mark.asyncio
    async def test_no_orphan_notification_when_the_calibration_finishes(self):
        """With no archive to close, the completion would otherwise fall into
        the no-archive notification path -- which attributes an unmatched
        completion to any queue item this printer finished in the last five
        minutes. For a calibration running alongside a real print that means
        telling its owner their print is done, early and twice.
        """
        with (
            patch("backend.app.main.async_session") as mock_session_maker,
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.printer_manager") as mock_pm,
            patch("backend.app.main.mqtt_relay") as mock_relay,
            patch("backend.app.main.spawn_background_task") as mock_spawn,
            patch("backend.app.main.clear_3mf_cache"),
        ):
            mock_ws.send_print_complete = AsyncMock()
            mock_relay.on_print_complete = AsyncMock()
            mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))
            mock_pm.get_current_print_user = MagicMock(return_value=None)
            mock_pm.clear_current_print_user = MagicMock()
            mock_pm.set_awaiting_plate_clear = MagicMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None), scalars=MagicMock())
            )
            mock_session_maker.return_value = mock_session

            from backend.app.main import on_print_complete

            await on_print_complete(
                1,
                {"filename": "", "subtask_name": "auto_pa_line_calib_mode", "status": "completed"},
            )

            spawned = [c for c in mock_spawn.call_args_list if "notify-no-archive" in str(c)]
            assert not spawned, "No completion notification should be spawned for a calibration run"

    @pytest.mark.asyncio
    async def test_a_real_orphan_print_still_notifies(self):
        """The no-archive path exists for prints started outside Bambuddy. The
        guard must not take those down with it.
        """
        with (
            patch("backend.app.main.async_session") as mock_session_maker,
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.printer_manager") as mock_pm,
            patch("backend.app.main.mqtt_relay") as mock_relay,
            patch("backend.app.main.spawn_background_task") as mock_spawn,
            patch("backend.app.main.clear_3mf_cache"),
        ):
            mock_ws.send_print_complete = AsyncMock()
            mock_relay.on_print_complete = AsyncMock()
            mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))
            mock_pm.get_current_print_user = MagicMock(return_value=None)
            mock_pm.clear_current_print_user = MagicMock()
            mock_pm.set_awaiting_plate_clear = MagicMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None), scalars=MagicMock())
            )
            mock_session_maker.return_value = mock_session

            from backend.app.main import on_print_complete

            await on_print_complete(
                1,
                {"filename": "", "subtask_name": "Benchy", "status": "completed"},
            )

            spawned = [c for c in mock_spawn.call_args_list if "notify-no-archive" in str(c)]
            assert spawned, "An unmatched real print must still notify"
