"""Shared URL-safety primitives for the SSRF guards in this package.

Bambuddy has exactly two outbound-URL policies, and which one applies is a
property of the *service*, not of the caller:

- **LAN-service** (``assert_safe_lan_service_url`` below) — the service
  legitimately lives on the same host or home LAN, so loopback and RFC-1918
  must be permitted; blocking them would break the normal topology. Used for
  Spoolman, self-hosted notification servers (ntfy, Bark, Gotify, custom
  webhooks), Home Assistant, the Obico ML endpoint and the slicer sidecars.
- **Public-internet** (``_oidc_helpers.assert_safe_public_https_url``) — the
  resource can only sensibly live on the public internet, so a private
  address is an SSRF probe rather than a configuration. Used for OIDC issuer
  and icon URLs.

Both reject the cases that are dangerous regardless of topology: non-HTTP
schemes, numeric-encoded IPs, cloud-metadata endpoints, multicast and
unspecified addresses, and IPv4-mapped IPv6 encodings of any of the above.

The LAN-service policy lives here because it now has several callers; the
public-internet policy stays in ``_oidc_helpers`` next to its only consumer.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

# Cloud-provider metadata endpoints — the classic SSRF credential-exfil
# targets. Both guards reject these unconditionally.
CLOUD_METADATA_IPS = frozenset(
    {
        # AWS / GCP / Azure / Oracle / DigitalOcean IMDS
        ipaddress.ip_address("169.254.169.254"),
        # Alibaba Cloud metadata
        ipaddress.ip_address("100.100.100.200"),
        # AWS IMDS IPv6
        ipaddress.ip_address("fd00:ec2::254"),
    }
)

# The DNS-name form of the same targets. Neither guard resolves hostnames (see
# the TOCTOU note on each), so an IP blocklist alone cannot catch these — but a
# literal-string match needs no resolution and costs nothing. These names only
# resolve inside the respective cloud, so there is no legitimate reason for any
# Bambuddy integration to point at one.
CLOUD_METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",  # GCP
        "metadata.goog",  # GCP short form
    }
)


# libc and browsers parse numeric-encoded IP forms (decimal ``2130706433``
# for 127.0.0.1, hex ``0x7f000001``) but Python's ``ipaddress.ip_address``
# raises ValueError on these, so they slip past the IP-class checks if
# not caught first. Used by both guards to reject up-front.
NUMERIC_IP_RE = re.compile(r"^(0x[0-9a-f]+|[0-9]+)$", re.I)


def unwrap_ipv4_mapped(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Return the underlying IPv4 for an IPv4-mapped IPv6 address, else return *addr*.

    ``::ffff:127.0.0.1`` and similar mapped forms must be unwrapped before
    the per-class checks (``is_private``, ``is_loopback``, …) — otherwise
    an attacker can encode a blocked IPv4 address as an IPv6 literal to
    bypass the guard.
    """
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def assert_safe_lan_service_url(url: str, *, label: str) -> None:
    """Raise ValueError if *url* is unsafe for a service that may live on the LAN.

    ``label`` names the setting in the error message ("Spoolman URL", "ntfy
    server URL", …) so the user sees which field they need to correct.

    Loopback (127.0.0.1) and RFC-1918 private ranges are deliberately
    **permitted** — Bambuddy is self-hosted and running Spoolman, ntfy,
    Bark, Home Assistant, an Obico ML endpoint or a slicer sidecar on the
    same host or home LAN is THE normal topology, not an attack. A blanket
    private-address block would break those integrations for most installs.

    What is rejected is dangerous under any topology:

    - Schemes other than http/https. ``httpx`` already raises
      ``UnsupportedProtocol`` for ``file://``/``gopher://`` etc., so this is
      about returning a clear validation error at configuration time rather
      than an opaque failure at delivery time.
    - Numeric-encoded IPv4 (decimal ``2130706433``, hex ``0x7f000001``) —
      libc and browsers resolve these, but Python's ``ipaddress`` raises
      ValueError on them, so they would slip past the checks below.
    - Cloud-provider metadata endpoints — the high-value SSRF target, and
      never a legitimate destination for any of these services.
    - Multicast and unspecified addresses — pointless as a destination and
      indicative of misuse.
    - IPv4-mapped IPv6 encodings of any of the above.

    Symbolic hostnames are otherwise accepted without DNS resolution, matching
    the public-internet guard: resolution here would be both a TOCTOU (DNS can
    change between validation and request) and a request the validator
    shouldn't be making. The one exception is the fixed set of cloud-metadata
    hostnames, which is a literal-string match and needs no resolution.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"{label} must use http or https")

    hostname = (parsed.hostname or "").lower()

    # "http:///path" parses to an empty hostname. Never a valid destination,
    # and without this it falls through the ip_address() ValueError branch
    # below and is accepted as if it were a symbolic hostname.
    if not hostname:
        raise ValueError(f"{label} must include a hostname")

    if hostname in CLOUD_METADATA_HOSTNAMES:
        raise ValueError(f"{label} must not point to a cloud metadata endpoint")

    if NUMERIC_IP_RE.match(hostname):
        raise ValueError(f"{label} must not use numeric-encoded IP addresses; use standard dotted-decimal notation")

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return  # symbolic hostname — out of scope by design (no DNS check)

    effective = unwrap_ipv4_mapped(addr)

    if effective in CLOUD_METADATA_IPS:
        raise ValueError(f"{label} must not point to a cloud metadata endpoint")

    if effective.is_multicast or effective.is_unspecified:
        raise ValueError(f"{label} must not point to a multicast or unspecified address")
