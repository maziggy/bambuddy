"""Pure helper functions for OIDC routes.

Hosts the public-internet SSRF guard, used for both admin-supplied icon URLs
and OIDC issuer URLs (via ``schemas.auth._validate_issuer_url``). Stricter
than ``_url_safety.assert_safe_lan_service_url`` — LAN services intentionally
allow loopback/RFC-1918 (same-host/same-LAN topology) while an IdP must be
reachable on the public internet, so a private address there is an SSRF probe
rather than a configuration.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from backend.app.api.routes._url_safety import (
    CLOUD_METADATA_HOSTNAMES,
    CLOUD_METADATA_IPS,
    NUMERIC_IP_RE,
    unwrap_ipv4_mapped,
)


def assert_safe_public_https_url(url: str) -> None:
    """Raise ValueError if *url* is unsafe to fetch as a public HTTPS resource.

    Used for OIDC provider icon URLs (#1333) and OIDC issuer URLs. Stricter
    than the LAN-service SSRF guard: also rejects loopback, private
    (RFC-1918), and link-local addresses because an IdP and its icon
    legitimately live only on the public internet.

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

    Hostname-based addresses are otherwise accepted without DNS resolution —
    the operator is trusted to configure a sensible IdP host, and resolving
    here would both add a TOCTOU gap (DNS can change between validation and
    request) and make the validator issue network requests of its own. The
    fixed cloud-metadata hostnames are the exception: matching them is a
    literal string comparison, not a resolution.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("icon URL must use https://")

    hostname = (parsed.hostname or "").lower()

    # "https:///path" parses to an empty hostname; without this it reaches the
    # ip_address() ValueError branch and is accepted as a symbolic hostname.
    if not hostname:
        raise ValueError("icon URL must include a hostname")

    if hostname in CLOUD_METADATA_HOSTNAMES:
        raise ValueError("icon URL must not point to a cloud metadata endpoint")

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
