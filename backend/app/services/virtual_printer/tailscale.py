"""Tailscale integration for virtual printer certificate provisioning.

When Tailscale is present, provisions a Let's Encrypt certificate via
`tailscale cert` for the machine's Tailscale FQDN. This cert is trusted
by slicers without any manual CA installation, unlike the self-signed CA.

Falls back gracefully when Tailscale is unavailable.
"""

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509

logger = logging.getLogger(__name__)

# Renew when fewer than this many days remain on the LE cert (LE issues 90-day certs)
TS_CERT_EXPIRY_THRESHOLD_DAYS = 14

# Defensive FQDN validation before passing to subprocess
_FQDN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)


@dataclass
class TailscaleStatus:
    """Runtime Tailscale availability and identity."""

    available: bool
    hostname: str  # "myhost"
    tailnet_name: str  # "tailnetname.ts.net"
    fqdn: str  # "myhost.tailnetname.ts.net"
    tailscale_ips: list[str] = field(default_factory=list)
    error: str | None = None


class TailscaleService:
    """Wraps Tailscale CLI commands for certificate provisioning.

    All methods are safe to call when Tailscale is absent — they return
    sensible defaults and never raise exceptions.
    """

    async def _run_tailscale(self, *args: str) -> tuple[int, bytes, bytes]:
        """Run a tailscale subcommand and return (returncode, stdout, stderr).

        Extracted to allow easy mocking in tests without patching asyncio internals.
        Raises OSError if the binary cannot be launched.
        """
        process = await asyncio.create_subprocess_exec(
            "tailscale",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout, stderr

    async def get_status(self) -> TailscaleStatus:
        """Query Tailscale status and return machine identity.

        Runs: tailscale status --json

        Returns TailscaleStatus(available=False) if the binary is missing,
        the daemon is not running, or any other error occurs.
        """
        if not shutil.which("tailscale"):
            return TailscaleStatus(
                available=False,
                hostname="",
                tailnet_name="",
                fqdn="",
                error="tailscale binary not found",
            )

        try:
            returncode, stdout, stderr = await self._run_tailscale("status", "--json")
        except OSError as e:
            return TailscaleStatus(
                available=False,
                hostname="",
                tailnet_name="",
                fqdn="",
                error=str(e),
            )

        if returncode != 0:
            return TailscaleStatus(
                available=False,
                hostname="",
                tailnet_name="",
                fqdn="",
                error=stderr.decode(errors="replace").strip(),
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            return TailscaleStatus(
                available=False,
                hostname="",
                tailnet_name="",
                fqdn="",
                error=f"JSON parse error: {e}",
            )

        self_info = data.get("Self", {})

        # DNSName includes trailing dot: "myhost.tailnetname.ts.net."
        fqdn = self_info.get("DNSName", "").rstrip(".")
        if not fqdn:
            return TailscaleStatus(
                available=False,
                hostname="",
                tailnet_name="",
                fqdn="",
                error="Tailscale not connected (no DNSName)",
            )

        # Split "myhost.tailnetname.ts.net" into hostname + tailnet_name
        parts = fqdn.split(".", 1)
        hostname = parts[0]
        tailnet_name = parts[1] if len(parts) > 1 else ""

        tailscale_ips = self_info.get("TailscaleIPs", [])

        logger.debug("Tailscale available: fqdn=%s, ips=%s", fqdn, tailscale_ips)
        return TailscaleStatus(
            available=True,
            hostname=hostname,
            tailnet_name=tailnet_name,
            fqdn=fqdn,
            tailscale_ips=tailscale_ips,
        )

    async def provision_cert(self, fqdn: str, cert_path: Path, key_path: Path) -> bool:
        """Request a Let's Encrypt certificate for the given Tailscale FQDN.

        Runs: tailscale cert --cert-file <cert_path> --key-file <key_path> <fqdn>

        Returns True on success, False on any error.
        """
        if not _FQDN_RE.match(fqdn):
            logger.warning("provision_cert: invalid FQDN %r, skipping", fqdn)
            return False

        logger.info("Provisioning Tailscale cert for %s -> %s", fqdn, cert_path)
        try:
            returncode, _, stderr = await self._run_tailscale(
                "cert", "--cert-file", str(cert_path), "--key-file", str(key_path), fqdn
            )
        except OSError as e:
            logger.warning("tailscale cert failed (OS error): %s", e)
            return False

        if returncode != 0:
            logger.warning(
                "tailscale cert failed (exit %d): %s",
                returncode,
                stderr.decode(errors="replace").strip(),
            )
            return False

        # Restrict private key permissions
        try:
            key_path.chmod(0o600)
        except OSError as e:
            logger.warning("Could not set key permissions on %s: %s", key_path, e)

        logger.info("Tailscale cert provisioned: %s", cert_path)
        return True

    def cert_needs_renewal(self, cert_path: Path, fqdn: str | None = None) -> bool:
        """Check whether the certificate at cert_path needs to be renewed.

        Returns True if the file is absent, unreadable, expires within
        TS_CERT_EXPIRY_THRESHOLD_DAYS days, or if fqdn is given and does not
        appear in the certificate's Subject Alternative Names.
        """
        if not cert_path.exists():
            return True

        try:
            cert_pem = cert_path.read_bytes()
            # The file may contain a full chain; load only the first PEM block
            cert = x509.load_pem_x509_certificate(cert_pem)
            now = datetime.now(timezone.utc)
            days_remaining = (cert.not_valid_after_utc - now).days
            if days_remaining < TS_CERT_EXPIRY_THRESHOLD_DAYS:
                logger.info("Tailscale cert expires in %d days, renewal needed", days_remaining)
                return True

            # Validate that the cert covers the requested FQDN (guards against stale
            # cert after machine rename or tailnet migration)
            if fqdn:
                try:
                    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                    dns_names = san.value.get_values_for_type(x509.DNSName)
                    if fqdn not in dns_names:
                        logger.info(
                            "Tailscale cert SAN mismatch (cert has %s, need %s), renewal needed",
                            dns_names,
                            fqdn,
                        )
                        return True
                except x509.ExtensionNotFound:
                    logger.info("Tailscale cert has no SAN extension, renewal needed")
                    return True

            logger.debug("Tailscale cert valid for %d more days", days_remaining)
            return False
        except (OSError, ValueError) as e:
            logger.warning("Could not read Tailscale cert %s: %s", cert_path, e)
            return True

    async def ensure_cert(self, fqdn: str, cert_path: Path, key_path: Path) -> bool:
        """Ensure a fresh certificate exists at cert_path.

        Skips provisioning if the cert is present, not near expiry, and covers fqdn.
        Returns True if a valid cert is now available.
        """
        if not self.cert_needs_renewal(cert_path, fqdn=fqdn):
            logger.debug("Tailscale cert is fresh, skipping provision")
            return True
        return await self.provision_cert(fqdn, cert_path, key_path)


# Module-level singleton — import this in other modules
tailscale_service = TailscaleService()
