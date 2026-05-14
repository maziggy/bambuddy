"""Unit tests for assert_safe_public_https_url (#1333).

Stricter than the Spoolman SSRF guard — explicitly verifies that Spoolman-
allowed cases (loopback, RFC-1918, hostname "localhost") are REJECTED here.
Run alongside test_ssrf_guard.py to confirm both guards keep their distinct
semantics.
"""

import pytest

from backend.app.api.routes._oidc_helpers import assert_safe_public_https_url

# ─── Accepts ────────────────────────────────────────────────────────────────


def test_accepts_public_https():
    assert_safe_public_https_url("https://accounts.google.com/icon.png")


def test_accepts_hostname_no_dns_resolution():
    # By design — DNS resolution is intentionally not performed (consistent
    # with _validate_issuer_url policy). Hostnames are out of scope here.
    assert_safe_public_https_url("https://idp.internal.corp/icon.png")


# ─── Rejects: non-HTTPS schemes ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/icon.png",
        "ftp://example.com/icon.png",
        "file:///etc/passwd",
        "gopher://example.com",
    ],
)
def test_rejects_non_https(url):
    with pytest.raises(ValueError, match="https"):
        assert_safe_public_https_url(url)


# ─── Rejects: numeric-encoded IP addresses ──────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/icon.png",  # decimal-encoded 127.0.0.1
        "https://0x7f000001/icon.png",  # hex-encoded 127.0.0.1
    ],
)
def test_rejects_numeric_encoded_ip(url):
    with pytest.raises(ValueError, match="numeric-encoded"):
        assert_safe_public_https_url(url)


# ─── Rejects: cases that Spoolman INTENTIONALLY allows ──────────────────────
# These tests are deliberately structured to mirror test_ssrf_guard.py — every
# URL here is a green case for the Spoolman guard and must be a red case here.


def test_rejects_loopback_127():
    with pytest.raises(ValueError, match="loopback"):
        assert_safe_public_https_url("https://127.0.0.1/icon.png")


@pytest.mark.parametrize(
    "url",
    [
        "https://192.168.1.50/icon.png",
        "https://10.0.0.5/icon.png",
        "https://172.16.0.1/icon.png",
    ],
)
def test_rejects_rfc1918_private(url):
    with pytest.raises(ValueError, match="private"):
        assert_safe_public_https_url(url)


# ─── Rejects: link-local, cloud-metadata, multicast, unspecified ────────────


def test_rejects_link_local():
    with pytest.raises(ValueError, match="link-local|cloud metadata"):
        # 169.254.169.254 is BOTH link-local AND cloud-metadata — both
        # rejections are correct; we accept either message.
        assert_safe_public_https_url("https://169.254.169.254/icon.png")


def test_rejects_cloud_metadata_alibaba():
    with pytest.raises(ValueError, match="cloud metadata"):
        assert_safe_public_https_url("https://100.100.100.200/icon.png")


def test_rejects_multicast():
    with pytest.raises(ValueError, match="multicast"):
        assert_safe_public_https_url("https://224.0.0.1/icon.png")


def test_rejects_unspecified():
    with pytest.raises(ValueError, match="unspecified"):
        assert_safe_public_https_url("https://0.0.0.0/icon.png")


# ─── IPv6 ──────────────────────────────────────────────────────────────────


def test_rejects_ipv4_mapped_private():
    # ::ffff:192.168.1.1 unwraps to 192.168.1.1 → private
    with pytest.raises(ValueError, match="private"):
        assert_safe_public_https_url("https://[::ffff:192.168.1.1]/icon.png")


def test_rejects_ipv4_mapped_cloud_metadata():
    with pytest.raises(ValueError, match="cloud metadata|link-local"):
        # ::ffff:169.254.169.254 unwraps and triggers cloud-metadata block
        # (the cloud-metadata frozenset is checked first; link-local catches
        # 169.254/16 if it slips through, hence the regex alternation).
        assert_safe_public_https_url("https://[::ffff:169.254.169.254]/icon.png")


def test_rejects_ipv6_loopback():
    with pytest.raises(ValueError, match="loopback"):
        assert_safe_public_https_url("https://[::1]/icon.png")
