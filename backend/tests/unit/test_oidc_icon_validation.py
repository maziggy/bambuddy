"""Unit tests for the icon_url Pydantic validator (#1333).

Mirrors the issuer_url validator pattern: HTTPS-only + private/loopback/
link-local IP literals rejected. Hostname-based URLs are accepted without
DNS resolution (deliberate, see _validate_issuer_url).
"""

import pytest

from backend.app.schemas.auth import OIDCProviderCreate


def _make_payload(icon_url: str | None) -> dict:
    """Minimal valid OIDCProviderCreate payload with the given icon_url."""
    return {
        "name": "Test",
        "issuer_url": "https://idp.example.com",
        "client_id": "client",
        "client_secret": "secret",
        "icon_url": icon_url,
    }


def test_icon_url_none_accepted():
    OIDCProviderCreate(**_make_payload(None))


def test_icon_url_valid_https_accepted():
    OIDCProviderCreate(**_make_payload("https://example.com/icon.png"))


def test_icon_url_http_rejected():
    with pytest.raises(ValueError, match="icon_url must start with https"):
        OIDCProviderCreate(**_make_payload("http://example.com/icon.png"))


def test_icon_url_empty_string_rejected():
    # Pydantic doesn't coerce "" to None — the validator runs on the raw value.
    with pytest.raises(ValueError, match="icon_url must start with https"):
        OIDCProviderCreate(**_make_payload(""))


@pytest.mark.parametrize(
    "url",
    [
        "https://192.168.1.1/icon.png",
        "https://10.0.0.5/icon.png",
        "https://172.16.0.1/icon.png",
    ],
)
def test_icon_url_private_ip_rejected(url):
    with pytest.raises(ValueError, match="private"):
        OIDCProviderCreate(**_make_payload(url))


def test_icon_url_loopback_ip_rejected():
    with pytest.raises(ValueError, match="loopback"):
        OIDCProviderCreate(**_make_payload("https://127.0.0.1/icon.png"))


def test_icon_url_link_local_or_cloud_metadata_rejected():
    # 169.254.169.254 is BOTH link-local AND a cloud-metadata IP — the guard
    # checks cloud-metadata first (per intentional ordering), so either
    # rejection message is correct. Mirrors the same pattern in
    # test_oidc_icon_helpers.test_rejects_link_local.
    with pytest.raises(ValueError, match="cloud metadata|link-local"):
        OIDCProviderCreate(**_make_payload("https://169.254.169.254/icon.png"))


def test_icon_url_hostname_accepted_no_dns():
    # "localhost" is a hostname, not a bare IP — DNS resolution is deliberately
    # not performed here (matches _validate_issuer_url policy). The runtime
    # SSRF guard (assert_safe_public_https_url) handles the bare-IP cases
    # again; hostnames are caught only by the IDP-itself-misconfigured path.
    OIDCProviderCreate(**_make_payload("https://idp.internal.corp/icon.png"))


# ─── I1: schema now delegates to runtime SSRF guard ──────────────────────
# Verifies that the wider allowlist (numeric IPs, cloud-meta, multicast,
# IPv4-mapped IPv6) is enforced at Pydantic-parse-time too, not just at
# fetch time.


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/icon.png",  # decimal-encoded 127.0.0.1
        "https://0x7f000001/icon.png",  # hex-encoded 127.0.0.1
    ],
)
def test_icon_url_numeric_encoded_ip_rejected(url):
    with pytest.raises(ValueError, match="numeric-encoded"):
        OIDCProviderCreate(**_make_payload(url))


def test_icon_url_cloud_metadata_alibaba_rejected():
    with pytest.raises(ValueError, match="cloud metadata"):
        OIDCProviderCreate(**_make_payload("https://100.100.100.200/icon.png"))


def test_icon_url_unspecified_rejected():
    with pytest.raises(ValueError, match="unspecified"):
        OIDCProviderCreate(**_make_payload("https://0.0.0.0/icon.png"))


def test_icon_url_multicast_rejected():
    with pytest.raises(ValueError, match="multicast"):
        OIDCProviderCreate(**_make_payload("https://224.0.0.1/icon.png"))


def test_icon_url_ipv4_mapped_ipv6_private_rejected():
    # ::ffff:192.168.1.1 unwraps to 192.168.1.1 → private
    with pytest.raises(ValueError, match="private"):
        OIDCProviderCreate(**_make_payload("https://[::ffff:192.168.1.1]/icon.png"))
