"""A printer's own "Require plate-clear confirmation" (#2860).

The app setting gated every printer the same way, so a printer with an
automatic plate changer and one cleared by hand could not share a queue. A
printer may now override it: on, off, or null to follow the setting. These
tests cover the cache, every place the scheduler applies the gate, and WLED.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import plate_clear
from backend.app.services.print_scheduler import PrintScheduler, _ModelCandidate
from backend.app.services.wled import WLEDManager

A1 = 1  # clears its own plate
P1S = 2  # cleared by hand


@pytest.fixture(autouse=True)
def overrides():
    plate_clear.load_overrides({})
    yield plate_clear
    plate_clear.load_overrides({})


class TestCache:
    def test_no_override_follows_the_setting(self):
        assert plate_clear.applies(A1, True) is True
        assert plate_clear.applies(A1, False) is False

    def test_override_wins_over_the_setting(self, overrides):
        overrides.set_override(A1, False)
        overrides.set_override(P1S, True)
        assert plate_clear.applies(A1, True) is False
        assert plate_clear.applies(P1S, False) is True

    def test_none_removes_the_override(self, overrides):
        overrides.set_override(A1, False)
        overrides.set_override(A1, None)
        assert plate_clear.applies(A1, True) is True

    def test_load_replaces_every_override(self, overrides):
        overrides.set_override(A1, False)
        overrides.load_overrides({P1S: True, 3: None})
        assert plate_clear.applies(A1, True) is True
        assert plate_clear.applies(P1S, False) is True
        assert plate_clear.applies(3, False) is False


def _awaiting_printer_manager():
    """Both printers connected, finished and awaiting plate-clear."""
    pm = MagicMock()
    pm.is_connected.return_value = True
    pm.get_status.return_value = MagicMock(state="FINISH")
    pm.is_awaiting_plate_clear.return_value = True
    return pm


class TestIdleCheck:
    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def test_printer_turned_off_is_idle_with_the_setting_on(self, overrides, scheduler):
        overrides.set_override(A1, False)
        with patch("backend.app.services.print_scheduler.printer_manager", _awaiting_printer_manager()):
            assert scheduler._is_printer_idle(A1, True) is True
            assert scheduler._is_printer_idle(P1S, True) is False

    def test_printer_turned_on_is_held_with_the_setting_off(self, overrides, scheduler):
        overrides.set_override(P1S, True)
        with patch("backend.app.services.print_scheduler.printer_manager", _awaiting_printer_manager()):
            assert scheduler._is_printer_idle(P1S, False) is False
            assert scheduler._is_printer_idle(A1, False) is True

    def test_drying_ignores_a_printer_turned_on(self, overrides, scheduler):
        """Drying never waited for the plate (#2801); a printer's own setting
        must not start holding it."""
        overrides.set_override(P1S, True)
        with patch("backend.app.services.print_scheduler.printer_manager", _awaiting_printer_manager()):
            assert scheduler._is_printer_idle(P1S, False, ignore_plate_clear=True) is True
            assert scheduler._is_printer_idle(P1S, True, ignore_plate_clear=True) is True


class TestPinnedHoldReason:
    def test_printer_turned_off_reads_busy(self, overrides):
        overrides.set_override(A1, False)
        with patch("backend.app.services.print_scheduler.printer_manager", _awaiting_printer_manager()):
            assert PrintScheduler._pinned_hold_reason(A1, "A1 mini", True) == "Busy: A1 mini"

    def test_printer_turned_on_waits_for_the_plate(self, overrides):
        overrides.set_override(P1S, True)
        with patch("backend.app.services.print_scheduler.printer_manager", _awaiting_printer_manager()):
            assert PrintScheduler._pinned_hold_reason(P1S, "P1S", False) == "Waiting for plate confirmation: P1S"


class TestWake:
    """A printer the gate holds is not powered on for a job; one it does not
    hold is, though its flag is up."""

    @staticmethod
    async def _woken(require_plate_clear: bool) -> tuple[int | None, int | None]:
        scheduler = PrintScheduler()
        printer = SimpleNamespace(id=A1, name="A1 mini")
        plug = SimpleNamespace(id=1, auto_on=True, enabled=True, controls_printer_power=True)
        pm = MagicMock()
        pm.is_connected.return_value = False
        pm.is_awaiting_plate_clear.return_value = True
        pm.get_status.return_value = None
        with (
            patch("backend.app.services.print_scheduler.printer_manager", pm),
            patch.object(scheduler, "_printers_for_model", AsyncMock(return_value=[printer])),
            patch.object(scheduler, "_cached_filament_shortfall", MagicMock(return_value=[])),
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[plug])),
            patch.object(scheduler, "_power_on_and_wait", AsyncMock(return_value=True)),
        ):
            return await scheduler._wake_printer_for_model(
                AsyncMock(),
                [
                    _ModelCandidate(
                        target_model="A1M", sliced_for="A1M", required_filament_types=None, filament_overrides=None
                    )
                ],
                None,
                set(),
                {A1},
                require_plate_clear,
            )

    @pytest.mark.asyncio
    async def test_printer_turned_off_is_woken_with_the_setting_on(self, overrides):
        overrides.set_override(A1, False)
        assert await self._woken(True) == (A1, A1)

    @pytest.mark.asyncio
    async def test_printer_turned_on_is_not_woken_with_the_setting_off(self, overrides):
        overrides.set_override(A1, True)
        assert await self._woken(False) == (None, None)


class TestKeepWarm:
    """Keep-warm holds the bed while someone clears the plate, so it only
    runs on a printer the gate applies to."""

    @staticmethod
    async def _held(require_plate_clear: bool) -> set[int]:
        scheduler = PrintScheduler()
        items = [
            SimpleNamespace(
                id=pid,
                printer_id=pid,
                archive=SimpleNamespace(file_path=None, bed_temperature=60),
                preheat_chamber_target_override=60,
            )
            for pid in (A1, P1S)
        ]
        clients = {A1: MagicMock(), P1S: MagicMock()}
        pm = MagicMock()
        pm.get_status.return_value = SimpleNamespace(
            state="FINISH", temperatures={"bed_target": 0.0, "chamber": 55.0}, raw_data={}
        )
        pm.get_client.side_effect = clients.get
        settings = {"queue_keep_bed_warm": True, "preheat_enabled": True}
        with (
            patch("backend.app.services.print_scheduler.printer_manager", pm),
            patch.object(
                scheduler,
                "_get_bool_setting",
                AsyncMock(side_effect=lambda _db, key, default: settings.get(key, default)),
            ),
            patch.object(scheduler, "_get_int_setting", AsyncMock(side_effect=lambda _db, key, default=0: default)),
        ):
            await scheduler._apply_keep_warm(AsyncMock(), items, [], {A1, P1S}, require_plate_clear)
        return {pid for pid, client in clients.items() if client.set_bed_temperature.called}

    @pytest.mark.asyncio
    async def test_printer_turned_off_is_not_held_with_the_setting_on(self, overrides):
        overrides.set_override(A1, False)
        assert await self._held(True) == {P1S}

    @pytest.mark.asyncio
    async def test_printer_turned_on_is_held_with_the_setting_off(self, overrides):
        overrides.set_override(P1S, True)
        assert await self._held(False) == {P1S}

    @pytest.mark.asyncio
    async def test_nothing_held_with_the_setting_off_and_no_override(self):
        assert await self._held(False) == set()


class TestWled:
    """The "queue waiting" preset shows only where the gate holds the queue."""

    @staticmethod
    def _status(printer_id: int, require_plate_clear: bool) -> str | None:
        from backend.app.schemas.printer import WLEDConfig

        manager = WLEDManager(client=MagicMock())
        manager.require_plate_clear = require_plate_clear
        manager._configs[printer_id] = WLEDConfig.model_validate(
            {"enabled": True, "base_url": "http://wled.local", "presets": {"queue_waiting": 5, "finished": 6}}
        )
        sent = []
        with patch.object(manager, "_apply_status", lambda pid, config, status, preset: sent.append(status)):
            manager.handle_status(
                printer_id,
                SimpleNamespace(connected=True, state="FINISH", stg_cur=0, hms_errors=[]),
                awaiting_plate_clear=True,
            )
        return sent[0]

    def test_printer_turned_off_shows_finished_with_the_setting_on(self, overrides):
        overrides.set_override(A1, False)
        assert self._status(A1, True) == "finished"
        assert self._status(P1S, True) == "queue_waiting"

    def test_printer_turned_on_shows_queue_waiting_with_the_setting_off(self, overrides):
        overrides.set_override(P1S, True)
        assert self._status(P1S, False) == "queue_waiting"
        assert self._status(A1, False) == "finished"
