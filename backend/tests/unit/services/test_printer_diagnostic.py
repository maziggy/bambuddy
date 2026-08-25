"""Unit tests for the connection diagnostic.

Pins the pass / fail / warn / skip contract of each check. Those statuses
drive the localized fix text the user sees when a printer won't connect,
so a status flip is a user-facing regression — each one is asserted here.
"""

import ssl
import types
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.services.printer_diagnostic import (
    _check_ftps_tls,
    _same_subnet,
    run_connection_diagnostic,
)

MOD = "backend.app.services.printer_diagnostic"


def _statuses(result):
    """Map of check id -> status for concise assertions."""
    return {c.id: c.status for c in result.checks}


def _port_probe(overrides=None):
    """Sync side_effect for _check_port. Defaults: every port reachable.

    990 is absent: the FTPS check runs a real TLS handshake through
    ``_check_ftps_tls`` rather than a bare TCP probe, and ``_Env(ftps=...)``
    drives it.
    """
    reachable = {8883: True, 322: True, 6000: True}
    reachable.update(overrides or {})

    def _probe(ip, port, timeout=3.0):
        return reachable[port]

    return _probe


def _state(
    *,
    connected=True,
    developer_mode=True,
    store_to_sdcard=True,
    sdcard=True,
    sdcard_reported=False,
    last_project_url=None,
):
    # sdcard_reported defaults False — "the printer never mentioned its card",
    # which is what every pre-#2780 test in this file assumes and what keeps
    # the storage branches out of their way.
    return types.SimpleNamespace(
        connected=connected,
        developer_mode=developer_mode,
        store_to_sdcard=store_to_sdcard,
        sdcard=sdcard,
        sdcard_reported=sdcard_reported,
        last_project_url=last_project_url,
    )


class _Env:
    """Patches the diagnostic's network/printer helpers for one run."""

    def __init__(
        self,
        *,
        ports=None,
        ftps="ok",
        in_docker=True,
        network_mode="host",
        host_ip="192.168.1.5",
        state=None,
        test_connection_success=True,
        report_messages_since_connect: int | None = 5,
        connect_error: str | None = None,
        file_found: str | None = None,
    ):
        self.ports = ports or _port_probe()
        # What the FTPS probe reports: "ok", "closed" or "no_tls" (#2780).
        self.ftps = ftps
        self.in_docker = in_docker
        self.network_mode = network_mode
        self.host_ip = host_ip
        self.state = state
        self.test_connection_success = test_connection_success
        # ``None`` means get_client returns None (e.g. pre-add flow); an int
        # means there's a client with that counter value.
        self.report_messages_since_connect = report_messages_since_connect
        # CONNACK-refusal slug the live client reports, or None when the last
        # connection attempt was never refused (#2698).
        self.connect_error = connect_error
        # Remote path the storage probe finds for the last print's file, or
        # None for "not there" (#2856). Patched unconditionally: without it
        # the internal-storage branch opens a real FTPS connection to a
        # printer that does not exist and every such test waits out the
        # socket timeout.
        self.file_found = file_found
        self.find_remote_file = AsyncMock(return_value=file_found)
        self._stack = ExitStack()

    def __enter__(self):
        manager = MagicMock()
        manager.get_status.return_value = self.state
        manager.test_connection = AsyncMock(
            return_value={
                "success": self.test_connection_success,
                "reason": None if self.test_connection_success else self.connect_error,
            }
        )
        if self.report_messages_since_connect is None:
            manager.get_client.return_value = None
        else:
            client = MagicMock()
            client.report_messages_since_connect = self.report_messages_since_connect
            client.last_connect_error = self.connect_error
            manager.get_client.return_value = client
        self._stack.enter_context(patch(f"{MOD}._check_port", new_callable=AsyncMock, side_effect=self.ports))
        self._stack.enter_context(patch(f"{MOD}._check_ftps_tls", new_callable=AsyncMock, return_value=self.ftps))
        self._stack.enter_context(patch(f"{MOD}.is_running_in_docker", return_value=self.in_docker))
        self._stack.enter_context(patch(f"{MOD}._detect_docker_network_mode", return_value=self.network_mode))
        self._stack.enter_context(patch(f"{MOD}._get_host_ip", return_value=self.host_ip))
        self._stack.enter_context(patch(f"{MOD}.printer_manager", manager))
        self._stack.enter_context(patch(f"{MOD}.find_remote_file_async", new=self.find_remote_file))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


def _printer(ip="192.168.1.50", model=None, access_code="12345678"):
    # access_code is what the storage probe needs to reach port 990 (#2856);
    # a printer without one can only be reported on, not checked.
    return types.SimpleNamespace(id=1, ip_address=ip, model=model, access_code=access_code)


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
        with _Env(ports=_port_probe({322: False}), ftps="closed", state=_state()):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        s = _statuses(result)
        # No critical failure -> warnings, not problems.
        assert result.overall == "warnings"
        assert s["port_ftps"] == "warn"
        assert s["port_rtsps"] == "warn"
        # Nothing was listening, so the message stays the generic "unblock the
        # port" one — no reason variant.
        ftps_check = next(c for c in result.checks if c.id == "port_ftps")
        assert ftps_check.params == {}

    async def test_open_port_that_cannot_negotiate_tls_says_so(self):
        """An open 990 that fails the handshake must not read as healthy.

        #2780's reporter saw port 990 green while every 3MF download died in
        the TLS handshake, so the archives arrived empty with nothing on
        screen to explain it. The reason variant selects a message that names
        a printer restart instead of sending the user to their firewall.
        """
        with _Env(ftps="no_tls", state=_state()):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="P2S"))
        assert _statuses(result)["port_ftps"] == "warn"
        ftps_check = next(c for c in result.checks if c.id == "port_ftps")
        assert ftps_check.params == {"reason": "no_tls"}
        assert result.overall == "warnings"

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


class TestAuthRejectedReason:
    """#2698: "not connected" and "credentials refused" are different answers.

    `state.connected == False` only says we have no session — the printer may
    be rebooting, at its connection limit, or refusing the access code. When
    the printer actually sent a CONNACK refusal the client records it, and the
    check surfaces it as a `params.reason` variant so the UI can name the cause
    instead of making the user guess. Without a recorded refusal the params
    stay empty and the generic text is used.
    """

    def _params(self, result):
        return next(c.params for c in result.checks if c.id == "mqtt_auth")

    async def test_recorded_refusal_surfaces_reason(self):
        with _Env(state=_state(connected=False), connect_error="auth_rejected"):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["mqtt_auth"] == "fail"
        assert self._params(result) == {"reason": "auth_rejected"}

    async def test_disconnected_without_refusal_stays_generic(self):
        with _Env(state=_state(connected=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["mqtt_auth"] == "fail"
        assert self._params(result) == {}

    async def test_unknown_slug_falls_back_to_generic(self):
        # `refused` has no dedicated message — degrade to the plain fail text
        # rather than asking the frontend for a key that doesn't exist.
        with _Env(state=_state(connected=False), connect_error="refused"):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert self._params(result) == {}

    async def test_connected_printer_carries_no_reason(self):
        with _Env(state=_state(connected=True), connect_error="auth_rejected"):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer())
        assert _statuses(result)["mqtt_auth"] == "pass"
        assert self._params(result) == {}

    async def test_pre_add_probe_surfaces_reason(self):
        with _Env(test_connection_success=False, connect_error="auth_rejected"):
            result = await run_connection_diagnostic("192.168.1.50", serial_number="01P", access_code="wrong")
        assert _statuses(result)["mqtt_auth"] == "fail"
        assert self._params(result) == {"reason": "auth_rejected"}


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

    async def test_skips_on_p1s_no_reachable_toggle(self):
        # #2524: P1S HAS a MicroSD slot (so has_external_storage is True and
        # the check proceeds), but current P1 firmware never publishes the
        # capability that renders the toggle in Bambu Studio and the P1S has
        # no screen — store_to_sdcard is stuck False with no way to fix it.
        # Report an informational skip (with a reason the UI explains), not a
        # permanently-unresolvable fail; overall must not escalate.
        with _Env(state=_state(store_to_sdcard=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="P1S"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "skip"
        assert check.params == {"reason": "unsupported_model"}
        assert result.overall == "ok"

    async def test_skips_on_p1p_no_reachable_toggle(self):
        with _Env(state=_state(store_to_sdcard=False)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="P1P"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "skip"
        assert check.params == {"reason": "unsupported_model"}

    async def test_p1s_still_passes_when_store_to_sdcard_true(self):
        # If a P1S somehow reports the option ON, respect it — pass, don't
        # mask it as an unsupported-model skip.
        with _Env(state=_state(store_to_sdcard=True)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="P1S"))
        assert _statuses(result)["external_storage"] == "pass"

    # --- #2780: the toggle being on is not the same as it working ---------

    async def test_fails_when_the_toggle_is_on_but_the_slot_is_empty(self):
        """#2780's H2C reported `store_to_sdcard` set and `sdcard` False for
        three solid weeks. The check called that a clean pass while every one
        of its 23 archives came out blank -- the toggle can be on and still
        achieve nothing with nothing in the slot.
        """
        with _Env(state=_state(store_to_sdcard=True, sdcard=False, sdcard_reported=True)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2C"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "fail"
        assert check.params == {"reason": "no_media"}
        assert result.overall == "problems"

    async def test_warns_when_the_printer_kept_the_print_internally(self):
        """Toggle on, card in, the printer used internal storage -- and the
        probe confirmed the file is not on the card either. A pass would be a
        lie and a fail would be unresolvable, so it warns."""
        state = _state(
            store_to_sdcard=True,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="brtc://emmc/Benchy.gcode.3mf",
        )
        with _Env(state=state) as env:
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2C"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "warn"
        assert check.params == {"reason": "internal_storage"}
        assert result.overall == "warnings"
        # It asked before it warned, with the name the dispatch gave.
        paths = env.find_remote_file.await_args.args[2]
        assert paths[:2] == ["/Benchy.gcode.3mf", "/cache/Benchy.gcode.3mf"]

    async def test_a_print_of_a_file_already_on_the_printer_gets_its_own_reason(self):
        """Same verdict, different advice (#1820).

        A print started from the printer's screen reaches this branch too, now
        that the report topic is read. The internal-storage wording tells the
        operator to use Send with External selected -- a dialog nobody opened,
        for a print where nothing was sent at all.
        """
        state = _state(
            store_to_sdcard=True,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="file:///userdata/model/history/JOB_A.gcode.3mf",
        )
        with _Env(state=state) as env:
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2S"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "warn"
        assert check.params == {"reason": "internal_history"}
        # Still asked first: an H2S keeps recently used jobs under /cache.
        paths = env.find_remote_file.await_args.args[2]
        assert paths[:2] == ["/JOB_A.gcode.3mf", "/cache/JOB_A.gcode.3mf"]

    async def test_a_screen_started_print_whose_file_is_there_still_passes(self):
        """The probe outranks the reason for this URL exactly as for the other
        one -- that copy under /cache is what archives the print in full."""
        state = _state(
            store_to_sdcard=True,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="file:///userdata/model/history/JOB_A.gcode.3mf",
        )
        with _Env(state=state, file_found="/cache/JOB_A.gcode.3mf"):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2S"))
        assert _statuses(result)["external_storage"] == "pass"

    async def test_the_internal_storage_warning_yields_to_the_file_being_there(self):
        """#2856. The URL says where the printer *put* the file, not whether
        port 990 can serve it: an H2D with a card in reports `brtc://emmc` and
        then hands the same file over from /cache. Warning that reader's
        archives are unreachable -- while they are demonstrably complete --
        sends them chasing a setting that is already right.
        """
        state = _state(
            store_to_sdcard=True,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="brtc://emmc/Benchy.gcode.3mf",
        )
        with _Env(state=state, file_found="/cache/Benchy.gcode.3mf"):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2D"))
        assert _statuses(result)["external_storage"] == "pass"

    async def test_the_file_being_there_outranks_the_toggle(self):
        """Same printer with the toggle off. The check exists to say whether
        Bambuddy can read the print file, and the probe just proved it can --
        telling this user to switch something on would be advice for a problem
        they do not have.
        """
        state = _state(
            store_to_sdcard=False,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="brtc://emmc/Benchy.gcode.3mf",
        )
        with _Env(state=state, file_found="/cache/Benchy.gcode.3mf"):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2D"))
        assert _statuses(result)["external_storage"] == "pass"

    async def test_no_probe_when_the_file_service_is_not_answering(self):
        """The probe is one FTPS connection, and #2780's printer refused those
        by the hundred. If port 990 is not answering there is nothing to learn
        and the warning stands on the URL alone."""
        state = _state(
            store_to_sdcard=True,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="brtc://emmc/Benchy.gcode.3mf",
        )
        with _Env(state=state, ftps="no_tls") as env:
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2C"))
        env.find_remote_file.assert_not_awaited()
        assert _statuses(result)["external_storage"] == "warn"

    async def test_a_probe_that_cannot_run_leaves_the_warning_in_place(self):
        """ "Could not check" must not read as "the file is fine" -- the check
        keeps warning, which is the recoverable direction for a warn."""
        state = _state(
            store_to_sdcard=True,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="brtc://emmc/Benchy.gcode.3mf",
        )
        with _Env(state=state) as env:
            env.find_remote_file.side_effect = OSError("connection reset")
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2C"))
        assert _statuses(result)["external_storage"] == "warn"

    async def test_an_external_storage_dispatch_still_passes(self):
        """The regression guard: an X1C dispatch says `ftp://`, and that must
        read exactly as it did before any of this existed."""
        state = _state(
            store_to_sdcard=True,
            sdcard=True,
            sdcard_reported=True,
            last_project_url="ftp://Benchy.gcode.3mf",
        )
        with _Env(state=state):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="X1C"))
        assert _statuses(result)["external_storage"] == "pass"

    async def test_a_p1_series_keeps_its_non_nagging_skip_with_no_card(self):
        """#2524 outranks #2780 here. Current P1 firmware exposes no way to
        turn the option on, so "insert a card" would promise a fix that
        inserting a card does not deliver -- the unresolvable-fail nagging
        that #2524 removed, back under a different label.
        """
        with _Env(state=_state(store_to_sdcard=False, sdcard=False, sdcard_reported=True)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="P1S"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "skip"
        assert check.params == {"reason": "unsupported_model"}
        assert result.overall == "ok"

    async def test_a_model_with_a_reachable_toggle_does_report_the_empty_slot(self):
        """The counterpart: on an X1C the advice is actionable, so give it."""
        with _Env(state=_state(store_to_sdcard=False, sdcard=False, sdcard_reported=True)):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="X1C"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "fail"
        assert check.params == {"reason": "no_media"}

    async def test_an_empty_slot_outranks_the_internal_storage_warning(self):
        """Both apply on a stickless H2C. "Put a card in" is the one the
        operator can act on, so it must not be masked by the softer warning.
        """
        state = _state(
            store_to_sdcard=True,
            sdcard=False,
            sdcard_reported=True,
            last_project_url="brtc://emmc/Benchy.gcode.3mf",
        )
        with _Env(state=state):
            result = await run_connection_diagnostic("192.168.1.50", printer=_printer(model="H2C"))
        check = next(c for c in result.checks if c.id == "external_storage")
        assert check.status == "fail"
        assert check.params == {"reason": "no_media"}


class TestFtpsTlsProbe:
    """The FTPS probe must reach the handshake, not stop at the TCP accept.

    #2780: a printer whose file service stops answering with TLS still
    accepts the connection on 990, so the old bare TCP probe reported it
    green while every archive came back empty.
    """

    async def test_completed_handshake_is_ok(self):
        writer = MagicMock()
        writer.wait_closed = AsyncMock()
        with patch(f"{MOD}.asyncio.open_connection", new_callable=AsyncMock, return_value=(MagicMock(), writer)):
            assert await _check_ftps_tls("192.168.1.50", "X1C") == "ok"
        writer.close.assert_called_once()

    async def test_handshake_failure_on_an_open_port_is_no_tls(self):
        with patch(
            f"{MOD}.asyncio.open_connection",
            new_callable=AsyncMock,
            side_effect=ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number"),
        ):
            assert await _check_ftps_tls("192.168.1.50", "P2S") == "no_tls"

    async def test_refused_connection_is_closed(self):
        with patch(f"{MOD}.asyncio.open_connection", new_callable=AsyncMock, side_effect=ConnectionRefusedError):
            assert await _check_ftps_tls("192.168.1.50", "X1C") == "closed"

    async def test_timeout_is_closed_not_no_tls(self):
        # A printer that is switched off never gets far enough to say anything
        # about TLS — that has to stay the generic "port unreachable" advice.
        with patch(f"{MOD}.asyncio.open_connection", new_callable=AsyncMock, side_effect=TimeoutError):
            assert await _check_ftps_tls("192.168.1.50", "X1C") == "closed"

    async def test_probe_mirrors_the_model_tls_cap(self):
        """The probe must negotiate exactly what the FTP client negotiates.

        A P2S is pinned to TLS 1.2 by its ftp_profiles entry; probing it on a
        context that also offers 1.3 could pass where the real transfer fails
        (or the reverse), which is the class of false green this check exists
        to remove.
        """
        contexts = []

        async def _capture(host, port, ssl=None):
            contexts.append(ssl)
            writer = MagicMock()
            writer.wait_closed = AsyncMock()
            return MagicMock(), writer

        with patch(f"{MOD}.asyncio.open_connection", new=_capture):
            await _check_ftps_tls("192.168.1.50", "P2S")
            await _check_ftps_tls("192.168.1.50", "X1C")

        capped, uncapped = contexts
        assert capped.maximum_version == ssl.TLSVersion.TLSv1_2
        assert capped.minimum_version == ssl.TLSVersion.TLSv1_2
        assert uncapped.maximum_version != ssl.TLSVersion.TLSv1_2
        assert uncapped.minimum_version == ssl.TLSVersion.TLSv1_2
