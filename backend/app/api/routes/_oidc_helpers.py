"""Pure helper functions for OIDC routes.

Hosts the SSRF guard for admin-supplied icon URLs. Stricter than
``_spoolman_helpers.assert_safe_spoolman_url`` — Spoolman intentionally allows
loopback/RFC-1918 (same-LAN topology) while OIDC icons must be reachable on
the public internet (IdP-hosted), so private addresses there are SSRF probes.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from backend.app.api.routes._url_safety import CLOUD_METADATA_IPS, NUMERIC_IP_RE, unwrap_ipv4_mapped


def assert_safe_public_https_url(url: str) -> None:
    """Raise ValueError if *url* is unsafe to fetch as a public HTTPS resource.

    Used for OIDC provider icon URLs (#1333). Stricter than the Spoolman SSRF
    guard: also rejects loopback, private (RFC-1918), and link-local addresses
    because an OIDC icon legitimately lives only on the public internet.

    Checks performed:
    - Scheme must be ``https`` (no ``http://``, ``file://``, ``gopher://``, …).
    - Numeric-encoded IPv4 (decimal ``2130706433``, hex ``0x7f000001``) is
      rejected — libc and browsers parse those as valid addresses while
      Python's ``ipaddress`` raises ValueError, so they bypass the IP block
      below if not caught first.
    - Cloud-provider metadata endpoints (169.254.169.254, 100.100.100.200,
      fd00:ec2::254) — classic SSRF credential-exfil targets.
    - Loopback (127.0.0.0/8, ::1), private RFC-1918 (10/8, 172.16/12,
      192.168/16) and link-local (169.254/16, fe80::/10) addresses.
    - Multicast (224.0.0.0/4, ff00::/8) and unspecified (0.0.0.0, ::).
    - IPv4-mapped IPv6 (``::ffff:127.0.0.1``) — unwrapped before the IP-class
      check so an attacker can't bypass via IPv6 encoding.

    Hostname-based addresses are accepted without DNS resolution (consistent
    with ``_validate_issuer_url`` policy — the operator is trusted to
    configure a sensible IdP host).
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("icon URL must use https://")

    hostname = (parsed.hostname or "").lower()

    if NUMERIC_IP_RE.match(hostname):
        raise ValueError("icon URL must not use numeric-encoded IP addresses")

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return  # hostname — out of scope (no DNS check by design)

    effective = unwrap_ipv4_mapped(addr)

    if effective in CLOUD_METADATA_IPS:
        raise ValueError("icon URL must not point to a cloud metadata endpoint")

    # Order matters: 0.0.0.0 sets BOTH is_private and is_unspecified — check
    # the more-specific is_unspecified first so the error message points at
    # the actual misuse. Similarly 127.0.0.1 sets is_loopback and is_private
    # (private under IANA's reservation); is_loopback first is clearer.
    if effective.is_unspecified:
        raise ValueError("icon URL must not point to an unspecified address")
    if effective.is_loopback:
        raise ValueError("icon URL must not point to a loopback address")
    if effective.is_link_local:
        raise ValueError("icon URL must not point to a link-local address")
    if effective.is_multicast:
        raise ValueError("icon URL must not point to a multicast address")
    if effective.is_private:
        raise ValueError("icon URL must not point to a private (RFC-1918) address")
