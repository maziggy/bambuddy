"""TLS certificate generation for virtual printer services.

Generates certificates that mimic real Bambu printer certificate format:
- CA certificate mimics "BBL CA" from "BBL Technologies Co., Ltd"
- Printer certificate has CN = serial number, signed by the CA

The CA certificate is persistent and only regenerated if missing or expired.
This allows users to add the CA to their slicer's trust store once.
"""

import logging
import socket
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = logging.getLogger(__name__)

# Default serial number for virtual printer (matches SSDP/MQTT config)
DEFAULT_SERIAL = "00M09A391800001"

# Minimum days remaining before CA is considered expired and needs regeneration
CA_EXPIRY_THRESHOLD_DAYS = 30


def _get_local_ip() -> str:
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class CertificateService:
    """Generate and manage TLS certificates for virtual printer.

    Creates a certificate chain mimicking real Bambu printers:
    - Root CA with CN="BBL CA", O="BBL Technologies Co., Ltd", C="CN"
    - Printer cert with CN=serial_number, signed by the CA
    """

    def __init__(self, cert_dir: Path, serial: str = DEFAULT_SERIAL, shared_ca_dir: Path | None = None):
        """Initialize the certificate service.

        Args:
            cert_dir: Directory to store per-instance certificates
            serial: Serial number to use as CN in printer certificate
            shared_ca_dir: If set, CA cert/key are read from this directory
                instead of cert_dir (for multi-instance shared CA)
        """
        self.cert_dir = cert_dir
        self.serial = serial
        ca_dir = shared_ca_dir or cert_dir
        self.ca_cert_path = ca_dir / "bbl_ca.crt"
        self.ca_key_path = ca_dir / "bbl_ca.key"
        self.cert_path = cert_dir / "virtual_printer.crt"
        self.key_path = cert_dir / "virtual_printer.key"

    def ensure_certificates(self) -> tuple[Path, Path]:
        """Ensure certificates exist, generate if needed.

        Returns:
            Tuple of (cert_path, key_path)
        """
        if self.cert_path.exists() and self.key_path.exists():
            logger.debug("Using existing virtual printer certificates")
            return self.cert_path, self.key_path
        return self.generate_certificates()

    def _load_existing_ca(self) -> tuple[rsa.RSAPrivateKey, x509.Certificate] | None:
        """Try to load existing CA certificate and key.

        Returns:
            Tuple of (ca_private_key, ca_certificate) if valid CA exists, None otherwise
        """
        if not self.ca_cert_path.exists() or not self.ca_key_path.exists():
            logger.debug("CA certificate or key not found")
            return None

        try:
            # Load CA certificate
            ca_cert_pem = self.ca_cert_path.read_bytes()
            ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)

            # Check if CA is expired or about to expire
            now = datetime.now(timezone.utc)
            days_remaining = (ca_cert.not_valid_after_utc - now).days
            if days_remaining < CA_EXPIRY_THRESHOLD_DAYS:
                logger.warning("CA certificate expires in %s days, will regenerate", days_remaining)
                return None

            # Load CA private key
            ca_key_pem = self.ca_key_path.read_bytes()
            ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)

            logger.info("Using existing CA certificate (expires in %s days)", days_remaining)
            return ca_key, ca_cert

        except (OSError, ValueError) as e:
            logger.warning("Failed to load existing CA: %s", e)
            return None

    def _get_or_create_ca(self) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
        """Get existing CA or create a new one.

        Returns:
            Tuple of (ca_private_key, ca_certificate)
        """
        # Try to load existing CA first
        existing = self._load_existing_ca()
        if existing:
            return existing

        # Generate new CA
        ca_key, ca_cert = self._generate_ca_certificate()

        # Save CA certificate and key
        self.cert_dir.mkdir(parents=True, exist_ok=True)
        self.ca_key_path.write_bytes(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.ca_key_path.chmod(0o600)
        self.ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        logger.info("Saved new CA certificate")
        return ca_key, ca_cert

    def _generate_ca_certificate(self) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
        """Generate a new CA certificate for the virtual printer.

        We use a generic name instead of mimicking BBL CA, since the slicer
        may specifically reject certificates claiming to be from BBL but
        with a different public key.

        Returns:
            Tuple of (ca_private_key, ca_certificate)
        """
        logger.info("Generating new Virtual Printer CA certificate...")

        # Generate CA private key
        ca_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # Use a generic CA name - NOT BBL to avoid being rejected as fake
        ca_name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "Virtual Printer CA"),
            ]
        )

        now = datetime.now(timezone.utc)

        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=7300))  # 20 years
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=0),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )

        return ca_key, ca_cert

    def _build_san_entries(self, local_ip: str, additional_ips: list[str] | None) -> list[x509.GeneralName]:
        """Build Subject Alternative Name entries for the printer certificate."""
        entries: list[x509.GeneralName] = [
            x509.DNSName("localhost"),
            x509.DNSName("bambuddy"),
            x509.DNSName(self.serial),
            x509.IPAddress(IPv4Address(local_ip)),
            x509.IPAddress(IPv4Address("127.0.0.1")),
        ]
        seen_ips = {local_ip, "127.0.0.1"}
        if additional_ips:
            for ip in additional_ips:
                if ip and ip not in seen_ips:
                    try:
                        entries.append(x509.IPAddress(IPv4Address(ip)))
                        seen_ips.add(ip)
                        logger.info("Added additional SAN IP: %s", ip)
                    except ValueError:
                        logger.warning("Skipping invalid additional SAN IP: %s", ip)
        return entries

    def generate_certificates(self, additional_ips: list[str] | None = None) -> tuple[Path, Path]:
        """Generate printer certificate (reusing existing CA if available).

        Creates a certificate chain mimicking real Bambu printers:
        - CA certificate (reused if exists and valid, otherwise generated)
        - Printer certificate (CN=serial, signed by CA)

        Args:
            additional_ips: Extra IP addresses to include in certificate SAN.
                Used in proxy mode to include the remote interface IP so the
                slicer's TLS handshake succeeds when connecting to the proxy.

        Returns:
            Tuple of (cert_path, key_path)
        """
        logger.info("Generating certificates for virtual printer (serial: %s)...", self.serial)

        # Ensure directory exists
        self.cert_dir.mkdir(parents=True, exist_ok=True)

        # Get or create CA (reuses existing if valid)
        ca_key, ca_cert = self._get_or_create_ca()

        # Generate printer private key
        printer_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # Printer certificate subject - CN is the serial number (like real Bambu printers)
        printer_subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, self.serial),
            ]
        )

        # Issuer is the CA
        issuer = ca_cert.subject

        now = datetime.now(timezone.utc)
        local_ip = _get_local_ip()
        logger.info("Generating printer certificate with CN=%s, local IP: %s", self.serial, local_ip)

        # Build printer certificate signed by CA
        printer_cert = (
            x509.CertificateBuilder()
            .subject_name(printer_subject)
            .issuer_name(issuer)
            .public_key(printer_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=3650))  # 10 years
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName(self._build_san_entries(local_ip, additional_ips)),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH,
                        ExtendedKeyUsageOID.CLIENT_AUTH,
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())  # Signed by CA, not self-signed
        )

        # Write printer private key
        self.key_path.write_bytes(
            printer_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.key_path.chmod(0o600)

        # Write printer certificate (include CA cert in chain for full chain)
        cert_chain = printer_cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(
            serialization.Encoding.PEM
        )
        self.cert_path.write_bytes(cert_chain)

        logger.info("Generated certificate chain at %s", self.cert_dir)
        logger.info("  CA: CN=Virtual Printer CA")
        logger.info("  Printer: CN=%s", self.serial)
        return self.cert_path, self.key_path

    def delete_printer_certificate(self) -> None:
        """Delete only the printer certificate (preserves CA)."""
        for path in [self.cert_path, self.key_path]:
            if path.exists():
                path.unlink()
        logger.info("Deleted printer certificate (CA preserved)")

    def delete_certificates(self, include_ca: bool = False) -> None:
        """Delete existing certificates.

        Args:
            include_ca: If True, also delete CA certificate and key.
                       If False (default), only delete printer certificate.
        """
        # Always delete printer certificate
        for path in [self.cert_path, self.key_path]:
            if path.exists():
                path.unlink()

        # Only delete CA if explicitly requested
        if include_ca:
            for path in [self.ca_cert_path, self.ca_key_path]:
                if path.exists():
                    path.unlink()
            logger.info("Deleted all certificates including CA")
        else:
            logger.info("Deleted printer certificate (CA preserved)")
