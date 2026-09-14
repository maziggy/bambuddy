"""The mesh route: `GET /library/files/{id}/mesh` (#2878).

The service behind it is covered by `tests/unit/services/test_mesh_export.py`. Everything here is a
route decision — the ownership gate, the status a refusal carries, the two response headers and the
conditional request — none of which the service tests can pin.
"""

import struct
from pathlib import Path

import pytest
from httpx import AsyncClient


def _write_binary_stl(path: Path):
    """A tetrahedron: the smallest mesh that encloses a volume."""
    a, b, c, d = (0, 0, 0), (10, 0, 0), (5, 9, 0), (5, 3, 8)
    triangles = [(a, b, c), (a, b, d), (b, c, d), (c, a, d)]
    with path.open("wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(triangles)))
        for tri in triangles:
            fh.write(struct.pack("<3f", 0.0, 0.0, 1.0))
            for vertex in tri:
                fh.write(struct.pack("<3f", *vertex))
            fh.write(struct.pack("<H", 0))


@pytest.fixture
async def mesh_file_factory(db_session, tmp_path):
    """A LibraryFile whose bytes really exist, so the route reaches the exporter."""
    counter = [0]

    async def _create(filename="widget.stl", write=True, **kwargs):
        from backend.app.models.library import LibraryFile

        counter[0] += 1
        path = tmp_path / f"{counter[0]}_{Path(filename).name}"
        if write:
            _write_binary_stl(path)
        defaults = {
            "filename": filename,
            "file_path": str(path),
            "file_type": Path(filename).suffix.lstrip("."),
            "file_size": path.stat().st_size if write else 0,
        }
        defaults.update(kwargs)
        row = LibraryFile(**defaults)
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return _create


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_model_is_returned_as_stl(async_client: AsyncClient, mesh_file_factory):
    file = await mesh_file_factory()
    response = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("model/stl")
    assert len(response.content) > 84  # header + at least one triangle


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_sliced_file_is_refused_with_400(async_client: AsyncClient, mesh_file_factory):
    """Sliced output carries toolpaths, and trimesh cannot read its scene graph. A specific
    status, because the caller can act on it — the G-code viewer is the right tool."""
    file = await mesh_file_factory(filename="tray.gcode.3mf", write=False)
    response = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_row_whose_file_is_gone_is_404(async_client: AsyncClient, mesh_file_factory):
    file = await mesh_file_factory(write=False)
    response = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_file_with_no_geometry_is_422_not_500(async_client: AsyncClient, mesh_file_factory, tmp_path):
    """The request was well formed and the server is healthy — this file has nothing to give.
    A 500 would send the caller looking for an outage."""
    file = await mesh_file_factory()
    Path(file.file_path).write_bytes(b"not a mesh, but long enough to pass the size floor" * 20)
    response = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unknown_id_is_404(async_client: AsyncClient):
    response = await async_client.get("/api/v1/library/files/999999/mesh")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_non_ascii_filename_does_not_break_the_header(async_client: AsyncClient, mesh_file_factory):
    """Response headers are latin-1. Building `Content-Disposition` with an f-string raises
    UnicodeEncodeError inside Starlette on the way out — which is a 500 with no useful body, and
    only for users whose filenames are not English. Aimed at the route rather than the helper,
    because the route is what would regress."""
    file = await mesh_file_factory(filename="крышка.stl")
    response = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")
    assert "filename*=UTF-8''" in disposition


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_newline_in_an_external_filename_cannot_reach_the_header(async_client: AsyncClient, mesh_file_factory):
    """An external name is never validated on the way in — `scan_external_folder` takes whatever
    the mount offers, and a POSIX filename may contain a newline. `build_content_disposition`
    strips quotes and backslashes but not control characters, so the name goes through
    `safe_download_filename` first."""
    file = await mesh_file_factory(filename="bad\nname.stl", is_external=True)
    response = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    assert response.status_code == 200
    assert "\n" not in response.headers["content-disposition"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_response_revalidates_rather_than_caching_blind(async_client: AsyncClient, mesh_file_factory):
    """`max-age` alone was wrong. A tracked EXTERNAL file replaced over the mount keeps its row and
    its id — `scan_external_folder` refreshes `fs_modified_at` and moves on — so a client holding a
    dated copy would show the wrong model for as long as the age allowed."""
    file = await mesh_file_factory()
    response = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    assert response.status_code == 200
    assert "no-cache" in response.headers["cache-control"]
    assert response.headers["cache-control"].startswith("private")
    assert response.headers["etag"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_matching_etag_answers_304(async_client: AsyncClient, mesh_file_factory):
    """And answers it before the parse, which is the point: a revalidation costs a row read
    rather than seconds of CPU."""
    file = await mesh_file_factory()
    first = await async_client.get(f"/api/v1/library/files/{file.id}/mesh")
    etag = first.headers["etag"]

    second = await async_client.get(f"/api/v1/library/files/{file.id}/mesh", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert second.content == b""


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_changed_file_gets_a_new_etag(async_client: AsyncClient, mesh_file_factory, db_session):
    """The validator has to move when the bytes do, or it is decoration. `file_size` and
    `fs_modified_at` are what change when an external file is replaced; the id does not."""
    file = await mesh_file_factory()
    before = (await async_client.get(f"/api/v1/library/files/{file.id}/mesh")).headers["etag"]

    file.file_size = (file.file_size or 0) + 1
    db_session.add(file)
    await db_session.commit()

    after = (await async_client.get(f"/api/v1/library/files/{file.id}/mesh")).headers["etag"]
    assert after != before
