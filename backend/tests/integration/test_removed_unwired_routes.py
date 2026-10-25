"""Routes for two features that never had a UI are gone (audit A1-02, A2-01).

Archive rescan and project file attachments were reachable only through the
API, while the wiki described buttons and a section that did not exist.
The project cover image keeps its own routes and storage.
"""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/archives/{archive}/rescan"),
        ("POST", "/api/v1/archives/rescan-all"),
        ("POST", "/api/v1/projects/{project}/attachments"),
        ("GET", "/api/v1/projects/{project}/attachments/notes.pdf"),
        ("DELETE", "/api/v1/projects/{project}/attachments/notes.pdf"),
    ],
)
async def test_route_is_gone(async_client: AsyncClient, printer_factory, archive_factory, method, path):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    project = (await async_client.post("/api/v1/projects/", json={"name": "Gone"})).json()

    response = await async_client.request(method, path.format(archive=archive.id, project=project["id"]))

    assert response.status_code in (404, 405), response.text
    if response.status_code == 404:
        # The router's (or the API catch-all's) generic answer, not the old
        # route's own "Attachment not found" for a missing file.
        assert response.json()["detail"].lower() == "not found"
