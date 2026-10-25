"""Chamber light for the camera (#1655).

The light goes on for a camera use when the light mode covers the printer, stays on
while anything still uses the camera, and goes off after a grace only if
Bambuddy turned it on.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.services import camera_light as svc
from backend.app.services.camera_light import camera_light

pytestmark = pytest.mark.unit

# The autouse fixture stubs the settings read; this is the real one.
_real_read_settings = svc._read_settings

GRACE = 0.05


class LightSettings:
    """Stands in for reading the two app settings; counts the reads."""

    def __init__(self):
        self.mode = "all"
        self.delay = 0.0
        self.reads = 0

    async def __call__(self):
        self.reads += 1
        return self.mode, self.delay


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(svc, "_holders", {})
    monkeypatch.setattr(svc, "_lit_at", {})
    off_tasks: dict = {}
    monkeypatch.setattr(svc, "_off_tasks", off_tasks)
    monkeypatch.setattr(svc, "OFF_GRACE_SECONDS", GRACE)
    light = LightSettings()
    monkeypatch.setattr(svc, "_read_settings", light)
    monkeypatch.setattr(svc, "_settings_cache", None)
    yield light
    for task in off_tasks.values():
        task.cancel()


class FakeClient:
    """A printer whose light state follows its commands, like a real report would."""

    def __init__(self, light_on: bool = False, reports: bool = True):
        self.state = SimpleNamespace(chamber_light=light_on)
        self.calls: list[bool] = []
        self._reports = reports

    def set_chamber_light(self, on: bool) -> bool:
        self.calls.append(on)
        if self._reports:
            self.state.chamber_light = on
        return True


def _printer(selected=False, printer_id=1):
    return SimpleNamespace(id=printer_id, camera_light_auto=selected)


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(svc, "_get_client", lambda _pid: fake)
    return fake


async def _past_grace():
    await asyncio.sleep(GRACE * 3)


class TestWhichPrinters:
    @pytest.mark.asyncio
    async def test_mode_off_never_switches(self, client, settings):
        settings.mode = "off"
        async with camera_light(_printer(selected=True)):
            pass
        await _past_grace()
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_mode_all_switches_every_printer(self, client, settings):
        settings.mode = "all"
        async with camera_light(_printer(selected=False)):
            pass
        assert client.calls == [True]

    @pytest.mark.asyncio
    async def test_mode_selected_switches_only_picked_printers(self, client, settings):
        settings.mode = "selected"
        async with camera_light(_printer(selected=False)):
            pass
        assert client.calls == []
        async with camera_light(_printer(selected=True)):
            pass
        assert client.calls == [True]

    @pytest.mark.asyncio
    async def test_no_printer_is_a_no_op(self, client):
        async with camera_light(None):
            pass
        assert client.calls == []


class TestTurnsOnAndOff:
    @pytest.mark.asyncio
    async def test_a_dark_printer_is_lit_for_the_picture_and_turned_off_after(self, client):
        async with camera_light(_printer()):
            assert client.calls == [True]
        assert client.calls == [True]  # not yet: the grace runs first
        await _past_grace()
        assert client.calls == [True, False]

    @pytest.mark.asyncio
    async def test_a_light_that_was_already_on_is_left_alone(self, client, settings):
        client.state.chamber_light = True
        settings.delay = 5
        async with camera_light(_printer()):
            pass
        await _past_grace()
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_the_light_stays_on_until_the_last_user_lets_go(self, client):
        printer = _printer()
        viewer = camera_light(printer, wait=False)
        await viewer.__aenter__()
        async with camera_light(printer):
            pass
        await _past_grace()
        assert client.calls == [True]  # the viewer still watches
        await viewer.__aexit__(None, None, None)
        await _past_grace()
        assert client.calls == [True, False]

    @pytest.mark.asyncio
    async def test_coming_back_within_the_grace_keeps_the_light_on(self, client):
        # The camera wall polls each tile every 8 seconds; it must not flash.
        printer = _printer()
        async with camera_light(printer):
            pass
        async with camera_light(printer):
            pass
        assert client.calls == [True]
        await _past_grace()
        assert client.calls == [True, False]


class TestTheDelay:
    @pytest.mark.asyncio
    async def test_a_snapshot_waits_after_turning_the_light_on(self, client, settings):
        settings.delay = 0.2
        start = time.monotonic()
        async with camera_light(_printer()):
            waited = time.monotonic() - start
        assert waited >= 0.2

    @pytest.mark.asyncio
    async def test_a_live_view_does_not_wait_either_way(self, client, settings):
        settings.delay = 5
        start = time.monotonic()
        async with camera_light(_printer(), wait=False):
            waited = time.monotonic() - start
        assert waited < 1

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_a_second_snapshot_waits_only_for_what_is_left_and_sends_nothing(self, monkeypatch, settings):
        # The printer hasn't reported the light yet: the second picture trusts
        # our own command instead of sending another, and still waits for it.
        slow = FakeClient(reports=False)
        monkeypatch.setattr(svc, "_get_client", lambda _pid: slow)
        settings.delay = 0.3
        printer = _printer()
        first = asyncio.create_task(_hold(printer))
        await asyncio.sleep(0.1)
        start = time.monotonic()
        async with camera_light(printer):
            waited = time.monotonic() - start
        await first
        assert slow.calls == [True]
        assert 0.1 <= waited < 0.3


async def _hold(printer):
    async with camera_light(printer):
        pass


class TestTheUserDecides:
    @pytest.mark.asyncio
    async def test_switching_the_light_by_hand_keeps_it_on(self, client):
        printer = _printer()
        async with camera_light(printer):
            svc.hand_back(printer.id)
        await _past_grace()
        assert client.calls == [True]

    @pytest.mark.asyncio
    async def test_a_light_switched_off_at_the_printer_comes_back_for_the_next_picture(self, client, monkeypatch):
        monkeypatch.setattr(svc, "_REPORT_LAG_SECONDS", 0.05)
        printer = _printer()
        viewer = camera_light(printer, wait=False)
        await viewer.__aenter__()
        client.state.chamber_light = False  # switched off on the printer's screen
        await asyncio.sleep(0.1)
        async with camera_light(printer):
            pass
        await viewer.__aexit__(None, None, None)
        await _past_grace()
        assert client.calls == [True, True, False]


class TestFailuresNeverCostThePicture:
    @pytest.mark.asyncio
    async def test_a_disconnected_printer_still_gets_its_capture(self, monkeypatch, settings):
        monkeypatch.setattr(svc, "_get_client", lambda _pid: None)
        settings.delay = 5
        ran = False
        async with camera_light(_printer()):
            ran = True
        assert ran
        assert svc._holders == {}

    @pytest.mark.asyncio
    async def test_a_failing_light_command_still_gets_its_capture(self, monkeypatch, settings):
        broken = MagicMock()
        broken.state = SimpleNamespace(chamber_light=False)
        broken.set_chamber_light.side_effect = RuntimeError("mqtt down")
        monkeypatch.setattr(svc, "_get_client", lambda _pid: broken)
        settings.delay = 5
        ran = False
        async with camera_light(_printer()):
            ran = True
        assert ran
        assert svc._holders == {}

    @pytest.mark.asyncio
    async def test_a_capture_that_raises_still_lets_go_of_the_light(self, client):
        with pytest.raises(ValueError):
            async with camera_light(_printer()):
                raise ValueError("capture failed")
        await _past_grace()
        assert client.calls == [True, False]
        assert svc._holders == {}


class TestTheSettings:
    @pytest.mark.asyncio
    async def test_settings_are_read_once_then_cached_until_saved(self, client, settings):
        async with camera_light(_printer()):
            pass
        async with camera_light(_printer()):
            pass
        assert settings.reads == 1
        svc.invalidate_settings()
        async with camera_light(_printer()):
            pass
        assert settings.reads == 2

    @pytest.mark.asyncio
    async def test_unreadable_settings_switch_nothing(self, client, monkeypatch):
        async def _broken():
            raise RuntimeError("database gone")

        monkeypatch.setattr(svc, "_read_settings", _broken)
        async with camera_light(_printer(selected=True)):
            pass
        assert client.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stored_mode", "stored_delay", "expected"),
        [
            ("all", "1.5", ("all", 1.5)),
            ("selected", None, ("selected", 2.0)),
            (None, None, ("off", 2.0)),  # never saved
            ("sometimes", "x", ("off", 2.0)),  # garbage reads as off
            ("all", "30", ("all", 5.0)),  # never past the finish photo's budget
            ("all", "-3", ("all", 0.0)),
        ],
    )
    async def test_stored_values_are_parsed_safely(self, monkeypatch, stored_mode, stored_delay, expected):
        from contextlib import asynccontextmanager

        stored = {"camera_light_mode": stored_mode, "camera_light_delay": stored_delay}

        async def _get_setting(_db, key):
            return stored[key]

        @asynccontextmanager
        async def _session():
            yield object()

        monkeypatch.setattr("backend.app.api.routes.settings.get_setting", _get_setting)
        monkeypatch.setattr("backend.app.core.database.async_session", _session)
        assert await _real_read_settings() == expected


class TestThePlateCheck:
    """SP-C11: the plate check lit the chamber itself, outside the count, and
    switched it off when done -- under a live view opened meanwhile, which had
    found the light on and so never took it."""

    @pytest.mark.asyncio
    async def test_it_lights_the_chamber_whatever_the_mode(self, client, settings):
        settings.mode = "off"
        async with camera_light(_printer(), any_mode=True, settle=0):
            assert client.calls == [True]
        await _past_grace()
        assert client.calls == [True, False]

    @pytest.mark.asyncio
    async def test_a_viewer_who_came_meanwhile_keeps_the_light(self, client):
        printer = _printer()
        async with camera_light(printer, any_mode=True, settle=0):
            viewer = camera_light(printer, wait=False)
            await viewer.__aenter__()
        await _past_grace()
        assert client.calls == [True]
        await viewer.__aexit__(None, None, None)
        await _past_grace()
        assert client.calls == [True, False]

    @pytest.mark.asyncio
    async def test_it_waits_its_own_settle_time(self, client, settings):
        settings.delay = 0
        start = time.monotonic()
        async with camera_light(_printer(), any_mode=True, settle=0.2):
            waited = time.monotonic() - start
        assert waited >= 0.2
