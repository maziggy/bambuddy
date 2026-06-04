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
async def test_default_block_img_src_excludes_https(async_client: AsyncClient, monkeypatch):
    """#1333 regression guard: the default SPA CSP must NOT allow img-src https:.

    Bambuddy's policy for external images is a backend proxy (see
    /api/v1/makerworld/thumbnail and /api/v1/auth/oidc/providers/{id}/icon),
    not a CSP relaxation. If a future change adds ``https:`` to img-src to
    "fix" a broken-image, the proxy pattern silently degrades into a
    do-nothing layer and the entire SPA gains a hot-link surface.
    """
    from backend.app import main as main_module

    monkeypatch.setattr(main_module, "_TRUSTED_FRAME_ORIGINS", ())

    resp = await async_client.get("/api/v1/auth/status")
    csp = resp.headers.get("Content-Security-Policy", "")
    # Extract the img-src directive — splits on ';' for safety against
    # neighbouring directives that happen to contain the substring.
    img_src_directive = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("img-src")),
        "",
    )
    assert img_src_directive, f"img-src directive missing from CSP: {csp!r}"
    assert "https:" not in img_src_directive, (
        f"img-src must not allow arbitrary https: hosts (proxy external images instead); got: {img_src_directive!r}"
    )
    # Sanity: the legitimately allowed scheme sources are still present.
    assert "'self'" in img_src_directive
    assert "data:" in img_src_directive
    assert "blob:" in img_src_directive


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


# ─── #1460: nonce-based script-src so Cloudflare-injected scripts pass ────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_spa_csp_includes_per_request_script_nonce(async_client: AsyncClient):
    """SPA CSP must stamp a fresh `'nonce-…'` token into script-src (#1460).

    Cloudflare's bot-detection inline script is injected after our response
    leaves the app, with a per-load hash that defeats hash allowlisting. When
    a nonce is present in the CSP header, Cloudflare clones it onto its
    injected `<script>` and the CSP passes without `'unsafe-inline'`.
    """
    import re

    resp = await async_client.get("/api/v1/auth/status")
    csp = resp.headers.get("Content-Security-Policy", "")
    # Pull out the script-src directive (split on ';' so neighbours don't confuse us).
    script_src = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("script-src")),
        "",
    )
    assert script_src, f"script-src directive missing: {csp!r}"
    assert "'self'" in script_src, f"script-src must still allow 'self': {script_src!r}"
    # Nonce token is `'nonce-<base64url>'` where the inner value is
    # secrets.token_urlsafe(16) — about 22 url-safe chars.
    assert re.search(r"'nonce-[A-Za-z0-9_-]{16,}'", script_src), (
        f"script-src must include a 'nonce-…' token: {script_src!r}"
    )
    # We deliberately did NOT add 'unsafe-inline' alongside the nonce — that
    # would defeat the purpose of using a nonce in the first place.
    assert "'unsafe-inline'" not in script_src, (
        f"script-src must not relax to 'unsafe-inline' on the SPA route: {script_src!r}"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_spa_csp_nonce_changes_per_request(async_client: AsyncClient):
    """A nonce is only useful if it's fresh per request (#1460)."""
    import re

    nonce_re = re.compile(r"'nonce-([A-Za-z0-9_-]+)'")

    nonces = set()
    for _ in range(5):
        resp = await async_client.get("/api/v1/auth/status")
        csp = resp.headers.get("Content-Security-Policy", "")
        m = nonce_re.search(csp)
        assert m, f"no nonce in CSP: {csp!r}"
        nonces.add(m.group(1))
    # 5 random 16-byte tokens collide with probability ~0 — anything less
    # than all-5-distinct means we're handing out a stale/global nonce.
    assert len(nonces) == 5, f"nonces should be per-request, got {nonces!r}"


# ─── #1460: HEAD on PWA bootstrap routes (manifest / sw / sw-register) ───


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("path", ["/manifest.json", "/sw.js", "/sw-register.js"])
async def test_pwa_bootstrap_routes_accept_head(async_client: AsyncClient, path: str):
    """Scanners and `curl -I` HEAD-probe these — must not 405 (#1460).

    Previously these were `@app.get` only, so HEAD returned 405 Method Not
    Allowed and looked like a manifest/SW server-side bug when debugging
    Cloudflare-fronted deployments.
    """
    resp = await async_client.head(path)
    # 200 if static asset is present in the test environment, 404 if it's
    # not packaged in this checkout — but never 405.
    assert resp.status_code != 405, f"HEAD {path} returned 405 — route must accept HEAD as well as GET"
