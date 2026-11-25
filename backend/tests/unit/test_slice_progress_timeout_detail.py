"""A slow slice-progress answer is reported as slow, not as an unreachable sidecar (TO-C14).

A heavy slice can keep the sidecar from answering its progress poll within
5 s; httpx's ReadTimeout is a RequestError, so the proxy said "Slicer sidecar
unreachable". The poller retries on 503 either way; the text was wrong.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from backend.app.api.routes import slicer_presets

pytestmark = pytest.mark.asyncio


async def _poll(error: Exception) -> HTTPException:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=error)
    with (
        patch.object(slicer_presets, "_resolve_slicer_api_url", AsyncMock(return_value="http://sidecar:3000")),
        patch("httpx.AsyncClient", return_value=client),
        pytest.raises(HTTPException) as raised,
    ):
        await slicer_presets.get_preview_slice_progress(request_id="abc", db=MagicMock(), _=(None, True))
    return raised.value


async def test_a_timeout_says_the_sidecar_is_busy():
    refused = await _poll(httpx.ReadTimeout("read timed out"))

    assert refused.status_code == 503
    assert "unreachable" not in refused.detail
    assert "did not answer in time" in refused.detail


async def test_a_refused_connection_is_still_unreachable():
    refused = await _poll(httpx.ConnectError("connection refused"))

    assert (refused.status_code, refused.detail) == (503, "Slicer sidecar unreachable")
