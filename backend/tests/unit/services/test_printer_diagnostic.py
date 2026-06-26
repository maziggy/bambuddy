"""Unit tests for the connection diagnostic.

Pins the pass / fail / warn / skip contract of each check. Those statuses
drive the localized fix text the user sees when a printer won't connect,
so a status flip is a user-facing regression — each one is asserted here.
"""

import types
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.services.printer_diagnostic import _same_subnet, run_connection_diagnostic

MOD = "backend.app.services.printer_diagnostic"


def _statuses(result):
    """Map of check id -> status for concise assertions."""
    return {c.id: c.status for c in result.checks}


def _port_probe(overrides=None):
    """Sync side_effect for _check_port. Defaults: every port reachable."""
    reachable = {8883: True, 990: True, 322: True, 6000: True}
    reachable.update(overrides or {})

    def _probe(ip, port, timeout=3.0):
        return reachable[port]

    return _probe


def _state(*, connected=True, developer_mode=True, store_to_sdcard=True):
    return types.SimpleNamespace(
        connected=connected,
        developer_mode=developer_mode,
        store_to_sdcard=store_to_sdcard,
    )


class _Env:
    """Patches the diagnostic's network/printer helpers for one run."""

    def __init__(
        self,
        *,
        ports=None,
        in_docker=True,
        network_mode="host",
        host_ip="192.168.1.5",
        state=None,
        test_connection_success=True,
        report_messages_since_connect: int | None = 5,
    ):
        self.ports = ports or _port_probe()
        self.in_docker = in_docker
        self.network_mode = network_mode
        self.host_ip = host_ip
        self.state = state
        self.test_connection_success = test_connection_success
        # ``None`` means get_client returns None (e.g. pre-add flow); an int
        # means there's a client with that counter value.
        self.report_messages_since_connect = report_messages_since_connect
        self._stack = ExitStack()

    def __enter__(self):
        manager = MagicMock()
        manager.get_status.return_value = self.state
        manager.test_connection = AsyncMock(return_value={"success": self.test_connection_success})
        if self.report_messages_since_connect is None:
            manager.get_client.return_value = None
        else:
            client = MagicMock()
            client.report_messages_since_connect = self.report_messages_since_connect
            manager.get_client.return_value = client
        self._stack.enter_context(patch(f"{MOD}._check_port", new_callable=AsyncMock, side_effect=self.ports))
        self._stack.enter_context(patch(f"{MOD}.is_running_in_docker", return_value=self.in_docker))
        self._stack.enter_context(patch(f"{MOD}._detect_docker_network_mode", return_value=self.network_mode))
        self._stack.enter_context(patch(f"{MOD}._get_host_ip", return_value=self.host_ip))
        self._stack.enter_context(patch(f"{MOD}.printer_manager", manager))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


def _printer(ip="192.168.1.50", model=None):
    return types.SimpleNamespace(id=1, ip_address=ip, model=model)


class TestSameSubnet:
    def test_same_24(self):
        assert _same_subnet("192.168.1.10", "192.168.1.200") is True

    def test_different_24(self):
        assert _same_subnet("192.168.1.10", "192.168.2.10") is False

    def test_hostname_undeterminable(self):
        assert _same_subnet("printer.local", "192.168.1.10") is None

    def test_ipv6_undeterminable(self):
        assert _same_subnet("fe80::1", "192.168.1.10") is None


class TestExistingPrinter:
    async def test_all_healthy(self):
        with _Env(
            state=_state(connected=True, developer_mode=True, store_to_sdcard=True),
            report_messages_since_connect=42,
        ):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        assert result.overall == "ok"
        assert s == {
            "port_mqtt": "pass",
            "port_ftps": "pass",
            "port_rtsps": "pass",
            "network_mode": "pass",
            "subnet": "pass",
            "external_storage": "pass",
            "mqtt_auth": "pass",
            "developer_mode": "pass",
            "printer_publishing": "pass",
        }

    async def test_mqtt_port_unreachable_is_a_problem(self):
        with _Env(ports=_port_probe({8883: False}), state=_state()):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        assert result.overall == "problems"
        assert s["port_mqtt"] == "fail"
        # Auth can't be judged when the broker port itself is closed.
        assert s["mqtt_auth"] == "skip"

    async def test_ftps_and_rtsps_only_warn(self):
        with _Env(ports=_port_probe({990: False, 322: False}), state=_state()):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        # No critical failure -> warnings, not problems.
        assert result.overall == "warnings"
        assert s["port_ftps"] == "warn"
        assert s["port_rtsps"] == "warn"

    async def test_a1_mini_uses_chamber_image_camera_port(self):
        # A1/P1-family printers use the chamber-image camera protocol on 6000,
        # not RTSPS on 322. A closed 322 must not create a false camera warning.
        with _Env(ports=_port_probe({322: False, 6000: True}), state=_state()):
            result = await run_connection_diagnostic(
                "192.168.1.50",
                printer=_printer(model="A1 Mini"),
            )
        assert _statuses(result)["port_rtsps"] == "pass"
        camera_check = next(c for c in result.checks if c.id == "port_rtsps")
        assert camera_check.params == {"port": 6000, "protocol": "Chamber Image"}

    async def test_rtsp_models_still_probe_rtsps_port(self):
        with _Env(ports=_port_probe({322: False, 6000: True}), state=_state()):
            result = await run_connection_diagnostic(
                "192.168.1.50",
                printer=_printer(model="X1C"),
            )
        assert _statuses(result)["port_rtsps"] == "warn"
        camera_check = next(c for c in result.checks if c.id == "port_rtsps")
        assert camera_check.params == {"port": 322, "protocol": "RTSPS"}

    async def test_developer_mode_off_is_a_problem(self):
        with _Env(state=_state(connected=True, developer_mode=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        assert s["developer_mode"] == "fail"
        assert result.overall == "problems"

    async def test_developer_mode_skipped_when_disconnected(self):
        # No live MQTT connection -> developer_mode can't be read.
        with _Env(state=_state(connected=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        assert s["developer_mode"] == "skip"
        # Reachable port but no connection -> credential failure class.
        assert s["mqtt_auth"] == "fail"
        # Can't observe report messages without a connection.
        assert s["printer_publishing"] == "skip"

    async def test_bridge_mode_warns_and_skips_subnet(self):
        with _Env(network_mode="bridge", state=_state()):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        assert s["network_mode"] == "warn"
        # Container IP isn't the host IP in bridge mode -> subnet check is meaningless.
        assert s["subnet"] == "skip"

    async def test_network_mode_skipped_outside_docker(self):
        with _Env(in_docker=False, state=_state()):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["network_mode"] == "skip"

    async def test_different_subnet_warns(self):
        with _Env(host_ip="10.0.0.5", state=_state()):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["subnet"] == "warn"

    async def test_printer_publishing_passes_when_reports_seen(self):
        # Counter > 0 means the printer is publishing on the report topic.
        with _Env(state=_state(), report_messages_since_connect=1):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["printer_publishing"] == "pass"

    async def test_printer_publishing_fails_when_zero_reports_after_wait(self):
        # Counter stays at 0 across the wait window — printer never published.
        # Tiny wait_for_publish_seconds keeps the test sub-second.
        with _Env(state=_state(), report_messages_since_connect=0):
            result = await run_connection_diagnostic(
                "192.168.1.50",
                printer=_printer(),
                wait_for_publish_seconds=0.05,
            )
        s = _statuses(result)
        assert s["printer_publishing"] == "fail"
        # Overall escalates because fail is present.
        assert result.overall == "problems"
        # The check exposes the wait budget so the UI can render a countdown.
        params = next(c.params for c in result.checks if c.id == "printer_publishing")
        assert params == {"max_wait_seconds": 0.05}

    async def test_printer_publishing_skips_when_disconnected(self):
        # No live MQTT connection -> can't observe report messages.
        with _Env(state=_state(connected=False), report_messages_since_connect=0):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["printer_publishing"] == "skip"

    async def test_printer_publishing_skips_when_no_client(self):
        # State says connected but printer_manager has no client object
        # (race between client teardown and a fresh diagnostic request).
        with _Env(state=_state(), report_messages_since_connect=None):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["printer_publishing"] == "skip"

    async def test_printer_publishing_no_wait_returns_instantly_on_zero(self):
        # Default wait is 0 — instant pass/fail without polling. Used by the
        # support-package code path so bundling stays fast.
        with _Env(state=_state(), report_messages_since_connect=0):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        assert s["printer_publishing"] == "fail"
        params = next(c.params for c in result.checks if c.id == "printer_publishing")
        # No wait -> no max_wait_seconds param surfaced to the UI.
        assert params == {}


class TestPreAddFlow:
    async def test_bad_credentials_fail_mqtt_auth(self):
        with _Env(test_connection_success=False):
            result = await run_connection_diagnostic("192.168.1.50", serial_number="01P", access_code="wrong")
        s = _statuses(result)
        assert s["mqtt_auth"] == "fail"
        # No saved printer -> developer mode can't be read.
        assert s["developer_mode"] == "skip"

    async def test_good_credentials_pass_mqtt_auth(self):
        with _Env(test_connection_success=True):
            result = await run_connection_diagnostic("192.168.1.50", serial_number="01P", access_code="right")
        assert _statuses(result)["mqtt_auth"] == "pass"

    async def test_no_credentials_skips_mqtt_auth(self):
        with _Env():
            result = await run_connection_diagnostic("192.168.1.50")
        assert _statuses(result)["mqtt_auth"] == "skip"


class TestExternalStorageCheck:
    """Install step 4 — "Store sent files on external storage".

    Detected via ``state.store_to_sdcard`` (parsed from MQTT push_status
    ``home_flag`` bit 11). Only catches the printer-side variant of the
    setting on newer firmware (P2S 01.02 / Studio 2.6+) — the older
    slicer-side variant is undetectable from outside the slicer and is
    covered separately by the no-3MF archive-fallback banner.
    """

    async def test_passes_when_store_to_sdcard_true(self):
        with _Env(state=_state(store_to_sdcard=True)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["external_storage"] == "pass"

    async def test_fails_when_store_to_sdcard_false(self):
        # Bit 11 reported as 0 -> printer-side toggle is off. Overall
        # escalates to "problems" because a fail is present.
        with _Env(state=_state(store_to_sdcard=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["external_storage"] == "fail"
        assert result.overall == "problems"

    async def test_skips_when_disconnected(self):
        # State exists (we have a saved printer) but the MQTT connection
        # dropped, so the latest store_to_sdcard value can't be trusted.
        with _Env(state=_state(connected=False, store_to_sdcard=True)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["external_storage"] == "skip"

    async def test_skips_pre_add_flow(self):
        # No saved printer -> no state -> nothing to read. The check has
        # to skip; pre-add can't probe this without a live MQTT session.
        with _Env():
            result = await run_connection_diagnostic(
                "192.168.1.50",
                serial_number="01P",
                access_code="probe-code",
            )
        assert _statuses(result)["external_storage"] == "skip"

    async def test_skips_when_field_missing(self):
        # State exists and is connected but store_to_sdcard was never
        # populated (firmware that doesn't push home_flag). Skip rather
        # than fabricate a False from a missing field.
        bare = types.SimpleNamespace(connected=True, developer_mode=True)
        with _Env(state=bare):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["external_storage"] == "skip"

    async def test_skips_on_a1_no_external_storage_slot(self):
        # Regression for #1703: A1 and A1 Mini ship without a MicroSD slot
        # at all, so home_flag bit 11 is never set and a naive read would
        # report `fail` for every A1-series user. The model-aware skip
        # branch suppresses that — and the overall result must NOT escalate
        # to "problems" purely because of this check.
        with _Env(state=_state(store_to_sdcard=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="A1"))
        assert _statuses(result)["external_storage"] == "skip"
        assert result.overall == "ok"

    async def test_skips_on_a1_mini_no_external_storage_slot(self):
        with _Env(state=_state(store_to_sdcard=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="A1 Mini"))
        assert _statuses(result)["external_storage"] == "skip"

    async def test_still_fails_on_x1c_when_toggle_off(self):
        # Sanity: the model-aware skip MUST NOT silently let X1C-class
        # printers off the hook. The store_to_sdcard=False path is the
        # one real bit of value this check provides for those models.
        with _Env(state=_state(store_to_sdcard=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="X1C"))
        assert _statuses(result)["external_storage"] == "fail"
