"""Shared URL-safety primitives used by both SSRF guards in this package.

The two top-level assertion functions —
``_spoolman_helpers.assert_safe_spoolman_url`` (Spoolman, deliberately allows
loopback/RFC-1918 because same-LAN deployment is the standard topology) and
``_oidc_helpers.assert_safe_public_https_url`` (OIDC icons, must be reachable
on the public internet, so loopback/private are rejected) — share the
*data* (cloud-metadata IP set, numeric-encoded-IP regex) but not the
*policy*. Only the data lives here. The functions stay in their respective
modules with their distinct policies intact.
"""

from __future__ import annotations

import ipaddress
import re

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
