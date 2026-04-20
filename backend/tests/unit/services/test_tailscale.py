"""Unit tests for TailscaleService and Tailscale-aware VirtualPrinterInstance."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _make_cert(tmp_path: Path, days_valid: int, fqdn: str | None = None) -> Path:
    """Write a self-signed cert valid for days_valid days and return its path.

    If fqdn is provided the cert includes a SubjectAlternativeName DNS entry.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=days_valid))
    )
    if fqdn:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(fqdn)]),
            critical=False,
        )
    cert = builder.sign(key, hashes.SHA256())
    path = tmp_path / "cert.crt"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path


# =============================================================================
# TailscaleService tests
# =============================================================================


class TestTailscaleService:
    """Tests for TailscaleService CLI wrapper."""

    # -- get_status --

    @pytest.mark.asyncio
    async def test_get_status_binary_not_found(self):
        """Returns available=False when the tailscale binary is absent from PATH."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with patch("shutil.which", return_value=None):
            status = await svc.get_status()

        assert status.available is False
        assert status.error is not None
        assert "not found" in status.error

    @pytest.mark.asyncio
    async def test_get_status_command_fails(self):
        """Returns available=False when the tailscale status command exits non-zero."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with (
            patch("shutil.which", return_value="/usr/bin/tailscale"),
            patch.object(svc, "_run_tailscale", new_callable=AsyncMock, return_value=(1, b"", b"permission denied")),
        ):
            status = await svc.get_status()

        assert status.available is False
        assert "permission denied" in (status.error or "")

    @pytest.mark.asyncio
    async def test_get_status_success(self):
        """Parses FQDN, hostname, tailnet_name, and IP list from JSON output."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        payload = {
            "Self": {
                "DNSName": "myhost.example.ts.net.",
                "TailscaleIPs": ["100.1.2.3", "fd7a::1"],
            }
        }
        svc = TailscaleService()
        with (
            patch("shutil.which", return_value="/usr/bin/tailscale"),
            patch.object(
                svc, "_run_tailscale", new_callable=AsyncMock, return_value=(0, json.dumps(payload).encode(), b"")
            ),
        ):
            status = await svc.get_status()

        assert status.available is True
        assert status.fqdn == "myhost.example.ts.net"
        assert status.hostname == "myhost"
        assert status.tailnet_name == "example.ts.net"
        assert "100.1.2.3" in status.tailscale_ips

    # -- provision_cert --

    @pytest.mark.asyncio
    async def test_provision_cert_success(self, tmp_path):
        """Returns True and forwards the correct arguments to _run_tailscale."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = tmp_path / "ts.crt"
        key_path = tmp_path / "ts.key"
        key_path.write_text("fake-key")

        svc = TailscaleService()
        with patch.object(svc, "_run_tailscale", new_callable=AsyncMock, return_value=(0, b"", b"")) as mock_run:
            result = await svc.provision_cert("myhost.ts.net", cert_path, key_path)

        assert result is True
        called_args = mock_run.call_args[0]  # positional args to _run_tailscale
        assert "cert" in called_args
        assert "--cert-file" in called_args
        assert str(cert_path) in called_args
        assert "myhost.ts.net" in called_args

    @pytest.mark.asyncio
    async def test_provision_cert_failure(self, tmp_path):
        """Returns False without raising when the tailscale cert command fails."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with patch.object(svc, "_run_tailscale", new_callable=AsyncMock, return_value=(1, b"", b"not logged in")):
            result = await svc.provision_cert("myhost.ts.net", tmp_path / "ts.crt", tmp_path / "ts.key")

        assert result is False

    # -- cert_needs_renewal --

    def test_cert_needs_renewal_absent(self, tmp_path):
        """Returns True when the cert file does not exist."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        assert svc.cert_needs_renewal(tmp_path / "nonexistent.crt") is True

    def test_cert_needs_renewal_fresh(self, tmp_path):
        """Returns False when the cert has more than the threshold days remaining."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = _make_cert(tmp_path, days_valid=60)
        svc = TailscaleService()
        assert svc.cert_needs_renewal(cert_path) is False

    def test_cert_needs_renewal_expiring(self, tmp_path):
        """Returns True when the cert is within the renewal threshold."""
        from backend.app.services.virtual_printer.tailscale import (
            TS_CERT_EXPIRY_THRESHOLD_DAYS,
            TailscaleService,
        )

        cert_path = _make_cert(tmp_path, days_valid=TS_CERT_EXPIRY_THRESHOLD_DAYS - 1)
        svc = TailscaleService()
        assert svc.cert_needs_renewal(cert_path) is True

    # -- ensure_cert --

    @pytest.mark.asyncio
    async def test_ensure_cert_skips_provision_when_fresh(self, tmp_path):
        """Does not call provision_cert when the existing cert is still fresh."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with (
            patch.object(svc, "cert_needs_renewal", return_value=False),
            patch.object(svc, "provision_cert", new_callable=AsyncMock) as mock_prov,
        ):
            result = await svc.ensure_cert("h.ts.net", tmp_path / "ts.crt", tmp_path / "ts.key")

        assert result is True
        mock_prov.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_cert_provisions_when_absent(self, tmp_path):
        """Calls provision_cert when no valid cert exists."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with (
            patch.object(svc, "cert_needs_renewal", return_value=True),
            patch.object(svc, "provision_cert", new_callable=AsyncMock, return_value=True) as mock_prov,
        ):
            result = await svc.ensure_cert("h.ts.net", tmp_path / "ts.crt", tmp_path / "ts.key")

        assert result is True
        mock_prov.assert_called_once()


# =============================================================================
# VirtualPrinterInstance Tailscale integration tests
# =============================================================================


class TestVirtualPrinterInstanceTailscale:
    """Tests for Tailscale cert/advertise resolution in VirtualPrinterInstance."""

    @pytest.fixture
    def instance(self, tmp_path):
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        return VirtualPrinterInstance(
            vp_id=1,
            name="TestPrinter",
            mode="immediate",
            model="C11",
            access_code="12345678",
            serial_suffix="391800001",
            base_dir=tmp_path,
        )

    @pytest.mark.asyncio
    async def test_resolve_uses_tailscale_when_available(self, instance):
        """Returns TS cert paths and FQDN advertise address when Tailscale is up."""
        from backend.app.services.virtual_printer.tailscale import TailscaleStatus

        ts_cert = instance.cert_dir / "virtual_printer_ts.crt"
        ts_key = instance.cert_dir / "virtual_printer_ts.key"

        mock_ts = MagicMock()
        mock_ts.get_status = AsyncMock(
            return_value=TailscaleStatus(
                available=True,
                hostname="myhost",
                tailnet_name="example.ts.net",
                fqdn="myhost.example.ts.net",
                tailscale_ips=["100.1.2.3"],
            )
        )

        with (
            patch("backend.app.services.virtual_printer.manager.tailscale_service", mock_ts),
            patch.object(
                instance._cert_service,
                "use_tailscale_cert",
                new_callable=AsyncMock,
                return_value=(ts_cert, ts_key),
            ),
        ):
            cert_path, key_path, advertise = await instance._resolve_cert_and_advertise()

        assert cert_path == ts_cert
        assert key_path == ts_key
        assert advertise == "myhost.example.ts.net"
        assert instance.tailscale_fqdn == "myhost.example.ts.net"

    @pytest.mark.asyncio
    async def test_resolve_falls_back_to_selfsigned(self, instance, tmp_path):
        """Falls back to self-signed cert and IP string when Tailscale is absent."""
        from backend.app.services.virtual_printer.tailscale import TailscaleStatus

        self_cert = tmp_path / "cert.crt"
        self_key = tmp_path / "cert.key"

        mock_ts = MagicMock()
        mock_ts.get_status = AsyncMock(
            return_value=TailscaleStatus(
                available=False,
                hostname="",
                tailnet_name="",
                fqdn="",
                error="tailscale binary not found",
            )
        )

        with (
            patch("backend.app.services.virtual_printer.manager.tailscale_service", mock_ts),
            patch.object(instance, "generate_certificates", return_value=(self_cert, self_key)),
        ):
            cert_path, key_path, advertise = await instance._resolve_cert_and_advertise()

        assert cert_path == self_cert
        assert key_path == self_key
        assert instance.tailscale_fqdn is None
        assert isinstance(advertise, str)

    def test_tailscale_fqdn_in_status_when_set(self, instance):
        """get_status() includes tailscale_fqdn when it is set."""
        instance.tailscale_fqdn = "myhost.example.ts.net"
        status = instance.get_status()
        assert status.get("tailscale_fqdn") == "myhost.example.ts.net"

    def test_tailscale_fqdn_absent_from_status_when_none(self, instance):
        """get_status() omits the tailscale_fqdn key when tailscale_fqdn is None."""
        instance.tailscale_fqdn = None
        status = instance.get_status()
        assert "tailscale_fqdn" not in status


# =============================================================================
# cert_needs_renewal — FQDN SAN validation, exception narrowing, FQDN regex
# =============================================================================


class TestCertNeedsRenewalExtended:
    """Extended tests for cert_needs_renewal() covering new FQDN and exception logic."""

    def test_fqdn_match_fresh_cert_not_renewed(self, tmp_path):
        """Fresh cert whose SAN matches the requested FQDN is not renewed."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        fqdn = "myhost.example.ts.net"
        cert_path = _make_cert(tmp_path, days_valid=60, fqdn=fqdn)
        svc = TailscaleService()
        assert svc.cert_needs_renewal(cert_path, fqdn=fqdn) is False

    def test_fqdn_mismatch_triggers_renewal(self, tmp_path):
        """Fresh cert whose SAN does NOT match the requested FQDN triggers renewal."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = _make_cert(tmp_path, days_valid=60, fqdn="oldhost.example.ts.net")
        svc = TailscaleService()
        assert svc.cert_needs_renewal(cert_path, fqdn="newhost.example.ts.net") is True

    def test_cert_without_san_triggers_renewal_when_fqdn_given(self, tmp_path):
        """Cert with no SAN extension triggers renewal when an FQDN is requested."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = _make_cert(tmp_path, days_valid=60, fqdn=None)
        svc = TailscaleService()
        assert svc.cert_needs_renewal(cert_path, fqdn="myhost.example.ts.net") is True

    def test_fqdn_not_checked_when_none(self, tmp_path):
        """Fresh cert with no SAN is valid when no FQDN is requested (backward-compat)."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = _make_cert(tmp_path, days_valid=60, fqdn=None)
        svc = TailscaleService()
        assert svc.cert_needs_renewal(cert_path, fqdn=None) is False

    def test_narrow_exception_oserror_triggers_renewal(self, tmp_path):
        """OSError while reading the cert file triggers renewal."""
        from unittest.mock import patch

        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = _make_cert(tmp_path, days_valid=60)
        svc = TailscaleService()
        with patch("pathlib.Path.read_bytes", side_effect=OSError("permission denied")):
            assert svc.cert_needs_renewal(cert_path) is True

    def test_narrow_exception_valueerror_triggers_renewal(self, tmp_path):
        """ValueError (bad PEM data) while loading the cert triggers renewal."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = tmp_path / "bad.crt"
        cert_path.write_bytes(b"not a valid pem")
        svc = TailscaleService()
        assert svc.cert_needs_renewal(cert_path) is True

    def test_programming_error_propagates(self, tmp_path):
        """Unexpected exceptions (not OSError/ValueError) are NOT silently swallowed."""
        from unittest.mock import patch

        from backend.app.services.virtual_printer.tailscale import TailscaleService

        cert_path = _make_cert(tmp_path, days_valid=60)
        svc = TailscaleService()
        with (
            patch("pathlib.Path.read_bytes", side_effect=RuntimeError("unexpected")),
            pytest.raises(RuntimeError, match="unexpected"),
        ):
            svc.cert_needs_renewal(cert_path)


class TestProvisionCertFQDNValidation:
    """Tests for FQDN input validation in provision_cert()."""

    @pytest.mark.asyncio
    async def test_invalid_fqdn_rejected_without_subprocess(self, tmp_path):
        """provision_cert() returns False immediately for an invalid FQDN."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with patch.object(svc, "_run_tailscale", new_callable=AsyncMock) as mock_run:
            result = await svc.provision_cert("../evil", tmp_path / "c.crt", tmp_path / "k.key")

        assert result is False
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_label_fqdn_rejected(self, tmp_path):
        """A hostname without dots (no tailnet) is rejected."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with patch.object(svc, "_run_tailscale", new_callable=AsyncMock) as mock_run:
            result = await svc.provision_cert("justhostname", tmp_path / "c.crt", tmp_path / "k.key")

        assert result is False
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_fqdn_passes_to_subprocess(self, tmp_path):
        """A valid FQDN is forwarded to _run_tailscale."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        key_path = tmp_path / "k.key"
        key_path.write_text("fake")
        svc = TailscaleService()
        with patch.object(svc, "_run_tailscale", new_callable=AsyncMock, return_value=(0, b"", b"")) as mock_run:
            result = await svc.provision_cert("myhost.example.ts.net", tmp_path / "c.crt", key_path)

        assert result is True
        assert "myhost.example.ts.net" in mock_run.call_args[0]
