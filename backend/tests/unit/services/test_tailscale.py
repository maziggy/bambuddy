"""Unit tests for TailscaleService — presence detection only.

Cert provisioning was removed: BambuStudio's printer-MQTT trust path validates
against its bundled BBL CA, not the system trust store, so a Tailscale-issued
LE cert was rejected regardless of hostname/IP. The Tailscale toggle is now
informational (surfacing the host's Tailscale IP/FQDN to guide the user).
"""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestTailscaleService:
    """Tests for TailscaleService CLI wrapper — get_status only."""

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
        """Returns available=False when `tailscale status` exits non-zero."""
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

    @pytest.mark.asyncio
    async def test_get_status_empty_dnsname(self):
        """Returns available=False when Tailscale daemon reports no DNSName (not connected)."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        payload = {"Self": {"DNSName": "", "TailscaleIPs": []}}
        svc = TailscaleService()
        with (
            patch("shutil.which", return_value="/usr/bin/tailscale"),
            patch.object(
                svc, "_run_tailscale", new_callable=AsyncMock, return_value=(0, json.dumps(payload).encode(), b"")
            ),
        ):
            status = await svc.get_status()

        assert status.available is False
        assert "no DNSName" in (status.error or "")

    @pytest.mark.asyncio
    async def test_get_status_malformed_json(self):
        """Returns available=False with a parse-error reason when stdout is not JSON."""
        from backend.app.services.virtual_printer.tailscale import TailscaleService

        svc = TailscaleService()
        with (
            patch("shutil.which", return_value="/usr/bin/tailscale"),
            patch.object(svc, "_run_tailscale", new_callable=AsyncMock, return_value=(0, b"not-json{", b"")),
        ):
            status = await svc.get_status()

        assert status.available is False
        assert "JSON parse error" in (status.error or "")
