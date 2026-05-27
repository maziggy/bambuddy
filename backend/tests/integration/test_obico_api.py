"""Integration tests for Obico API endpoints (#172 follow-up).

Verifies the /obico/cached-frame/{nonce} endpoint used by Obico's ML API to fetch
pre-captured JPEG frames. This endpoint lets the detection loop sidestep Obico's
hardcoded 5s read timeout by pre-populating a cache before issuing the ML call.
"""

import pytest
from httpx import AsyncClient

from backend.app.services.obico_detection import _frame_cache, stash_frame

FAKE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


@pytest.fixture(autouse=True)
def clear_cache():
    _frame_cache.clear()
    yield
    _frame_cache.clear()


class TestObicoCachedFrame:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_valid_nonce_returns_jpeg(self, async_client: AsyncClient):
        """A stashed nonce returns the stored JPEG bytes with image/jpeg."""
        nonce = await stash_frame(FAKE_JPEG)
        response = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == FAKE_JPEG

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_nonce_is_404(self, async_client: AsyncClient):
        """An unguessable URL must not leak that the endpoint exists — return 404."""
        response = await async_client.get("/api/v1/obico/cached-frame/definitely-not-a-real-nonce")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nonce_is_single_use(self, async_client: AsyncClient):
        """A second fetch with the same nonce returns 404 — prevents replay."""
        nonce = await stash_frame(FAKE_JPEG)
        first = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert first.status_code == 200
        second = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert second.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_endpoint_is_public(self, async_client: AsyncClient):
        """Obico's ML API can't send auth headers, so the nonce IS the credential.
        The path must be in PUBLIC_API_PATTERNS (no auth wall)."""
        nonce = await stash_frame(FAKE_JPEG)
        # Intentionally omit any auth headers even if the fixture would normally inject them
        response = await async_client.get(
            f"/api/v1/obico/cached-frame/{nonce}",
            headers={},  # no Authorization header
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_response_is_not_cached(self, async_client: AsyncClient):
        """Browsers/proxies must not hold onto the image after Obico consumes it."""
        nonce = await stash_frame(FAKE_JPEG)
        response = await async_client.get(f"/api/v1/obico/cached-frame/{nonce}")
        assert response.status_code == 200
        assert "no-store" in response.headers.get("cache-control", "")
