"""Tests for the MakerWorldService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services.makerworld import (
    MakerWorldAuthError,
    MakerWorldForbiddenError,
    MakerWorldNotFoundError,
    MakerWorldService,
    MakerWorldUnavailableError,
    MakerWorldUrlError,
)


class TestParseUrl:
    """MakerWorld URL extraction."""

    def test_strips_locale_prefix_and_slug(self):
        model, profile = MakerWorldService.parse_url(
            "https://makerworld.com/en/models/1400373-self-watering-seed-starter"
        )
        assert model == 1400373
        assert profile is None

    def test_extracts_profile_id_from_fragment(self):
        model, profile = MakerWorldService.parse_url("https://makerworld.com/en/models/1400373-slug#profileId-1452154")
        assert model == 1400373
        assert profile == 1452154

    def test_accepts_scheme_omitted(self):
        model, profile = MakerWorldService.parse_url("makerworld.com/models/999")
        assert model == 999
        assert profile is None

    def test_accepts_subdomain(self):
        # Defensive: if MakerWorld ever stands up a regional subdomain, still accept it
        model, _ = MakerWorldService.parse_url("https://www.makerworld.com/en/models/42")
        assert model == 42

    def test_rejects_non_makerworld_host(self):
        with pytest.raises(MakerWorldUrlError):
            MakerWorldService.parse_url("https://thingiverse.com/things/123")

    def test_rejects_malformed_url(self):
        # No /models/ segment anywhere in path
        with pytest.raises(MakerWorldUrlError):
            MakerWorldService.parse_url("https://makerworld.com/en/creators/foo")

    def test_rejects_empty(self):
        with pytest.raises(MakerWorldUrlError):
            MakerWorldService.parse_url("")


class TestGetDesign:
    """Metadata endpoint happy-path + error mapping."""

    @pytest.fixture
    def service(self):
        # Use a MagicMock for the client so each call can be individually stubbed
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        svc._client.get = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_returns_decoded_json(self, service):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": 1400373, "title": "Benchy"}
        service._client.get.return_value = resp

        data = await service.get_design(1400373)
        assert data == {"id": 1400373, "title": "Benchy"}
        call = service._client.get.call_args
        # The x-bbl identification headers are the whole reason MakerWorld's
        # backend returns non-empty responses; regressing this is silent-failure.
        headers = call.kwargs["headers"]
        assert headers["x-bbl-client-type"] == "web"
        assert headers["x-bbl-app-source"] == "makerworld"

    @pytest.mark.asyncio
    async def test_maps_404_to_not_found(self, service):
        resp = MagicMock()
        resp.status_code = 404
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldNotFoundError):
            await service.get_design(404)

    @pytest.mark.asyncio
    async def test_maps_401_to_auth_error(self, service):
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"code": 1, "error": "Please log in"}
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldAuthError) as exc_info:
            await service.get_design(1)
        # Upstream's own message is surfaced to the caller
        assert "Please log in" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_maps_403_to_forbidden_with_upstream_reason(self, service):
        """403 is distinct from 401: auth was valid, MakerWorld refuses the
        specific resource (content-gated, region-locked, etc.). The upstream
        reason must reach the user so they know what to do."""
        resp = MagicMock()
        resp.status_code = 403
        resp.json.return_value = {
            "code": 15001,
            "error": "This model is only available to members",
        }
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldForbiddenError) as exc_info:
            await service.get_design(1)
        assert "members" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_maps_5xx_to_unavailable(self, service):
        resp = MagicMock()
        resp.status_code = 503
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldUnavailableError):
            await service.get_design(1)

    @pytest.mark.asyncio
    async def test_maps_timeout_to_unavailable(self, service):
        service._client.get.side_effect = httpx.TimeoutException("tooo slow")

        with pytest.raises(MakerWorldUnavailableError):
            await service.get_design(1)

    @pytest.mark.asyncio
    async def test_rejects_non_dict_json(self, service):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [1, 2, 3]  # list, not dict
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldUnavailableError):
            await service.get_design(1)


class TestGetInstanceDownload:
    """The auth-gated 3MF manifest endpoint."""

    @pytest.mark.asyncio
    async def test_requires_auth_token(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(MakerWorldAuthError):
            await svc.get_instance_download(1452154)

    @pytest.mark.asyncio
    async def test_returns_signed_manifest(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient), auth_token="tok-abc")
        svc._client.get = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "name": "benchy.3mf",
            "url": "https://makerworld.bblmw.com/makerworld/model/X/Y/f.3mf?exp=1&key=k",
        }
        svc._client.get.return_value = resp

        manifest = await svc.get_instance_download(1452154)
        assert manifest["name"] == "benchy.3mf"
        assert manifest["url"].startswith("https://makerworld.bblmw.com/")
        # Token must be attached on authenticated calls
        assert svc._client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok-abc"


class TestDownload3MF:
    """SSRF guard + size cap + streaming behaviour."""

    @pytest.mark.asyncio
    async def test_rejects_non_cdn_host(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(MakerWorldUrlError):
            # Attacker-controlled URL must not be fetched
            await svc.download_3mf("https://evil.example.com/steal.3mf")

    @pytest.mark.asyncio
    async def test_rejects_loopback(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(MakerWorldUrlError):
            await svc.download_3mf("http://127.0.0.1/loot")

    @pytest.mark.asyncio
    async def test_happy_path_streams_bytes(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))

        resp = MagicMock()
        resp.status_code = 200

        async def _chunks():
            yield b"PK\x03\x04"  # 3MF = zip magic
            yield b"rest of file"

        resp.aiter_bytes = lambda: _chunks()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=None)
        svc._client.stream = MagicMock(return_value=ctx)

        payload, filename = await svc.download_3mf(
            "https://makerworld.bblmw.com/makerworld/model/X/Y/foo.3mf?exp=1&key=k"
        )
        assert payload.startswith(b"PK\x03\x04")
        assert filename == "foo.3mf"


class TestFetchThumbnail:
    """Proxy the CDN thumbnails so img-src CSP doesn't need to allow external hosts."""

    @pytest.fixture
    def service(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        svc._client.get = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_rejects_non_cdn_host(self, service):
        with pytest.raises(MakerWorldUrlError):
            await service.fetch_thumbnail("https://evil.example.com/img.jpg")

    @pytest.mark.asyncio
    async def test_rejects_loopback(self, service):
        # SSRF: don't let anyone abuse this as an open proxy toward 127.0.0.1
        with pytest.raises(MakerWorldUrlError):
            await service.fetch_thumbnail("http://127.0.0.1/secret.jpg")

    @pytest.mark.asyncio
    async def test_rejects_html_content_type_even_with_image_extension(self, service):
        # An upstream error page (HTML) at a .jpg URL must be refused —
        # otherwise we'd forward it to the browser under an image framing.
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "text/html"}
        resp.content = b"<html>error page</html>"
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldUnavailableError):
            await service.fetch_thumbnail("https://makerworld.bblmw.com/makerworld/model/X/cover.jpg")

    @pytest.mark.asyncio
    async def test_happy_path_with_proper_image_content_type(self, service):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "image/jpeg; charset=binary"}
        resp.content = b"\xff\xd8\xff\xe0JFIF"  # JPEG magic bytes
        service._client.get.return_value = resp

        payload, content_type = await service.fetch_thumbnail(
            "https://makerworld.bblmw.com/makerworld/model/X/cover.jpg"
        )
        assert payload == b"\xff\xd8\xff\xe0JFIF"
        # Semi-colon params stripped
        assert content_type == "image/jpeg"

    @pytest.mark.asyncio
    async def test_infers_mime_from_extension_when_cdn_lies(self, service):
        """MakerWorld's CDN returns application/octet-stream for real PNG/JPG
        files. Relying on upstream content-type alone would fail every
        thumbnail request; fall back to the URL extension."""
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/octet-stream"}
        resp.content = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
        service._client.get.return_value = resp

        payload, content_type = await service.fetch_thumbnail(
            "https://makerworld.bblmw.com/makerworld/model/X/design/abc.png"
        )
        assert payload.startswith(b"\x89PNG")
        assert content_type == "image/png"

    @pytest.mark.asyncio
    async def test_refuses_when_no_extension_and_non_image_type(self, service):
        """If the URL carries no image extension AND upstream doesn't declare
        image/*, we can't confidently serve it as an image — refuse."""
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/octet-stream"}
        resp.content = b"who knows what this is"
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldUnavailableError):
            await service.fetch_thumbnail("https://makerworld.bblmw.com/makerworld/model/X/blob")
