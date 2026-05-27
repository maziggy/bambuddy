"""Tests for the SPA index.html cache-control behaviour.

Background: Vite emits content-hashed JS/CSS bundle filenames (e.g.
``index-JRaF_JhW.js``), so those assets are safe to cache forever — the
hash changes when their content changes. The wrapping HTML, however, is
the only file that knows which hash is current. Without explicit cache
directives, Chromium falls back to heuristic caching (typically 10% of
the time since Last-Modified) and on long-running kiosks happily serves
stale HTML across browser restarts. That stale HTML references an old
bundle hash, which is also still in disk cache, so the kiosk runs
pre-deploy JS indefinitely without ever knowing why.

Reproduced in the wild during the #1133 rollout — the SpoolBuddy
display kept serving the pre-fix picker for hours after every
cache-clear attempt because Chromium would re-seed its cache from
disk on next start. Fixed by sending ``no-cache, must-revalidate`` on
the two routes that serve ``index.html``.

These tests pin that behaviour so it can't silently regress (e.g. a
later PR adding a third index.html serve route forgetting the headers,
or someone tightening the policy to ``max-age=N`` and breaking deploys
in subtle ways).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

# index.html is served by two distinct routes:
#   - "/" — root entry
#   - the SPA catch-all (any unrecognised path that isn't /api/)
# Both must carry the same headers; testing both individually is the
# only guard against one being added later without the other.
HTML_ROUTES = [
    pytest.param("/", id="root"),
    # Catch-all routes a path like /spoolbuddy/ to index.html. The trailing
    # slash matters — without it FastAPI redirects, which would skip the
    # cache-control middleware. Tested as a real-world client URL.
    pytest.param("/spoolbuddy/", id="spa-catchall-spoolbuddy"),
    pytest.param("/printers", id="spa-catchall-printers"),
]


@pytest.fixture
def fake_static_index(monkeypatch, tmp_path):
    """Provide a minimal ``static/index.html`` so the route handlers don't
    fall through to their "frontend not built" JSON branch.

    The ``backend-test`` Dockerfile.test target intentionally doesn't bake
    in the built frontend (saves ~30 s of build time per test run), and
    contributors running ``pytest backend/tests/`` from a checkout without
    a prior ``npm run build`` would also miss it.  The test asserts the
    cache-header contract on the index.html serve path, not the bundle
    contents — so a one-line stub is enough to exercise the real route
    handlers in ``main.py:serve_frontend`` / ``main.py:serve_spa`` against
    a real ``index.html`` on disk.
    """
    from backend.app import main as main_mod
    from backend.app.core import config as config_mod

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>stub</title>")

    monkeypatch.setattr(config_mod.settings, "static_dir", static_dir)
    # main.py imports `settings as app_settings`, so the route handlers
    # resolve `app_settings.static_dir` per request. Patch the import-site
    # binding too in case future refactors stop sharing the singleton.
    monkeypatch.setattr(main_mod.app_settings, "static_dir", static_dir)
    return static_dir


@pytest.mark.asyncio
@pytest.mark.parametrize(("path",), HTML_ROUTES)
async def test_index_html_emits_no_cache_directive(async_client: AsyncClient, fake_static_index, path: str):
    """Every index.html serve must emit ``Cache-Control: no-cache,
    must-revalidate`` — kiosks rely on this to pick up new builds without
    operator intervention."""
    response = await async_client.get(path)

    # Both serve routes should return 200 with HTML content type.
    assert response.status_code == 200, f"Expected 200 for {path}, got {response.status_code}: {response.text[:200]}"
    assert response.headers.get("content-type", "").startswith("text/html"), (
        f"{path} returned non-HTML content-type: {response.headers.get('content-type')}"
    )

    # The Cache-Control header is the actual contract under test.
    cache_control = response.headers.get("cache-control", "")
    assert "no-cache" in cache_control, (
        f"{path} missing 'no-cache' in Cache-Control header (got: {cache_control!r}). "
        f"Without this kiosks serve stale HTML across browser restarts and never "
        f"pick up new builds."
    )
    assert "must-revalidate" in cache_control, (
        f"{path} missing 'must-revalidate' in Cache-Control header (got: {cache_control!r}). "
        f"This belt-and-braces directive prevents stale-while-revalidate-style "
        f"intermediaries from serving cached HTML even when it's expired."
    )


@pytest.mark.asyncio
async def test_api_routes_unaffected_by_html_cache_headers(async_client: AsyncClient):
    """Defensive: the cache-control directive must NOT leak onto API
    responses. API responses set their own headers (or none at all) per
    endpoint; a global ``no-cache`` would silently disable the React
    Query cache wins we depend on for snappy UI updates."""
    response = await async_client.get("/api/v1/printers")

    # We don't care about success/failure here — just that no cache
    # directive was inherited from the HTML serve path. (The endpoint
    # itself may 401/403 depending on auth state in the test fixture
    # which is fine; what matters is the response shape.)
    cache_control = response.headers.get("cache-control", "")
    assert "no-cache" not in cache_control or "private" in cache_control, (
        f"API route /api/v1/printers leaked HTML cache-control: {cache_control!r}. "
        f"If a 'no-cache' directive is intentional on an API endpoint it should be "
        f"set per-route, not inherited from the SPA HTML path."
    )
