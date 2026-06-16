"""Unit tests for the shared SSRF-data primitives (#1333).

These primitives are imported by both ``_spoolman_helpers.assert_safe_spoolman_url``
and ``_oidc_helpers.assert_safe_public_https_url``. Test them in isolation
so a future change to one consumer doesn't accidentally drift the data.
"""

import ipaddress

import pytest

from backend.app.api.routes._url_safety import (
    CLOUD_METADATA_IPS,
    NUMERIC_IP_RE,
    unwrap_ipv4_mapped,
)


def test_cloud_metadata_set_contains_known_endpoints():
    # Both v4 and v6 IMDS endpoints, plus Alibaba's variant.
    assert ipaddress.ip_address("169.254.169.254") in CLOUD_METADATA_IPS
    assert ipaddress.ip_address("100.100.100.200") in CLOUD_METADATA_IPS
    assert ipaddress.ip_address("fd00:ec2::254") in CLOUD_METADATA_IPS


def test_cloud_metadata_set_is_frozen():
    # frozenset is the right immutable container — protects against
    # accidental mutation in tests/imports.
    assert isinstance(CLOUD_METADATA_IPS, frozenset)


@pytest.mark.parametrize(
    "candidate",
    [
        "2130706433",  # decimal-encoded 127.0.0.1
        "0x7f000001",  # hex-encoded 127.0.0.1
        "0xFFFFFFFF",  # uppercase hex
        "0",
        "4294967295",  # max uint32
    ],
)
def test_numeric_ip_re_matches_encoded_forms(candidate):
    assert NUMERIC_IP_RE.match(candidate) is not None


@pytest.mark.parametrize(
    "candidate",
    [
        "127.0.0.1",  # dotted-decimal — not "numeric-encoded"
        "example.com",
        "spoolman.lan",
        "::1",
        "localhost",
    ],
)
def test_numeric_ip_re_rejects_normal_forms(candidate):
    assert NUMERIC_IP_RE.match(candidate) is None


def test_unwrap_ipv4_mapped_unwraps_mapped_address():
    mapped = ipaddress.ip_address("::ffff:127.0.0.1")
    result = unwrap_ipv4_mapped(mapped)
    assert result == ipaddress.ip_address("127.0.0.1")
    assert isinstance(result, ipaddress.IPv4Address)


def test_unwrap_ipv4_mapped_passes_through_pure_ipv4():
    addr = ipaddress.ip_address("8.8.8.8")
    assert unwrap_ipv4_mapped(addr) is addr


def test_unwrap_ipv4_mapped_passes_through_pure_ipv6():
    addr = ipaddress.ip_address("2001:db8::1")
    assert unwrap_ipv4_mapped(addr) is addr
