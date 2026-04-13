"""Integration tests for the /gcode-viewer static-file routes.

Covers two behaviours added by the GCode viewer PR:

1. Route ordering — /gcode-viewer/* is served by explicit @app.get routes
   that are registered before the /{full_path:path} SPA catch-all, so the
   GCode viewer is never accidentally served the React app HTML.

2. Path-traversal guard — requests for paths that escape gcode_viewer/
   (e.g. /gcode-viewer/../main.py) must return 403, not the file contents.
"""

import pytest
from httpx import AsyncClient


class TestGCodeViewerRouteOrdering:
    """Verify the /gcode-viewer routes are reachable and distinct from the SPA."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_gcode_viewer_index_does_not_fall_through_to_spa(
        self, async_client: AsyncClient
    ):
        """GET /gcode-viewer/ must not return the React SPA index.html.

        If route ordering is broken the SPA catch-all returns 200 with
        Content-Type: text/html and a <div id="root"> body.  The correct
        response is either 200 (gcode_viewer/index.html present) or 404
        (directory absent in CI) — never the SPA shell.
        """
        response = await async_client.get("/gcode-viewer/")
        # 200 or 404 are both acceptable depending on whether gcode_viewer/
        # exists in the test environment; the SPA catch-all always returns 200.
        assert response.status_code in (200, 404)
        # If a body came back it must NOT be the React SPA shell.
        assert b'<div id="root">' not in response.content

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_gcode_viewer_no_trailing_slash_redirects_or_responds(
        self, async_client: AsyncClient
    ):
        """GET /gcode-viewer (no trailing slash) is handled by the explicit route."""
        response = await async_client.get("/gcode-viewer", follow_redirects=True)
        assert response.status_code in (200, 404)
        assert b'<div id="root">' not in response.content


class TestGCodeViewerPathTraversal:
    """Verify the path-traversal guard on /gcode-viewer/{file_path:path}.

    HTTP clients (and servers) normalise plain `..` segments before the
    request reaches a route handler, so `/gcode-viewer/../x` becomes `/x`
    and hits the SPA catch-all rather than our guard — that normalisation is
    itself a defence layer.  The actual at-risk form is URL-encoded dots
    (`%2E%2E`) which survive normalisation and land in {file_path:path} as
    the literal string `../x`.  We test that form here.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_encoded_dotdot_traversal_is_forbidden(self, async_client: AsyncClient):
        """GET /gcode-viewer/%2E%2E/main.py must return 403.

        %2E%2E URL-decodes to .. which is not normalised away by httpx/
        Starlette, so it reaches _gcode_viewer_response as '../main.py'.
        Path.is_relative_to(gcode_viewer_dir) then blocks it with 403.
        """
        response = await async_client.get("/gcode-viewer/%2E%2E/main.py")
        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_encoded_nested_dotdot_traversal_is_forbidden(self, async_client: AsyncClient):
        """GET /gcode-viewer/js/%2E%2E/%2E%2E/main.py must return 403."""
        response = await async_client.get("/gcode-viewer/js/%2E%2E/%2E%2E/main.py")
        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nonexistent_safe_path_returns_404(self, async_client: AsyncClient):
        """A safe but nonexistent path returns 404, not 403."""
        response = await async_client.get("/gcode-viewer/does-not-exist.js")
        assert response.status_code == 404
