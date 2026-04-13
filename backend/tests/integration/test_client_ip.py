"""Unit tests for _get_client_ip (M-R9-A / M-R10-A).

Covers:
- Direct connection without TRUSTED_PROXY_IPS → returns client.host
- Trusted proxy with XFF → walks right-to-left, returns first non-proxy IP
- Spoofed XFF from an untrusted client → client.host is returned
- Multiple trusted proxies in chain → returns leftmost non-proxy entry
- All XFF entries are trusted proxies → falls back to leftmost
- Empty XFF header with trusted proxy → returns direct_ip
- No client (client=None) → returns unique per-request token
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_request(client_host: str | None, xff: str = "") -> MagicMock:
    """Create a minimal mock Request with given client.host and X-Forwarded-For."""
    req = MagicMock()
    if client_host is None:
        req.client = None
    else:
        req.client = MagicMock()
        req.client.host = client_host
    req.headers = MagicMock()
    req.headers.get = lambda key, default="": xff if key == "X-Forwarded-For" else default
    return req


def _call(request, trusted: frozenset[str]) -> str:
    from backend.app.api.routes.auth import _get_client_ip

    with patch("backend.app.api.routes.auth._TRUSTED_PROXY_IPS", trusted):
        return _get_client_ip(request)


# ---------------------------------------------------------------------------
# No proxy configured (TRUSTED_PROXY_IPS empty)
# ---------------------------------------------------------------------------


def test_no_proxy_returns_client_host():
    req = _make_request("1.2.3.4")
    assert _call(req, frozenset()) == "1.2.3.4"


def test_no_proxy_xff_ignored():
    """XFF must be ignored when TRUSTED_PROXY_IPS is not set."""
    req = _make_request("1.2.3.4", xff="9.9.9.9")
    assert _call(req, frozenset()) == "1.2.3.4"


# ---------------------------------------------------------------------------
# Trusted proxy present; direct peer is the proxy
# ---------------------------------------------------------------------------


def test_trusted_proxy_returns_rightmost_non_proxy():
    """Single proxy: XFF = client_ip; direct_ip = proxy_ip → return client."""
    proxy = "10.0.0.1"
    client = "203.0.113.5"
    req = _make_request(proxy, xff=client)
    assert _call(req, frozenset({proxy})) == client


def test_trusted_proxy_chain_skips_proxy_ips():
    """Multi-hop: client → proxy1 → proxy2 (direct) → app.
    XFF = 'client, proxy1'; direct = proxy2.  Should return client."""
    proxy1 = "10.0.0.1"
    proxy2 = "10.0.0.2"
    client = "198.51.100.7"
    req = _make_request(proxy2, xff=f"{client}, {proxy1}")
    assert _call(req, frozenset({proxy1, proxy2})) == client


def test_all_xff_entries_are_proxies_falls_back_to_leftmost():
    """When every XFF entry is a trusted proxy, return the leftmost (original) entry."""
    proxy1 = "10.0.0.1"
    proxy2 = "10.0.0.2"
    req = _make_request(proxy2, xff=f"{proxy1}, {proxy2}")
    assert _call(req, frozenset({proxy1, proxy2})) == proxy1


def test_empty_xff_with_trusted_proxy_returns_direct_ip():
    """Trusted proxy but no XFF header → fall through to direct_ip."""
    proxy = "10.0.0.1"
    req = _make_request(proxy, xff="")
    assert _call(req, frozenset({proxy})) == proxy


# ---------------------------------------------------------------------------
# Spoofed XFF from an untrusted client
# ---------------------------------------------------------------------------


def test_spoofed_xff_from_untrusted_client_ignored():
    """Client not in TRUSTED_PROXY_IPS → XFF is ignored; client.host returned."""
    untrusted_client = "203.0.113.99"
    req = _make_request(untrusted_client, xff="1.1.1.1")
    assert _call(req, frozenset({"10.0.0.1"})) == untrusted_client


# ---------------------------------------------------------------------------
# No client (transport layer provides no address)
# ---------------------------------------------------------------------------


def test_no_client_returns_unique_token():
    """When request.client is None, each call returns a unique rate-limit sentinel."""
    req1 = _make_request(None)
    req2 = _make_request(None)
    ip1 = _call(req1, frozenset())
    ip2 = _call(req2, frozenset())
    assert ip1.startswith("__no_ip_")
    assert ip2.startswith("__no_ip_")
    assert ip1 != ip2, "Each missing-client request must get a distinct sentinel"


# ---------------------------------------------------------------------------
# Whitespace in XFF values
# ---------------------------------------------------------------------------


def test_xff_with_extra_whitespace_trimmed():
    """IPs in XFF with leading/trailing spaces are handled correctly."""
    proxy = "10.0.0.1"
    client = "192.0.2.33"
    req = _make_request(proxy, xff=f"  {client}  ,  {proxy}  ")
    assert _call(req, frozenset({proxy})) == client
