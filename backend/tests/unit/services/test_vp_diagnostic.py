"""Unit tests for the virtual printer setup diagnostic."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.virtual_printer.certificate import CertificateService
from backend.app.services.virtual_printer.diagnostic import run_vp_diagnostic

_DIAG = "backend.app.services.virtual_printer.diagnostic._check_port"
_FIND_IFACE = "backend.app.services.network_utils.find_interface_for_ip"


def _vp(**overrides):
    """A virtual-printer DB row stand-in with sensible healthy defaults."""
    base = {
        "id": 1,
        "name": "Test VP",
        "mode": "archive",
        "enabled": True,
        "bind_ip": "192.168.1.50",
        "access_code": "12345678",
        "target_printer_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeInstance:
    """Minimal VirtualPrinterInstance stand-in for the diagnostic."""

    def __init__(self, running=True, cert_exists=True, proxy_status=None):
        self.is_running = running
        self._cert_exists = cert_exists
        self._proxy_status = proxy_status

    @property
    def cert_path(self):
        return SimpleNamespace(exists=lambda: self._cert_exists)

    def get_status(self):
        return {"proxy": self._proxy_status} if self._proxy_status is not None else {}


def _checks(result):
    return {c.id: c.status for c in result.checks}


class TestRunVpDiagnostic:
    @pytest.mark.asyncio
    async def test_disabled_vp_reports_problems(self):
        """A disabled VP fails the 'enabled' check; running/port checks skip."""
        result = await run_vp_diagnostic(_vp(enabled=False, bind_ip=None, access_code=None), None)
        c = _checks(result)
        assert result.overall == "problems"
        assert c["enabled"] == "fail"
        assert c["running"] == "skip"
        assert c["port_ftps"] == c["port_mqtt"] == c["port_bind"] == "skip"
        assert c["certificate"] == "skip"

    @pytest.mark.asyncio
    async def test_running_server_vp_all_pass(self):
        """Enabled + running + every port listening + cert present → overall ok."""
        with (
            patch(_DIAG, AsyncMock(return_value=True)),
            patch(_FIND_IFACE, return_value={"name": "eth0", "ip": "192.168.1.50"}),
        ):
            result = await run_vp_diagnostic(_vp(), _FakeInstance())
        c = _checks(result)
        assert result.overall == "ok"
        assert c["enabled"] == "pass"
        assert c["running"] == "pass"
        assert c["bind_interface"] == "pass"
        assert c["access_code"] == "pass"
        assert c["target_printer"] == "skip"  # not proxy mode
        assert c["port_ftps"] == c["port_mqtt"] == c["port_bind"] == "pass"
        assert c["certificate"] == "pass"

    @pytest.mark.asyncio
    async def test_port_not_listening_is_a_problem(self):
        """A service object can exist while its socket never bound — the probe
        is what catches it, so a dead port must surface as a failure."""
        with (
            patch(_DIAG, AsyncMock(return_value=False)),
            patch(_FIND_IFACE, return_value={"name": "eth0", "ip": "192.168.1.50"}),
        ):
            result = await run_vp_diagnostic(_vp(), _FakeInstance())
        c = _checks(result)
        assert result.overall == "problems"
        assert c["port_ftps"] == c["port_mqtt"] == c["port_bind"] == "fail"

    @pytest.mark.asyncio
    async def test_stale_bind_ip_fails_interface_check(self):
        """A bind IP that no longer matches any interface fails the check."""
        with (
            patch(_DIAG, AsyncMock(return_value=True)),
            patch(_FIND_IFACE, return_value=None),
        ):
            result = await run_vp_diagnostic(_vp(), _FakeInstance())
        c = _checks(result)
        assert c["bind_interface"] == "fail"
        assert result.overall == "problems"

    @pytest.mark.asyncio
    async def test_missing_access_code_fails_non_proxy(self):
        with (
            patch(_DIAG, AsyncMock(return_value=True)),
            patch(_FIND_IFACE, return_value={"name": "eth0", "ip": "192.168.1.50"}),
        ):
            result = await run_vp_diagnostic(_vp(access_code=None), _FakeInstance())
        assert _checks(result)["access_code"] == "fail"

    @pytest.mark.asyncio
    async def test_proxy_mode_skips_access_code_and_bind_port(self):
        """Proxy mode has no access code and runs no bind/detect server."""
        instance = _FakeInstance(proxy_status={"ftp_port": 3001, "mqtt_port": 3003})
        with (
            patch(_DIAG, AsyncMock(return_value=True)),
            patch(_FIND_IFACE, return_value={"name": "eth0", "ip": "192.168.1.50"}),
        ):
            result = await run_vp_diagnostic(_vp(mode="proxy", target_printer_id=7), instance)
        c = _checks(result)
        assert c["access_code"] == "skip"
        assert c["port_bind"] == "skip"
        assert c["port_ftps"] == "pass"
        assert c["port_mqtt"] == "pass"

    @pytest.mark.asyncio
    async def test_proxy_without_target_fails(self):
        """Proxy mode with no target printer fails the target check."""
        with (
            patch(_DIAG, AsyncMock(return_value=True)),
            patch(_FIND_IFACE, return_value={"name": "eth0", "ip": "192.168.1.50"}),
        ):
            result = await run_vp_diagnostic(
                _vp(mode="proxy", target_printer_id=None, access_code=None), _FakeInstance()
            )
        c = _checks(result)
        assert c["target_printer"] == "fail"
        assert result.overall == "problems"


class TestCaCertificateInfo:
    def test_get_ca_certificate_info_generates_and_returns_pem(self):
        """The CA is generated on demand; the returned PEM is the public cert."""
        with tempfile.TemporaryDirectory() as d:
            service = CertificateService(cert_dir=Path(d), shared_ca_dir=Path(d))
            info = service.get_ca_certificate_info()
        assert info["pem"].startswith("-----BEGIN CERTIFICATE-----")
        assert "-----END CERTIFICATE-----" in info["pem"]
        # SHA-256 fingerprint: 32 colon-separated uppercase hex bytes.
        parts = info["fingerprint_sha256"].split(":")
        assert len(parts) == 32
        assert all(len(p) == 2 and p == p.upper() for p in parts)
        assert info["not_valid_after"]

    def test_ca_certificate_info_is_stable_across_calls(self):
        """A second call reuses the persisted CA — same fingerprint, no key leak."""
        with tempfile.TemporaryDirectory() as d:
            service = CertificateService(cert_dir=Path(d), shared_ca_dir=Path(d))
            first = service.get_ca_certificate_info()
            second = service.get_ca_certificate_info()
        assert first["fingerprint_sha256"] == second["fingerprint_sha256"]
        assert "PRIVATE KEY" not in first["pem"]
