"""Time to the next slicer pause, and the heads-up before it (#2984)."""

import asyncio
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.services import pause_eta
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.utils.threemf_tools import (
    PausePoint,
    extract_pause_points_from_3mf,
    parse_gcode_pause_points,
)

# The shape Bambu Studio writes: the layer change (M73 L), then the pause,
# with the slicer's time left (M73 P R) along the way.
GCODE = """; HEADER_BLOCK_START
M73 P0 R120
; layer num/total_layer_count: 1/30
M73 L1
M991 S0 P0 ;notify layer change
G1 X10 Y10 E1.2
M73 P10 R108
; layer num/total_layer_count: 2/30
M73 L2
G1 X20 Y10 E1.0
M73 P35 R78
; layer num/total_layer_count: 3/30
M73 L3
M991 S0 P2 ;notify layer change
;PAUSE_PRINTING
M400 U1
G1 X30 Y10 E1.0
M73 P60 R48
M73 L4
M400 ; wait for moves, not a pause
M73 P80 R24
M73 L5
;PAUSE_PRINTING
M400 U1
M400 U1 ; a second pause on the same layer
G1 X40 E1.0
"""


def _three_mf(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestParse:
    def test_each_pause_has_its_layer_and_the_time_left_there(self):
        assert parse_gcode_pause_points(GCODE) == [
            PausePoint(layer=3, remaining_minutes=78),
            PausePoint(layer=5, remaining_minutes=24),
        ]

    def test_a_plain_m400_is_not_a_pause(self):
        assert parse_gcode_pause_points("M73 L2\nM73 P10 R50\nM400\nM400 S1\n") == []

    def test_no_pauses_in_a_plain_print(self):
        assert parse_gcode_pause_points("M73 P0 R10\nM73 L1\nG1 X1 E1\n") == []

    def test_time_left_is_unknown_without_m73_r(self):
        # A slicer that writes layers but no time left.
        assert parse_gcode_pause_points("M73 L7\nM400 U1\n") == [PausePoint(layer=7, remaining_minutes=None)]

    def test_lowercase_and_indented_commands(self):
        assert parse_gcode_pause_points("  m73 p5 r40\nm73 l2\n  m400 u1\n") == [
            PausePoint(layer=2, remaining_minutes=40)
        ]

    def test_a_malformed_value_keeps_the_last_good_one(self):
        assert parse_gcode_pause_points("M73 P5 R40\nM73 L2\nM73 Lx Rabc\nM400 U1\n") == [
            PausePoint(layer=2, remaining_minutes=40)
        ]

    def test_stealth_mode_time_is_not_taken_for_normal(self):
        # M73 Q/S is the silent-mode estimate; R stays the normal one.
        assert parse_gcode_pause_points("M73 P5 R40\nM73 Q5 S80\nM73 L2\nM400 U1\n") == [
            PausePoint(layer=2, remaining_minutes=40)
        ]


class TestExtract:
    def test_reads_the_plate_that_is_printing(self, tmp_path):
        path = tmp_path / "two.gcode.3mf"
        path.write_bytes(
            _three_mf(
                {
                    "Metadata/plate_2.gcode": "M73 P1 R9\nM73 L4\nM400 U1\n",
                    "Metadata/plate_1.gcode": GCODE,
                }
            )
        )
        assert [p.layer for p in extract_pause_points_from_3mf(path, 2)] == [4]
        assert [p.layer for p in extract_pause_points_from_3mf(path, 1)] == [3, 5]

    def test_not_another_plate_when_the_printed_one_is_missing(self, tmp_path):
        path = tmp_path / "two.gcode.3mf"
        path.write_bytes(_three_mf({"Metadata/plate_1.gcode": GCODE, "Metadata/plate_2.gcode": "M73 L4\nM400 U1\n"}))
        assert extract_pause_points_from_3mf(path, 3) == []

    def test_unreadable_file_has_no_pauses(self, tmp_path):
        path = tmp_path / "broken.3mf"
        path.write_bytes(b"not a zip")
        assert extract_pause_points_from_3mf(path, 1) == []


def _running(layer: int, remaining: int, points=None) -> PrinterState:
    state = PrinterState()
    state.state = "RUNNING"
    state.layer_num = layer
    state.remaining_time = remaining
    state.total_layers = 30
    state.subtask_name = "Wifi_QR"
    state.pause_points = points if points is not None else [PausePoint(3, 78), PausePoint(5, 24)]
    return state


class TestNextPause:
    def test_time_to_the_first_pause(self):
        assert pause_eta.next_pause(_running(layer=1, remaining=110)) == {"layer": 3, "minutes": 32}

    def test_moves_on_once_the_printer_is_on_the_pause_layer(self):
        # On the pause's layer the printer waits there, or has resumed past it.
        assert pause_eta.next_pause(_running(layer=3, remaining=78)) == {"layer": 5, "minutes": 54}

    def test_none_after_the_last_pause(self):
        assert pause_eta.next_pause(_running(layer=5, remaining=20)) is None

    def test_none_when_the_print_is_paused(self):
        state = _running(layer=1, remaining=110)
        state.state = "PAUSE"
        assert pause_eta.next_pause(state) is None

    def test_none_without_pauses(self):
        assert pause_eta.next_pause(_running(layer=1, remaining=110, points=[])) is None

    def test_layer_only_while_the_printer_reports_no_time_left(self):
        assert pause_eta.next_pause(_running(layer=0, remaining=0)) == {"layer": 3, "minutes": None}

    def test_layer_only_when_the_gcode_had_no_time(self):
        state = _running(layer=1, remaining=110, points=[PausePoint(3, None)])
        assert pause_eta.next_pause(state) == {"layer": 3, "minutes": None}

    def test_never_negative(self):
        # The printer a little ahead of the slicer's estimate.
        assert pause_eta.next_pause(_running(layer=2, remaining=70)) == {"layer": 3, "minutes": 0}

    def test_the_last_minute_before_a_pause(self):
        # The slicer writes time left in whole minutes: the last M73 R before
        # the pause can come a minute ahead of it.
        assert pause_eta.next_pause(_running(layer=2, remaining=78)) == {"layer": 3, "minutes": 0}

    @pytest.mark.parametrize("speed_level", [1, 3, 4])
    def test_layer_only_outside_standard_speed(self, speed_level):
        state = _running(layer=1, remaining=110)
        state.speed_level = speed_level
        assert pause_eta.next_pause(state) == {"layer": 3, "minutes": None}

    def test_standard_speed(self):
        state = _running(layer=1, remaining=110)
        state.speed_level = 2
        assert pause_eta.next_pause(state) == {"layer": 3, "minutes": 32}


class TestStatusPayloads:
    def test_websocket_status_carries_it(self):
        from backend.app.services.printer_manager import printer_state_to_dict

        state = _running(layer=1, remaining=110)
        state.connected = True
        assert printer_state_to_dict(state)["next_pause"] == {"layer": 3, "minutes": 32}

    def test_websocket_status_none_without_pauses(self):
        from backend.app.services.printer_manager import printer_state_to_dict

        state = _running(layer=1, remaining=110, points=[])
        state.connected = True
        assert printer_state_to_dict(state)["next_pause"] is None


class TestNewPrintClears:
    def test_the_layer_it_was_on_is_kept_for_a_resume(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")
        client.state.layer_num = 42
        client._previous_gcode_state = "PAUSE"
        client._process_message({"print": {"gcode_state": "RUNNING", "gcode_file": "same.3mf"}})
        assert client.layer_before_new_print == 42

    def test_pauses_and_warnings_of_the_last_print_are_dropped(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")
        client.state.pause_points = [PausePoint(3, 78)]
        client.state.pause_warned_layers = {3}
        client._previous_gcode_state = "IDLE"

        client._process_message({"print": {"gcode_state": "RUNNING", "gcode_file": "next.3mf"}})

        assert client.state.pause_points == []
        assert client.state.pause_warned_layers == set()


@pytest.fixture(autouse=True)
def _fresh_throttle():
    pause_eta._last_check.clear()
    yield
    pause_eta._last_check.clear()


class TestObserve:
    def test_spawns_a_check_when_a_pause_is_near(self):
        with patch.object(pause_eta, "spawn_background_task") as spawn:
            pause_eta.observe(1, _running(layer=2, remaining=85))
        spawn.assert_called_once()
        spawn.call_args.args[0].close()

    def test_nothing_while_the_pause_is_far(self):
        with patch.object(pause_eta, "spawn_background_task") as spawn:
            pause_eta.observe(1, _running(layer=1, remaining=78 + pause_eta.MAX_WARNING_MINUTES + 1))
        spawn.assert_not_called()

    def test_nothing_for_a_pause_already_warned_about(self):
        state = _running(layer=2, remaining=85)
        state.pause_warned_layers.add(3)
        with patch.object(pause_eta, "spawn_background_task") as spawn:
            pause_eta.observe(1, state)
        spawn.assert_not_called()

    def test_nothing_while_the_time_is_unknown(self):
        with patch.object(pause_eta, "spawn_background_task") as spawn:
            pause_eta.observe(1, _running(layer=0, remaining=0))
        spawn.assert_not_called()

    def test_checks_at_most_once_per_interval(self):
        with patch.object(pause_eta, "spawn_background_task") as spawn:
            pause_eta.observe(1, _running(layer=2, remaining=85))
            pause_eta.observe(1, _running(layer=2, remaining=84))
            pause_eta.observe(2, _running(layer=2, remaining=84))
        assert spawn.call_count == 2  # once per printer
        for call in spawn.call_args_list:
            call.args[0].close()


class _Session:
    def __init__(self, printer):
        self.get = AsyncMock(return_value=printer)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _warn(state, *, lead: int | None, printer=SimpleNamespace(name="X1C")):
    """Run the delayed check against ``state`` with the setting at ``lead``."""
    client = SimpleNamespace(state=state)
    notify = AsyncMock()
    with (
        patch("backend.app.services.printer_manager.printer_manager.get_client", return_value=client),
        patch("backend.app.core.database.async_session", return_value=_Session(printer)),
        patch(
            "backend.app.api.routes.settings.get_setting", AsyncMock(return_value=None if lead is None else str(lead))
        ),
        patch("backend.app.services.notification_service.notification_service.on_print_pause_upcoming", notify),
    ):
        asyncio.run(pause_eta._warn_if_due(1, 3))
    return notify


class TestWarnIfDue:
    def test_sends_within_the_default_five_minutes(self):
        state = _running(layer=2, remaining=82)
        notify = _warn(state, lead=None)
        notify.assert_awaited_once()
        kwargs = notify.await_args.kwargs
        assert notify.await_args.args[:3] == (1, "X1C", "Wifi_QR")
        assert (kwargs["minutes"], kwargs["layer"], kwargs["total_layers"]) == (4, 3, 30)
        assert state.pause_warned_layers == {3}

    def test_waits_while_further_away_than_the_setting(self):
        state = _running(layer=2, remaining=90)
        notify = _warn(state, lead=10)
        notify.assert_not_awaited()
        assert state.pause_warned_layers == set()

    def test_a_longer_setting_sends_earlier(self):
        notify = _warn(_running(layer=2, remaining=90), lead=15)
        notify.assert_awaited_once()

    def test_once_per_pause(self):
        state = _running(layer=2, remaining=80)
        _warn(state, lead=5)
        notify = _warn(state, lead=5)
        notify.assert_not_awaited()

    def test_not_when_the_print_moved_past_it_meanwhile(self):
        notify = _warn(_running(layer=3, remaining=76), lead=120)
        notify.assert_not_awaited()

    def test_bad_setting_falls_back_to_the_default(self):
        notify = _warn(_running(layer=2, remaining=82), lead="soon")  # type: ignore[arg-type]
        notify.assert_awaited_once()

    def test_setting_is_clamped_to_the_largest_heads_up(self):
        notify = _warn(_running(layer=1, remaining=78 + 200), lead=500)
        notify.assert_not_awaited()


class TestNotificationService:
    def test_variables_for_the_template(self):
        from backend.app.services.notification_service import NotificationService

        service = NotificationService()
        db = AsyncMock()
        with (
            patch.object(service, "_get_providers_for_event", AsyncMock(return_value=[MagicMock()])) as providers,
            patch.object(service, "_build_message_from_template", AsyncMock(return_value=("t", "m"))) as build,
            patch.object(service, "_send_to_providers", AsyncMock()) as send,
            patch.object(service, "_format_eta", AsyncMock(return_value="16:05")) as eta,
        ):
            asyncio.run(
                service.on_print_pause_upcoming(
                    1, "X1C", "/data/Wifi_QR.gcode.3mf", minutes=0, layer=42, total_layers=120, db=db
                )
            )
        assert providers.await_args.args[1] == "on_print_pause_upcoming"
        assert build.await_args.args[1] == "print_pause_upcoming"
        variables = build.await_args.args[2]
        assert variables == {
            "printer": "X1C",
            "filename": "Wifi_QR",
            "time_to_pause": "1m",
            "eta": "16:05",
            "layer": "42",
            "total_layers": "120",
        }
        # A pause under a minute away is due now, not "Unknown".
        assert eta.await_args.args[0] > 0
        assert send.await_args.args[4] == "print_pause_upcoming"

    def test_nothing_without_providers(self):
        from backend.app.services.notification_service import NotificationService

        service = NotificationService()
        with (
            patch.object(service, "_get_providers_for_event", AsyncMock(return_value=[])),
            patch.object(service, "_send_to_providers", AsyncMock()) as send,
        ):
            asyncio.run(
                service.on_print_pause_upcoming(1, "X1C", "a.3mf", minutes=3, layer=2, total_layers=9, db=AsyncMock())
            )
        send.assert_not_awaited()

    def test_default_template_uses_only_its_variables(self):
        from backend.app.models.notification_template import DEFAULT_TEMPLATES
        from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA

        template = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "print_pause_upcoming")
        allowed = set(EVENT_VARIABLES["print_pause_upcoming"])
        used = set(__import__("re").findall(r"\{([a-z_]+)\}", template["title_template"] + template["body_template"]))
        assert used <= allowed
        assert set(SAMPLE_DATA["print_pause_upcoming"]) == allowed


class TestLoadFromArchive:
    def _client(self):
        state = PrinterState()
        state.subtask_name = "Wifi_QR"
        return SimpleNamespace(state=state)

    def _run_load(self, client, path, *, replace_before_done=False, warned_layers=(), passed_through_layer=0):
        from backend.app import main

        spawned = []

        async def go():
            with (
                patch.object(main.printer_manager, "get_client", return_value=client),
                patch.object(main, "resolve_plate_id", return_value=1),
                patch.object(main, "spawn_background_task", side_effect=lambda coro, name=None: spawned.append(coro)),
            ):
                main._load_pauses_from_3mf(
                    path, 1, warned_layers=warned_layers, passed_through_layer=passed_through_layer
                )
            if replace_before_done:
                client.state.pause_points = []  # a new print started meanwhile
            await spawned[0]

        asyncio.run(go())

    def test_loads_the_pauses(self, tmp_path):
        path = tmp_path / "p.gcode.3mf"
        path.write_bytes(_three_mf({"Metadata/plate_1.gcode": GCODE}))
        client = self._client()
        self._run_load(client, path)
        assert [p.layer for p in client.state.pause_points] == [3, 5]

    def test_with_the_layers_already_warned_about(self, tmp_path):
        path = tmp_path / "p.gcode.3mf"
        path.write_bytes(_three_mf({"Metadata/plate_1.gcode": GCODE}))
        client = self._client()
        self._run_load(client, path, warned_layers={3})
        assert client.state.pause_warned_layers == {3}

    def test_without_the_pauses_a_resumed_print_has_passed(self, tmp_path):
        path = tmp_path / "p.gcode.3mf"
        path.write_bytes(_three_mf({"Metadata/plate_1.gcode": GCODE}))
        client = self._client()
        self._run_load(client, path, passed_through_layer=3)
        assert [p.layer for p in client.state.pause_points] == [5]

    def test_streams_a_large_plate(self, tmp_path):
        # Read line by line; the result is the same as for the whole text.
        body = "G1 X1 E0.1\n" * 200_000
        path = tmp_path / "big.gcode.3mf"
        path.write_bytes(_three_mf({"Metadata/plate_1.gcode": body + GCODE}))
        assert extract_pause_points_from_3mf(path, 1) == parse_gcode_pause_points(GCODE)

    def test_dropped_when_another_print_started_meanwhile(self, tmp_path):
        path = tmp_path / "p.gcode.3mf"
        path.write_bytes(_three_mf({"Metadata/plate_1.gcode": GCODE}))
        client = self._client()
        self._run_load(client, path, replace_before_done=True)
        assert client.state.pause_points == []


class TestLoadedWhereObjectsAre:
    """The pauses are read wherever a print's archive is bound at its start,
    and again after a restart, like the skip-objects list."""

    def test_print_start_reads_the_archive_s_pauses(self, tmp_path):
        from backend.tests.unit.test_reprint_updates_subtask_id import _clear_dicts, _drive, _make_archive  # noqa: F401

        archive = _make_archive(archive_id=31, stored_subtask_id=None)
        with patch("backend.app.main._load_pauses_from_3mf") as load:
            asyncio.run(_drive(tmp_path, archive, mqtt_subtask_id="2103771517"))

        load.assert_called_once()
        assert load.call_args.args == (tmp_path / archive.file_path, 1)

    def _restore(self, *, objects, pauses, extra_data=None):
        import logging

        from backend.app import main

        archive = MagicMock(id=360, file_path="archive/7/job/job.3mf", extra_data=extra_data or {})
        client = MagicMock()
        client.state = MagicMock(printable_objects=objects, pause_points=pauses)
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=archive)
        with (
            patch.object(main, "printer_manager") as pm,
            patch.object(main, "_load_objects_from_archive") as load_objects,
            patch.object(main, "_load_pauses_from_3mf") as load_pauses,
        ):
            pm.get_client.return_value = client
            asyncio.run(
                main._restore_printable_objects(7, MagicMock(subtask_id="330151809"), db, logging.getLogger("t"))
            )
        return load_objects, load_pauses

    def test_restart_reloads_the_pauses(self):
        from backend.app.core.config import settings as app_settings

        load_objects, load_pauses = self._restore(objects={}, pauses=[])
        load_objects.assert_called_once()
        assert load_pauses.call_args.args == (app_settings.base_dir / "archive/7/job/job.3mf", 7)
        assert load_pauses.call_args.kwargs == {"warned_layers": set()}

    def test_restart_keeps_the_pauses_already_warned_about(self):
        _objects, load_pauses = self._restore(
            objects={},
            pauses=[],
            extra_data={"pause_warned_layers": {"subtask_id": "330151809", "layers": [3]}},
        )
        assert load_pauses.call_args.kwargs == {"warned_layers": {3}}

    def test_restart_reloads_the_pauses_when_the_objects_are_loaded(self):
        load_objects, load_pauses = self._restore(objects={1: "cube"}, pauses=[])
        load_objects.assert_not_called()
        load_pauses.assert_called_once()

    def test_loaded_pauses_are_left_alone(self):
        load_objects, load_pauses = self._restore(objects={}, pauses=[PausePoint(3, 78)])
        load_objects.assert_called_once()
        load_pauses.assert_not_called()


class TestStatusHook:
    def test_every_status_push_is_checked(self):
        from backend.app import main

        state = _running(layer=2, remaining=85)
        with patch.object(main.pause_eta, "observe") as observe:
            asyncio.run(main.on_printer_status_change(1, state))
        observe.assert_called_once_with(1, state)

    def test_a_failing_check_does_not_stop_the_status_handler(self):
        from backend.app import main

        state = _running(layer=2, remaining=85)
        with patch.object(main.pause_eta, "observe", side_effect=RuntimeError("boom")):
            # Must not raise: the rest of the handler sends the status out.
            asyncio.run(main.on_printer_status_change(1, state))


class TestWarnedLayersOnTheArchive:
    def _state(self, subtask_id="4242"):
        state = _running(layer=2, remaining=82)
        state.subtask_id = subtask_id
        return state

    def test_read_back_for_the_same_run(self):
        archive = SimpleNamespace(extra_data={"pause_warned_layers": {"subtask_id": "4242", "layers": [3, 5]}})
        assert pause_eta.warned_layers_of(archive, self._state()) == {3, 5}

    def test_not_for_another_run_of_the_same_archive(self):
        archive = SimpleNamespace(extra_data={"pause_warned_layers": {"subtask_id": "1111", "layers": [3]}})
        assert pause_eta.warned_layers_of(archive, self._state()) == set()

    @pytest.mark.parametrize("subtask_id", ["", "0"])
    def test_not_for_a_print_without_a_run_id(self, subtask_id):
        archive = SimpleNamespace(extra_data={"pause_warned_layers": {"subtask_id": subtask_id, "layers": [3]}})
        assert pause_eta.warned_layers_of(archive, self._state(subtask_id)) == set()

    @pytest.mark.parametrize("saved", [None, [], "3", {"subtask_id": "4242", "layers": ["x", 3]}])
    def test_tolerates_what_it_did_not_write(self, saved):
        archive = SimpleNamespace(extra_data={"pause_warned_layers": saved})
        assert pause_eta.warned_layers_of(archive, self._state()) <= {3}

    def test_kept_when_the_heads_up_goes_out(self):
        state = self._state()
        archive = SimpleNamespace(extra_data={"other": 1})
        db = MagicMock(commit=AsyncMock())
        with patch("backend.app.main.running_print_archive", AsyncMock(return_value=archive)):
            state.pause_warned_layers.add(3)
            asyncio.run(pause_eta._remember_warned(db, 1, state))
        assert archive.extra_data == {"other": 1, "pause_warned_layers": {"subtask_id": "4242", "layers": [3]}}
        db.commit.assert_awaited_once()

    def test_warn_if_due_keeps_it(self):
        state = self._state()
        with patch.object(pause_eta, "_remember_warned", AsyncMock()) as remember:
            _warn(state, lead=5)
        remember.assert_awaited_once()

    def test_a_failure_to_keep_it_is_not_a_failure_to_warn(self):
        state = self._state()
        with patch.object(pause_eta, "_remember_warned", AsyncMock(side_effect=RuntimeError("db"))):
            notify = _warn(state, lead=5)
        notify.assert_awaited_once()
        assert state.pause_warned_layers == {3}


class TestRecoveredFallbackArchive:
    """A print that started without its 3MF (FTPS cool-off, slow transfer)
    gets its pauses once the 3MF is recovered (#2957 / #3063)."""

    async def _recover(self, test_engine, tmp_path, status, *, price=True):
        from backend.app import main as main_module
        from backend.app.core.config import settings as app_settings
        from backend.tests.unit.test_fallback_archive_recovery_2957 import DISPATCH_FILENAME, _seed, _write_3mf

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            archive.status = status
            await db.commit()
        source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)
        with (
            patch.object(main_module, "async_session", maker),
            patch.object(main_module, "_schedule_archive_cost_estimate"),
            patch.object(main_module, "_load_pauses_from_3mf") as load,
            patch.object(app_settings, "base_dir", tmp_path),
            patch.object(app_settings, "archive_dir", tmp_path / "archive"),
            patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source, price=price)
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
        return load, printer_id, archive

    @pytest.mark.asyncio
    async def test_while_it_prints(self, test_engine, tmp_path):
        load, printer_id, archive = await self._recover(test_engine, tmp_path, "printing")
        load.assert_called_once_with(tmp_path / archive.file_path, printer_id)

    @pytest.mark.asyncio
    async def test_not_once_it_has_ended(self, test_engine, tmp_path):
        load, _printer_id, _archive = await self._recover(test_engine, tmp_path, "failed")
        load.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_from_its_own_completion(self, test_engine, tmp_path):
        load, _printer_id, _archive = await self._recover(test_engine, tmp_path, "printing", price=False)
        load.assert_not_called()


def test_a_plate_that_cannot_be_told_does_not_break_the_print_start():
    from backend.app import main

    client = SimpleNamespace(state=PrinterState())
    with (
        patch.object(main.printer_manager, "get_client", return_value=client),
        patch.object(main, "resolve_plate_id", side_effect=TypeError("odd gcode_file")),
        patch.object(main, "spawn_background_task") as spawn,
    ):
        main._load_pauses_from_3mf("/nowhere.3mf", 1)  # must not raise
    spawn.assert_not_called()


def test_nothing_kept_without_the_print_s_archive():
    state = _running(layer=2, remaining=82)
    db = MagicMock(commit=AsyncMock())
    with patch("backend.app.main.running_print_archive", AsyncMock(return_value=None)):
        asyncio.run(pause_eta._remember_warned(db, 1, state))
    db.commit.assert_not_awaited()


class TestResumedArchive:
    """A restart while the print was paused makes its resume a print start."""

    def test_keeps_what_was_warned_and_passed(self):
        from backend.app import main
        from backend.app.core.config import settings as app_settings

        state = _running(layer=0, remaining=78)
        state.subtask_id = "4242"
        client = SimpleNamespace(state=state, layer_before_new_print=3)
        archive = SimpleNamespace(
            file_path="archive/1/job/job.3mf",
            extra_data={"pause_warned_layers": {"subtask_id": "4242", "layers": [3]}},
        )
        with (
            patch.object(main.printer_manager, "get_client", return_value=client),
            patch.object(main, "_load_pauses_from_3mf") as load,
        ):
            main._load_pauses_from_resumed_archive(archive, 1)
        load.assert_called_once_with(
            app_settings.base_dir / "archive/1/job/job.3mf", 1, warned_layers={3}, passed_through_layer=3
        )

    def test_without_a_client(self):
        from backend.app import main

        archive = SimpleNamespace(file_path="archive/1/job/job.3mf", extra_data={})
        with (
            patch.object(main.printer_manager, "get_client", return_value=None),
            patch.object(main, "_load_pauses_from_3mf") as load,
        ):
            main._load_pauses_from_resumed_archive(archive, 1)
        assert load.call_args.kwargs == {"warned_layers": (), "passed_through_layer": 0}
