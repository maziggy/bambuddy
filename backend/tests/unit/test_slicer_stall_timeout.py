"""Tests for the progress-supervised slice timeout (#2730).

The old behaviour was a flat 300 s httpx timeout on the slice POST. A heavy
model that Bambu Studio also took a long time over blew through it while the
slicer was working perfectly happily, and — because ``httpx.ReadTimeout`` is a
subclass of ``RequestError`` — the failure was reported as "Slicer sidecar
unreachable", sending the reporter off to check a sidecar that was reachable
throughout.

The wait is now bounded by *silence* instead: Bambuddy already polls the
sidecar's progress endpoint once a second, so it can tell a slow slice from a
stalled one. The deadline moves forward on every progress update.
"""

import asyncio

import httpx
import pytest

from backend.app.services.slicer_api import (
    DEFAULT_SLICE_STALL_TIMEOUT_SECONDS,
    SlicerApiService,
    SlicerApiUnavailableError,
    SlicerTimeoutError,
    _Liveness,
    get_stall_timeout_seconds,
)

SLICE_ARGS = {
    "model_bytes": b"solid\n",
    "model_filename": "cube.3mf",
    "printer_profile_json": "{}",
    "process_profile_json": "{}",
    "filament_profile_jsons": ["{}"],
}


def _service(handler, *, timeout_seconds: float, poll_interval: float = 0.02) -> SlicerApiService:
    """A service wired to a mock sidecar, with the timing compressed.

    The stall window is floored at three poll intervals — liveness can only be
    observed as fast as the poller ticks — so tests shrink both together rather
    than waiting out production's 1 Hz.
    """
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = SlicerApiService("http://sidecar:3003", client=client, timeout_seconds=timeout_seconds)
    svc.progress_poll_interval = poll_interval
    return svc


class TestLivenessWindow:
    """The unit that decides when to stop waiting."""

    def test_a_fresh_slice_has_the_full_window(self):
        live = _Liveness(60.0, 1.0)
        assert live.deadline - live.started_at == pytest.approx(60.0)

    def test_progress_pushes_the_deadline_out(self):
        live = _Liveness(60.0, 1.0)
        live.saw_progress_endpoint()
        before = live.deadline
        live._last_alive += 30.0  # simulate a progress update 30s later
        assert live.deadline > before

    def test_without_a_progress_channel_the_window_is_total_elapsed(self):
        """No liveness signal means no way to tell slow from stalled, so the
        window degrades to the pre-#2730 wall clock — just configurable."""
        live = _Liveness(60.0, 1.0)
        live.mark_alive()  # would move the deadline if progress were supported
        assert live.deadline == pytest.approx(live.started_at + 60.0)

    def test_message_distinguishes_the_two_cases(self):
        supported = _Liveness(60.0, 1.0)
        supported.saw_progress_endpoint()
        assert "stopped reporting progress" in supported.timeout_message()

        unsupported = _Liveness(60.0, 1.0)
        assert "does not report progress" in unsupported.timeout_message()

    def test_message_points_at_the_setting(self):
        live = _Liveness(900.0, 1.0)
        assert "Settings -> Workflow -> Slicer" in live.timeout_message()


class TestSliceIsNotCutOffWhileProgressing:
    @pytest.mark.asyncio
    async def test_a_slow_slice_that_reports_progress_completes(self):
        """The reporter's case: slower than the old ceiling, still working.

        The slice takes ~5x the stall window; progress keeps arriving, so it
        must run to completion rather than being abandoned.
        """
        progress = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/slice"):
                await asyncio.sleep(0.5)
                return httpx.Response(
                    200,
                    content=b"G1 X0\n",
                    headers={
                        "x-print-time-seconds": "100",
                        "x-filament-used-g": "1.0",
                        "x-filament-used-mm": "100",
                    },
                )
            progress["n"] += 1
            return httpx.Response(200, json={"percent": progress["n"]})

        svc = _service(handler, timeout_seconds=0.1)
        result = await svc.slice_with_profiles(**SLICE_ARGS, request_id="req-1", on_progress=lambda _p: None)

        assert result.print_time_seconds == 100
        assert progress["n"] > 1, "the poller must have been running throughout"

    @pytest.mark.asyncio
    async def test_repeated_identical_progress_does_not_count_as_alive(self):
        """The sidecar re-serves its last snapshot on every poll. Treating that
        as progress would make a stall undetectable."""

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/slice"):
                await asyncio.sleep(10)
                return httpx.Response(200, content=b"never gets here")
            return httpx.Response(200, json={"percent": 42})  # frozen

        svc = _service(handler, timeout_seconds=0.3)
        with pytest.raises(SlicerTimeoutError):
            await svc.slice_with_profiles(**SLICE_ARGS, request_id="req-2", on_progress=lambda _p: None)


class TestStalledSliceFails:
    @pytest.mark.asyncio
    async def test_silence_ends_the_wait(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/slice"):
                await asyncio.sleep(10)
                return httpx.Response(200, content=b"never gets here")
            return httpx.Response(404)  # no progress available

        svc = _service(handler, timeout_seconds=0.2)
        with pytest.raises(SlicerTimeoutError) as exc:
            await svc.slice_with_profiles(**SLICE_ARGS, request_id="req-3", on_progress=lambda _p: None)

        assert "does not report progress" in str(exc.value)

    @pytest.mark.asyncio
    async def test_timeout_is_not_reported_as_unreachable(self):
        """The whole point: this used to surface as "Slicer sidecar unreachable"."""

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/slice"):
                await asyncio.sleep(10)
            return httpx.Response(404)

        svc = _service(handler, timeout_seconds=0.2)
        with pytest.raises(SlicerTimeoutError) as exc:
            await svc.slice_with_profiles(**SLICE_ARGS, request_id="req-4", on_progress=lambda _p: None)

        assert not isinstance(exc.value, SlicerApiUnavailableError)
        assert "unreachable" not in str(exc.value)

    @pytest.mark.asyncio
    async def test_a_genuinely_unreachable_sidecar_still_says_so(self):
        """Timeouts got their own type; connection failures keep the old one."""

        async def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        svc = _service(handler, timeout_seconds=5.0)
        with pytest.raises(SlicerApiUnavailableError) as exc:
            await svc.slice_with_profiles(**SLICE_ARGS)

        assert "unreachable" in str(exc.value)


class TestStallTimeoutSetting:
    @pytest.mark.asyncio
    async def test_reads_the_configured_value(self):
        class _DB:
            pass

        async def fake_get_setting(_db, key):
            assert key == "slicer_stall_timeout_minutes"
            return "45"

        import backend.app.api.routes.settings as settings_module

        original = settings_module.get_setting
        settings_module.get_setting = fake_get_setting
        try:
            assert await get_stall_timeout_seconds(_DB()) == 45 * 60
        finally:
            settings_module.get_setting = original

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", [None, "", "not-a-number", "0", "-5"])
    async def test_falls_back_rather_than_failing_the_slice(self, stored):
        """A bad settings row must not be the reason a print doesn't happen."""

        async def fake_get_setting(_db, _key):
            return stored

        import backend.app.api.routes.settings as settings_module

        original = settings_module.get_setting
        settings_module.get_setting = fake_get_setting
        try:
            assert await get_stall_timeout_seconds(object()) == DEFAULT_SLICE_STALL_TIMEOUT_SECONDS
        finally:
            settings_module.get_setting = original

    @pytest.mark.asyncio
    async def test_a_failing_lookup_falls_back_too(self):
        async def boom(_db, _key):
            raise RuntimeError("db is down")

        import backend.app.api.routes.settings as settings_module

        original = settings_module.get_setting
        settings_module.get_setting = boom
        try:
            assert await get_stall_timeout_seconds(object()) == DEFAULT_SLICE_STALL_TIMEOUT_SECONDS
        finally:
            settings_module.get_setting = original

    def test_default_is_longer_than_the_old_fixed_ceiling(self):
        """300s was the number that broke; the new default must beat it."""
        assert DEFAULT_SLICE_STALL_TIMEOUT_SECONDS > 300
