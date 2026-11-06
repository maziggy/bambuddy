"""Unit tests for the shared SSRF-data primitives (#1333).

These primitives are imported by both ``_spoolman_helpers.assert_safe_spoolman_url``
and ``_oidc_helpers.assert_safe_public_https_url``. Test them in isolation
so a future change to one consumer doesn't accidentally drift the data.
"""

import ipaddress

import pytest

from backend.app.api.routes._url_safety import (
    CLOUD_METADATA_HOSTNAMES,
    CLOUD_METADATA_IPS,
    is_numeric_encoded_ip,
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
        "0xa9.0xfe.0xa9.0xfe",  # dotted hex
        "169.254.43518",  # short form
        "0251.0376.0251.0376",  # dotted octal
        "0169.254.169.254",  # a leading zero
    ],
)
def test_numeric_encoded_forms_are_recognised(candidate):
    assert is_numeric_encoded_ip(candidate) is True


@pytest.mark.parametrize(
    "candidate",
    [
        "127.0.0.1",  # dotted-decimal — not "numeric-encoded"
        "example.com",
        "spoolman.lan",
        "::1",
        "localhost",
        "3dprinter.lan",
        "node-01",
    ],
)
def test_normal_forms_are_not_numeric_encoded(candidate):
    assert is_numeric_encoded_ip(candidate) is False


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


def test_unwrap_drops_a_zone_id():
    """Ignored on connect to an address that is not link-local: the address
    reached is the one without it."""
    result = unwrap_ipv4_mapped(ipaddress.ip_address("fd00:ec2::254%251"))
    assert result == ipaddress.ip_address("fd00:ec2::254")
    assert result in CLOUD_METADATA_IPS


def test_cloud_metadata_names_include_the_short_and_alias_forms():
    assert {"metadata", "metadata.google", "instance-data", "instance-data.ec2.internal"} <= CLOUD_METADATA_HOSTNAMES
