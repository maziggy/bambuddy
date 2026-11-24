"""A VP whose services cannot bind is restarted until they can (#3687).

When a VP's bind address is not assigned yet as Bambuddy starts (a DHCP
interface still waiting for its lease), FTP, MQTT, Bind and SSDP cannot bind
and end at once. The VP was still logged as started, and nothing tried again
until it was turned off and on.
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.virtual_printer import manager as vp_manager
from backend.app.services.virtual_printer.manager import VirtualPrinterInstance, VirtualPrinterManager

# TEST-NET-1 (RFC 5737): never assigned to an interface here.
UNASSIGNED_IP = "192.0.2.10"


def _instance(tmp_path, *, mode: str = "library", bind_ip: str = UNASSIGNED_IP, **kwargs) -> VirtualPrinterInstance:
    return VirtualPrinterInstance(
        vp_id=7,
        name="VP-1",
        mode=mode,
        model="BL-P001",
        access_code="12345678",
        serial_suffix="391800007",
        bind_ip=bind_ip,
        base_dir=tmp_path,
        **kwargs,
    )


async def _stop(instance: VirtualPrinterInstance) -> None:
    if instance.is_proxy:
        await instance.stop_proxy()
    else:
        await instance.stop_server()


class _AddressState:
    """Whether the fake services' bind address is assigned yet."""

    assigned = False


def _fake_services(monkeypatch) -> _AddressState:
    """FTP, MQTT, Bind and SSDP that bind only once the address is assigned.

    Like the real ones: a bind failure is logged by the service, which then
    returns and so ends its task; a bound service sets ``ready`` and runs
    until it is stopped.
    """
    state = _AddressState()

    class FakeService:
        def __init__(self, *args, **kwargs):
            self.ready = asyncio.Event()
            self._stopped = asyncio.Event()

        async def start(self):
            if not state.assigned:
                return
            self.ready.set()
            await self._stopped.wait()

        async def stop(self):
            self._stopped.set()

        def set_bridge(self, bridge):
            pass

    for name in ("VirtualPrinterFTPServer", "SimpleMQTTServer", "BindServer", "VirtualPrinterSSDPServer"):
        monkeypatch.setattr(vp_manager, name, FakeService)
    return state


def _no_certs(monkeypatch, instance: VirtualPrinterInstance) -> None:
    monkeypatch.setattr(
        instance,
        "_resolve_cert_and_advertise",
        lambda: (Path("/tmp/cert.pem"), Path("/tmp/key.pem"), instance.bind_ip),  # nosec B108
    )


class TestStartServerReportsFailure:
    @pytest.mark.asyncio
    async def test_real_services_on_an_unassigned_address_are_not_started(self, tmp_path, caplog):
        """The reporter's case, with the real services and certificates."""
        instance = _instance(tmp_path)
        caplog.set_level(logging.INFO)
        try:
            loop = asyncio.get_running_loop()
            begun = loop.time()
            started = await instance.start_server()
            elapsed = loop.time() - begun
        finally:
            await _stop(instance)

        assert started is False
        # Settled by the services ending, not by the 5 s readiness timeout.
        assert elapsed < 4.0
        assert "Server-mode services started" not in caplog.text
        assert "[VP VP-1] Not started on 192.0.2.10: FTP, MQTT, Bind, SSDP could not start" in caplog.text
        # Each service names the VP in its own error, so with several VPs the
        # log says which one failed.
        for service_error in (
            "FTP server error",
            "MQTT server error",
            "Bind server: could not bind",
            "SSDP server error",
        ):
            assert f"[VP-1] {service_error}" in caplog.text

    @pytest.mark.asyncio
    async def test_bound_services_are_started(self, tmp_path, monkeypatch, caplog):
        _fake_services(monkeypatch).assigned = True
        instance = _instance(tmp_path)
        _no_certs(monkeypatch, instance)
        caplog.set_level(logging.INFO)
        try:
            assert await instance.start_server() is True
            assert instance.ended_services() == []
        finally:
            await _stop(instance)
        assert "[VP VP-1] Server-mode services started on 192.0.2.10" in caplog.text
        assert "Not started" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_failed_camera_bridge_alone_is_not_a_failed_vp(self, tmp_path, monkeypatch):
        """The VP works without its camera, and restarting it would not free
        a camera port another program holds."""
        _fake_services(monkeypatch).assigned = True

        class FailingCamera:
            def __init__(self, **kwargs):
                pass

            async def start(self):
                return  # could not bind, like a taken port 322

            async def stop(self):
                pass

        monkeypatch.setattr(vp_manager, "CameraBridge", FailingCamera)
        client = MagicMock(ip_address="192.0.2.20", model="X1C")
        printer_manager = MagicMock()
        printer_manager.get_client.return_value = client
        mqtt_bridge = MagicMock(start=AsyncMock(), stop=AsyncMock())
        monkeypatch.setattr(vp_manager, "MQTTBridge", lambda **kwargs: mqtt_bridge)
        instance = _instance(tmp_path, target_printer_id=3, printer_manager=printer_manager)
        _no_certs(monkeypatch, instance)
        try:
            assert await instance.start_server() is True
            await asyncio.sleep(0)
            assert instance.ended_services() == []
            assert instance.is_running is False  # the diagnostic still reports the camera
        finally:
            await _stop(instance)


class TestProxyEndedServices:
    @pytest.mark.asyncio
    async def test_a_proxy_whose_listeners_all_ended_is_reported(self, tmp_path, monkeypatch):
        class EndedProxy:
            def __init__(self, **kwargs):
                pass

            async def start(self):
                return  # every listener failed to bind

            async def stop(self):
                pass

        class RunningSSDP:
            def __init__(self, **kwargs):
                self._stopped = asyncio.Event()

            async def start(self):
                await self._stopped.wait()

            async def stop(self):
                self._stopped.set()

        monkeypatch.setattr(vp_manager, "SlicerProxyManager", EndedProxy)
        monkeypatch.setattr(vp_manager, "VirtualPrinterSSDPServer", RunningSSDP)
        instance = _instance(tmp_path, mode="proxy", target_printer_ip="192.0.2.20")
        _no_certs(monkeypatch, instance)
        try:
            await instance.start_proxy()
            await asyncio.sleep(0.01)
            assert instance.ended_services() == ["Proxy"]
        finally:
            await _stop(instance)
        assert instance.ended_services() == []


class _FakeInstance:
    def __init__(self, name: str, ended: list[str]):
        self.name = name
        self.ended = ended
        self.restart = AsyncMock()
        self.is_proxy = False
        self.stop_server = AsyncMock()

    def ended_services(self) -> list[str]:
        return list(self.ended)


class TestSupervisorBackoff:
    @pytest.mark.asyncio
    async def test_restarts_at_once_then_backs_off_up_to_the_cap(self, caplog):
        manager = VirtualPrinterManager()
        down = _FakeInstance("VP-1", ["FTP", "MQTT"])
        manager._instances[1] = down
        caplog.set_level(logging.INFO)

        restarts_at = []
        for now in range(0, 1300, 5):
            before = down.restart.await_count
            await manager._restart_ended_locked(float(now))
            if down.restart.await_count > before:
                restarts_at.append(now)

        gaps = [b - a for a, b in zip(restarts_at, restarts_at[1:], strict=False)]
        assert restarts_at[0] == 0
        assert gaps[:6] == [10, 20, 40, 80, 160, 300]
        assert set(gaps[6:]) == {300}
        # One line when it goes down, none per attempt.
        assert caplog.text.count("[VP VP-1] FTP, MQTT stopped, the VP is unreachable") == 1

    @pytest.mark.asyncio
    async def test_waits_out_the_backoff(self):
        manager = VirtualPrinterManager()
        down = _FakeInstance("VP-1", ["Bind"])
        manager._instances[1] = down
        await manager._restart_ended_locked(100.0)
        await manager._restart_ended_locked(109.0)
        assert down.restart.await_count == 1
        await manager._restart_ended_locked(110.0)
        assert down.restart.await_count == 2

    @pytest.mark.asyncio
    async def test_recovery_is_logged_once_and_resets_the_backoff(self, caplog):
        manager = VirtualPrinterManager()
        vp = _FakeInstance("VP-1", ["SSDP"])
        manager._instances[1] = vp
        caplog.set_level(logging.INFO)
        await manager._restart_ended_locked(0.0)
        await manager._restart_ended_locked(10.0)
        vp.ended = []
        await manager._restart_ended_locked(30.0)
        await manager._restart_ended_locked(40.0)
        assert caplog.text.count("[VP VP-1] Running again") == 1
        assert 1 not in manager._restart_backoff

        # A later failure starts from the shortest delay again.
        vp.ended = ["SSDP"]
        await manager._restart_ended_locked(50.0)
        assert vp.restart.await_count == 3
        assert manager._restart_backoff[1] == (manager.RESTART_BACKOFF_MIN, 50.0 + manager.RESTART_BACKOFF_MIN)

    @pytest.mark.asyncio
    async def test_a_running_vp_is_left_alone(self):
        manager = VirtualPrinterManager()
        up = _FakeInstance("VP-1", [])
        manager._instances[1] = up
        await manager._restart_ended_locked(0.0)
        assert up.restart.await_count == 0

    @pytest.mark.asyncio
    async def test_a_removed_vp_is_not_restarted_and_forgets_its_backoff(self):
        manager = VirtualPrinterManager()
        down = _FakeInstance("VP-1", ["FTP"])
        manager._instances[1] = down
        await manager._restart_ended_locked(0.0)
        await manager.remove_instance(1)  # turned off, or deleted
        assert manager._restart_backoff == {}
        await manager._restart_ended_locked(1000.0)
        assert down.restart.await_count == 1

    @pytest.mark.asyncio
    async def test_a_failing_restart_does_not_stop_the_others(self, caplog):
        manager = VirtualPrinterManager()
        broken = _FakeInstance("VP-1", ["FTP"])
        broken.restart.side_effect = RuntimeError("boom")
        other = _FakeInstance("VP-2", ["MQTT"])
        manager._instances[1] = broken
        manager._instances[2] = other
        await manager._restart_ended_locked(0.0)
        assert other.restart.await_count == 1
        assert "[VP VP-1] Restart failed" in caplog.text


class TestRecoveryEndToEnd:
    @pytest.mark.asyncio
    async def test_the_vp_comes_up_once_its_address_is_assigned(self, tmp_path, monkeypatch, caplog):
        address = _fake_services(monkeypatch)
        instance = _instance(tmp_path)
        _no_certs(monkeypatch, instance)
        manager = VirtualPrinterManager()
        manager._instances[instance.id] = instance
        caplog.set_level(logging.INFO)
        try:
            assert await instance.start_server() is False
            await manager._restart_ended_locked(0.0)  # still no address
            assert instance.ended_services() == ["FTP", "MQTT", "Bind", "SSDP"]

            address.assigned = True  # the DHCP lease arrives
            await manager._restart_ended_locked(10.0)
            assert instance.ended_services() == []
            assert instance.is_running is True
            await manager._restart_ended_locked(20.0)
        finally:
            await _stop(instance)
        assert caplog.text.count("[VP VP-1] Running again") == 1
        assert "[VP VP-1] Server-mode services started on 192.0.2.10" in caplog.text


class TestSupervisorLifecycle:
    @pytest.mark.asyncio
    async def test_the_supervisor_checks_under_the_sync_lock_and_stops_with_the_rest(self, monkeypatch):
        manager = VirtualPrinterManager()
        monkeypatch.setattr(manager, "SUPERVISE_INTERVAL", 0.01)
        checked = asyncio.Event()
        held = []

        async def check(now):
            held.append(manager._sync_lock.locked())
            checked.set()

        monkeypatch.setattr(manager, "_restart_ended_locked", check)
        manager.start_supervisor()
        task = manager._supervisor_task
        manager.start_supervisor()  # a second call does not start a second loop
        assert manager._supervisor_task is task
        await asyncio.wait_for(checked.wait(), timeout=1.0)
        assert held and all(held)

        await manager.stop_all()
        assert task.done()
        assert manager._supervisor_task is None

    @pytest.mark.asyncio
    async def test_a_failing_check_keeps_the_supervisor_running(self, monkeypatch):
        manager = VirtualPrinterManager()
        monkeypatch.setattr(manager, "SUPERVISE_INTERVAL", 0.01)
        calls = 0

        async def check(now):
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        monkeypatch.setattr(manager, "_restart_ended_locked", check)
        manager.start_supervisor()
        for _ in range(100):
            if calls >= 2:
                break
            await asyncio.sleep(0.01)
        assert calls >= 2
        await manager.stop_all()
