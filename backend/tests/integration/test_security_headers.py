"""Integration tests for security_headers_middleware (#1191).

Default behaviour is strict: ``X-Frame-Options: SAMEORIGIN`` plus
``frame-ancestors 'none'`` on the catch-all route, ``frame-ancestors 'self'``
on /gcode-viewer/. Operators can opt into iframe embedding from trusted
origins (e.g. Home Assistant on a different port) via the
``TRUSTED_FRAME_ORIGINS`` env var; when set, X-Frame-Options is dropped and
``frame-ancestors`` includes the allowlist.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

# ─── helpers ──────────────────────────────────────────────────────────────


def _parse_origins(value: str) -> tuple[str, ...]:
    """Re-import the parser with a specific env var set, return its result.

    Uses a fresh import so the module-level _TRUSTED_FRAME_ORIGINS is
    re-evaluated against the patched os.environ.
    """
    import os

    from backend.app import main as main_module

    old = os.environ.get("TRUSTED_FRAME_ORIGINS")
    try:
        if value is None:
            os.environ.pop("TRUSTED_FRAME_ORIGINS", None)
        else:
            os.environ["TRUSTED_FRAME_ORIGINS"] = value
        # Function reads from os.environ each call.
        return main_module._parse_trusted_frame_origins()
    finally:
        if old is None:
            os.environ.pop("TRUSTED_FRAME_ORIGINS", None)
        else:
            os.environ["TRUSTED_FRAME_ORIGINS"] = old


# ─── env-var parsing ──────────────────────────────────────────────────────


class TestParseTrustedFrameOrigins:
    """Unit tests for _parse_trusted_frame_origins."""

    def test_empty_env_returns_empty_tuple(self):
        assert _parse_origins("") == ()

    def test_unset_env_returns_empty_tuple(self):
        assert _parse_origins(None) == ()  # type: ignore[arg-type]

    def test_single_origin(self):
        assert _parse_origins("http://homeassistant.local:8123") == ("http://homeassistant.local:8123",)

    def test_multiple_origins(self):
        result = _parse_origins("http://homeassistant.local:8123,https://ha.example.com")
        assert result == ("http://homeassistant.local:8123", "https://ha.example.com")

    def test_whitespace_around_entries_stripped(self):
        result = _parse_origins("  http://a.local:1 ,   https://b.local:2  ")
        assert result == ("http://a.local:1", "https://b.local:2")

    def test_empty_segment_skipped(self):
        result = _parse_origins("http://a.local,,https://b.local")
        assert result == ("http://a.local", "https://b.local")

    def test_non_http_scheme_dropped(self):
        # ftp://, javascript:, file:// etc. — never a valid frame ancestor.
        assert _parse_origins("ftp://attacker.example,http://ok.local") == ("http://ok.local",)
        assert _parse_origins("javascript:alert(1)") == ()

    def test_missing_host_dropped(self):
        # "http://" with no host
        assert _parse_origins("http://") == ()

    def test_path_dropped(self):
        # frame-ancestors only takes scheme://host[:port], no path
        assert _parse_origins("http://ha.local/dashboard") == ()

    def test_query_or_fragment_dropped(self):
        assert _parse_origins("http://ha.local?foo=1") == ()
        assert _parse_origins("http://ha.local#frag") == ()

    def test_wildcard_in_host_dropped(self):
        # Wildcards would defeat the allowlist purpose; reject explicitly.
        assert _parse_origins("http://*.example.com") == ()

    def test_root_path_kept(self):
        # Trailing slash is a degenerate but harmless path; treat as bare host.
        assert _parse_origins("http://ha.local:8123/") == ("http://ha.local:8123",)


# ─── HTTP integration: middleware emits expected headers ──────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_default_headers_strict(async_client: AsyncClient, monkeypatch):
    """Without env var: X-Frame-Options=SAMEORIGIN and frame-ancestors 'none'."""
    monkeypatch.delenv("TRUSTED_FRAME_ORIGINS", raising=False)
    # Re-import the module-level constant so the middleware closes over the new value.
    from backend.app import main as main_module

    monkeypatch.setattr(main_module, "_TRUSTED_FRAME_ORIGINS", ())

    resp = await async_client.get("/api/v1/auth/status")
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_trusted_origins_relaxes_csp_and_drops_xfo(async_client: AsyncClient, monkeypatch):
    """With env var set: X-Frame-Options is absent, frame-ancestors lists the origins."""
    from backend.app import main as main_module

    monkeypatch.setattr(
        main_module,
        "_TRUSTED_FRAME_ORIGINS",
        ("http://homeassistant.local:8123",),
    )

    resp = await async_client.get("/api/v1/auth/status")
    assert "X-Frame-Options" not in resp.headers
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'self' http://homeassistant.local:8123;" in csp
    assert "'none'" not in csp.split("frame-ancestors")[1].split(";")[0]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_trusted_origins_applies_to_docs_branch(async_client: AsyncClient, monkeypatch):
    """The /docs CSP also honors the allowlist (consistent with main app)."""
    from backend.app import main as main_module

    monkeypatch.setattr(
        main_module,
        "_TRUSTED_FRAME_ORIGINS",
        ("https://ha.example.com",),
    )

    resp = await async_client.get("/docs")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'self' https://ha.example.com;" in csp


@pytest.mark.asyncio
@pytest.mark.integration
async def test_other_security_headers_unchanged(async_client: AsyncClient, monkeypatch):
    """Other headers (X-Content-Type-Options, Referrer-Policy) are not affected."""
    from backend.app import main as main_module

    # Test in both modes — headers should be the same regardless.
    for origins in [(), ("http://homeassistant.local:8123",)]:
        monkeypatch.setattr(main_module, "_TRUSTED_FRAME_ORIGINS", origins)
        resp = await async_client.get("/api/v1/auth/status")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
