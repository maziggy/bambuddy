"""The file manager says a slow listing is slow, not that the printer is gone (TO-C13)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_ftp import FileListResult

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize(
    ("result", "warnings"),
    [
        (FileListResult(files=[], available=False, timed_out=True), ["listing_timed_out"]),
        (FileListResult(files=[], available=False), ["printer_unavailable"]),
        (FileListResult(files=[], available=True), []),
    ],
)
async def test_the_listing_warning_says_which(async_client: AsyncClient, printer_factory, result, warnings):
    printer = await printer_factory()
    with patch("backend.app.api.routes.printers.list_files_result_async", new=AsyncMock(return_value=result)):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/files?path=/ipcam")

    assert response.status_code == 200
    assert response.json()["warnings"] == warnings
