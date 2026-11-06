"""/assets serving: gzip and long-lived caching for the frontend build (#3175)."""

from __future__ import annotations

import gzip

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Mount

from backend.app.core import static_assets
from backend.app.core.static_assets import (
    IMMUTABLE_CACHE_CONTROL,
    REVALIDATE_CACHE_CONTROL,
    AssetStaticFiles,
    accepts_gzip,
)

BUNDLE = b"export const answer = 42;\n" * 400  # ~10 KB, compresses well
BUNDLE_NAME = "index-CdoB5JwO.js"


@pytest.fixture
def assets_dir(tmp_path):
    root = tmp_path / "assets"
    (root / "pdfjs" / "cmaps").mkdir(parents=True)
    (root / BUNDLE_NAME).write_bytes(BUNDLE)
    (root / "index-Dk3mQx9a.css").write_bytes(b"body { color: red; }\n" * 200)
    (root / "tiny-AbCdEfGh.js").write_bytes(b"export {};\n")
    (root / "logo-AbCdEfGh.png").write_bytes(b"\x89PNG" + b"\x00" * 4000)
    (root / "pdfjs" / "cmaps" / "Adobe-Japan1-UCS2.bcmap").write_bytes(b"\x00" * 4000)
    (root / "pdfjs" / "wasm.json").write_bytes(b'{"a": 1}' * 400)
    static_assets._gzip_cache.clear()
    yield root
    static_assets._gzip_cache.clear()


@pytest.fixture
async def client(assets_dir):
    app = Starlette(routes=[Mount("/assets", app=AssetStaticFiles(directory=assets_dir))])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _get(client: AsyncClient, path: str, **headers: str):
    """GET without httpx decoding the body, so the raw bytes can be checked."""
    request = client.build_request("GET", path, headers=headers)
    response = await client.send(request, stream=True)
    raw = b"".join([chunk async for chunk in response.aiter_raw()])
    await response.aclose()
    return response, raw


class TestCompression:
    @pytest.mark.asyncio
    async def test_bundle_is_gzipped_when_accepted(self, client):
        response, raw = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip, deflate, br"})

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert response.headers["vary"] == "Accept-Encoding"
        assert response.headers["content-type"].startswith("text/javascript")
        assert int(response.headers["content-length"]) == len(raw) < len(BUNDLE)
        assert gzip.decompress(raw) == BUNDLE
        assert "accept-ranges" not in response.headers

    @pytest.mark.asyncio
    async def test_identity_when_gzip_not_accepted(self, client):
        response, raw = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "identity"})

        assert response.status_code == 200
        assert "content-encoding" not in response.headers
        assert response.headers["vary"] == "Accept-Encoding"
        assert raw == BUNDLE

    @pytest.mark.asyncio
    async def test_gzip_q0_is_a_refusal(self, client):
        response, raw = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip;q=0, *"})

        assert "content-encoding" not in response.headers
        assert raw == BUNDLE

    @pytest.mark.asyncio
    async def test_variants_carry_different_etags(self, client):
        plain, _ = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "identity"})
        zipped, _ = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip"})

        assert plain.headers["etag"] != zipped.headers["etag"]

    @pytest.mark.asyncio
    async def test_gzip_etag_revalidates_to_304(self, client):
        first, _ = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip"})
        again, raw = await _get(
            client,
            f"/assets/{BUNDLE_NAME}",
            **{"Accept-Encoding": "gzip", "If-None-Match": first.headers["etag"]},
        )

        assert again.status_code == 304
        assert raw == b""
        assert again.headers["etag"] == first.headers["etag"]
        assert again.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL

    @pytest.mark.asyncio
    async def test_range_request_is_served_uncompressed(self, client):
        response, raw = await _get(
            client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip", "Range": "bytes=0-9"}
        )

        assert response.status_code == 206
        assert "content-encoding" not in response.headers
        assert raw == BUNDLE[:10]

    @pytest.mark.asyncio
    async def test_head_is_not_compressed(self, client):
        response = await client.head(f"/assets/{BUNDLE_NAME}", headers={"Accept-Encoding": "gzip"})

        assert response.status_code == 200
        assert "content-encoding" not in response.headers
        assert response.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["tiny-AbCdEfGh.js", "logo-AbCdEfGh.png", "pdfjs/cmaps/Adobe-Japan1-UCS2.bcmap"])
    async def test_small_or_binary_files_are_not_compressed(self, client, name):
        response, _ = await _get(client, f"/assets/{name}", **{"Accept-Encoding": "gzip"})

        assert response.status_code == 200
        assert "content-encoding" not in response.headers

    @pytest.mark.asyncio
    async def test_compressed_body_is_reused(self, client, monkeypatch):
        calls = []
        real = static_assets._gzip_file
        monkeypatch.setattr(static_assets, "_gzip_file", lambda path: calls.append(path) or real(path))

        for _ in range(3):
            response, raw = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip"})
            assert gzip.decompress(raw) == BUNDLE

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_rebuilt_file_is_compressed_again(self, client, assets_dir):
        await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip"})
        (assets_dir / BUNDLE_NAME).write_bytes(b"export const answer = 43;\n" * 500)

        _, raw = await _get(client, f"/assets/{BUNDLE_NAME}", **{"Accept-Encoding": "gzip"})

        assert gzip.decompress(raw) == b"export const answer = 43;\n" * 500


class TestCacheControl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", [BUNDLE_NAME, "index-Dk3mQx9a.css", "logo-AbCdEfGh.png"])
    async def test_hashed_files_are_immutable(self, client, name):
        response, _ = await _get(client, f"/assets/{name}")

        assert response.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["pdfjs/cmaps/Adobe-Japan1-UCS2.bcmap", "pdfjs/wasm.json"])
    async def test_pdfjs_runtime_files_are_revalidated(self, client, name):
        response, _ = await _get(client, f"/assets/{name}", **{"Accept-Encoding": "gzip"})

        assert response.status_code == 200
        assert response.headers["cache-control"] == REVALIDATE_CACHE_CONTROL

    @pytest.mark.asyncio
    async def test_identity_304_keeps_cache_control(self, client):
        first, _ = await _get(client, f"/assets/{BUNDLE_NAME}")
        again, _ = await _get(client, f"/assets/{BUNDLE_NAME}", **{"If-None-Match": first.headers["etag"]})

        assert again.status_code == 304
        assert again.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL

    @pytest.mark.asyncio
    async def test_missing_old_chunk_is_404_not_html(self, client):
        """A tab still running the previous build asks for chunks the new build
        deleted. It must get a 404 so the frontend can reload, never a page."""
        response, _ = await _get(client, "/assets/ProfilesPage-OldHash1.js", **{"Accept-Encoding": "gzip"})

        assert response.status_code == 404
        assert "cache-control" not in response.headers


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, False),
        ("", False),
        ("gzip", True),
        ("GZIP", True),
        ("gzip, deflate, br, zstd", True),
        ("br", False),
        ("identity", False),
        ("gzip;q=0", False),
        ("gzip; q=0.5", True),
        ("*", True),
        ("*;q=0", False),
        ("gzip;q=0, *", False),
        ("br, *;q=0.1", True),
        ("gzip;q=abc", False),
    ],
)
def test_accepts_gzip(header, expected):
    assert accepts_gzip(header) is expected
