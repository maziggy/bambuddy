"""Tests for the MakerWorldService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError

import httpx
import pytest

from backend.app.services.makerworld import (
    _MAX_3MF_BYTES,
    MAKERWORLD_API_BASE,
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


class TestApiBase:
    """Sanity check on the module-level constant — changing it is a deploy-risk."""

    def test_api_base_targets_bambulab_backend(self):
        # ``api.bambulab.com`` is not Cloudflare-fronted; ``makerworld.com`` is
        # and returns empty JSON to plain httpx. Regressing this constant
        # silently breaks the whole integration.
        assert MAKERWORLD_API_BASE == "https://api.bambulab.com/v1/design-service"


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

    @pytest.mark.asyncio
    async def test_hits_bambulab_api_base(self, service):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": 1}
        service._client.get.return_value = resp

        await service.get_design(1)
        call = service._client.get.call_args
        # First positional arg is the URL — must be on the api.bambulab.com
        # backend, not the Cloudflare-fronted makerworld.com host.
        url = call.args[0] if call.args else call.kwargs.get("url")
        assert url == "https://api.bambulab.com/v1/design-service/design/1"

    @pytest.mark.asyncio
    async def test_sends_browser_like_headers(self, service):
        """Post-refactor the client uses a minimal Firefox-ish header set.
        The old ``x-bbl-*`` Bambu-app identification headers are gone —
        ``api.bambulab.com`` accepts browser-like headers cleanly."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": 1}
        service._client.get.return_value = resp

        await service.get_design(1)
        headers = service._client.get.call_args.kwargs["headers"]
        assert "Firefox" in headers["User-Agent"]
        assert headers["Accept-Language"].startswith("en-US")
        assert headers["Referer"] == "https://makerworld.com/"
        assert "Accept" in headers
        # The deprecated Bambu-identification headers must no longer be sent.
        for dead_header in (
            "x-bbl-client-type",
            "x-bbl-client-version",
            "x-bbl-app-source",
            "x-bbl-client-name",
        ):
            assert dead_header not in headers

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


class TestGetProfileDownload:
    """The new auth-gated 3MF manifest endpoint on the Bambu iot-service.

    Replaces the removed ``get_instance_download`` / ``get_model_download``
    helpers — YASTL#51's endpoint mints the signed CDN URL from the same
    long-lived Bambu Cloud bearer users already have.
    """

    def _make_service(self, *, auth_token: str | None = "tok-abc") -> MakerWorldService:
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient), auth_token=auth_token)
        svc._client.get = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_requires_auth_token(self):
        svc = self._make_service(auth_token=None)
        with pytest.raises(MakerWorldAuthError):
            await svc.get_profile_download(1452154, "US2bb73b106683e5")

    @pytest.mark.asyncio
    async def test_returns_signed_manifest(self):
        svc = self._make_service()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "name": "benchy.3mf",
            "url": "https://makerworld.bblmw.com/makerworld/model/X/Y/f.3mf?exp=1&key=k",
        }
        svc._client.get.return_value = resp

        manifest = await svc.get_profile_download(1452154, "US2bb73b106683e5")
        assert manifest["url"].startswith("https://makerworld.bblmw.com/")
        assert manifest["name"] == "benchy.3mf"

    @pytest.mark.asyncio
    async def test_sends_bearer_and_model_id_query(self):
        """Auth goes in ``Authorization`` and the alphanumeric modelId as a
        ``model_id`` query param — this is what YASTL#51 reverse-engineered."""
        svc = self._make_service(auth_token="tok-abc")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"url": "https://makerworld.bblmw.com/x.3mf"}
        svc._client.get.return_value = resp

        await svc.get_profile_download(1452154, "US2bb73b106683e5")
        call = svc._client.get.call_args
        url = call.args[0] if call.args else call.kwargs.get("url")
        assert url == "https://api.bambulab.com/v1/iot-service/api/user/profile/1452154"
        assert call.kwargs["headers"]["Authorization"] == "Bearer tok-abc"
        assert call.kwargs["params"] == {"model_id": "US2bb73b106683e5"}

    @pytest.mark.asyncio
    async def test_maps_401_to_auth_error(self):
        svc = self._make_service()
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"error": "token expired"}
        svc._client.get.return_value = resp

        with pytest.raises(MakerWorldAuthError):
            await svc.get_profile_download(1, "M1")

    @pytest.mark.asyncio
    async def test_maps_403_to_forbidden(self):
        svc = self._make_service()
        resp = MagicMock()
        resp.status_code = 403
        resp.json.return_value = {"error": "paid model"}
        svc._client.get.return_value = resp

        with pytest.raises(MakerWorldForbiddenError) as exc_info:
            await svc.get_profile_download(1, "M1")
        assert "paid model" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_maps_404_to_not_found(self):
        svc = self._make_service()
        resp = MagicMock()
        resp.status_code = 404
        svc._client.get.return_value = resp

        with pytest.raises(MakerWorldNotFoundError):
            await svc.get_profile_download(1, "M1")

    @pytest.mark.asyncio
    async def test_maps_timeout_to_unavailable(self):
        svc = self._make_service()
        svc._client.get.side_effect = httpx.TimeoutException("nope")

        with pytest.raises(MakerWorldUnavailableError):
            await svc.get_profile_download(1, "M1")

    @pytest.mark.asyncio
    async def test_rejects_non_dict_json(self):
        svc = self._make_service()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = ["not", "a", "dict"]
        svc._client.get.return_value = resp

        with pytest.raises(MakerWorldUnavailableError):
            await svc.get_profile_download(1, "M1")


class TestDownload3MF:
    """SSRF guard + size cap + streaming behaviour."""

    def _stream_ctx(self, resp):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/steal.3mf",
            "https://169.254.169.254/meta",  # EC2 metadata
            "http://internal.host/loot",
            "http://127.0.0.1/loot",
        ],
    )
    async def test_rejects_non_allowed_hosts(self, url):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(MakerWorldUrlError):
            await svc.download_3mf(url)

    @pytest.mark.asyncio
    async def test_s3_host_delegates_to_urllib_path(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        with patch(
            "backend.app.services.makerworld._download_s3_urllib",
            new=AsyncMock(return_value=(b"payload", "file.3mf")),
        ) as mocked:
            payload, filename = await svc.download_3mf(
                "https://s3.us-west-2.amazonaws.com/bucket/key/file.3mf?X-Amz-Signature=abc"
            )
        mocked.assert_awaited_once()
        # First arg is the verbatim URL — must NOT be round-tripped through
        # httpx/urlparse.urlencode since that breaks S3 SigV4.
        args = mocked.await_args.args
        assert args[0] == ("https://s3.us-west-2.amazonaws.com/bucket/key/file.3mf?X-Amz-Signature=abc")
        assert payload == b"payload"
        assert filename == "file.3mf"

    @pytest.mark.asyncio
    async def test_cdn_url_uses_httpx_with_minimal_headers(self):
        """Signed CDN URLs already carry the auth in the query string — don't
        leak the Bambu Cloud bearer to the CDN too. The client is reduced to a
        single ``User-Agent`` header; no ``Authorization``, no ``x-bbl-*``."""
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient), auth_token="tok-abc")

        resp = MagicMock()
        resp.status_code = 200

        async def _chunks():
            yield b"PK\x03\x04"

        resp.aiter_bytes = lambda: _chunks()
        svc._client.stream = MagicMock(return_value=self._stream_ctx(resp))

        await svc.download_3mf("https://makerworld.bblmw.com/makerworld/model/X/Y/foo.3mf?exp=1&key=k")

        call = svc._client.stream.call_args
        headers = call.kwargs["headers"]
        # Minimal: UA only. No bearer to the CDN.
        assert "Authorization" not in headers
        assert all(not k.startswith("x-bbl") for k in headers)
        assert "User-Agent" in headers
        # Redirects off — host allowlist is only meaningful on the initial URL.
        assert call.kwargs["follow_redirects"] is False

    @pytest.mark.asyncio
    async def test_happy_path_streams_bytes(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))

        resp = MagicMock()
        resp.status_code = 200

        async def _chunks():
            yield b"PK\x03\x04"  # 3MF = zip magic
            yield b"rest of file"

        resp.aiter_bytes = lambda: _chunks()
        svc._client.stream = MagicMock(return_value=self._stream_ctx(resp))

        payload, filename = await svc.download_3mf(
            "https://makerworld.bblmw.com/makerworld/model/X/Y/foo.3mf?exp=1&key=k"
        )
        assert payload.startswith(b"PK\x03\x04")
        assert filename == "foo.3mf"

    @pytest.mark.asyncio
    async def test_http_error_on_cdn_path_raises_unavailable(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        resp = MagicMock()
        resp.status_code = 500
        resp.aiter_bytes = lambda: (_ for _ in ())
        svc._client.stream = MagicMock(return_value=self._stream_ctx(resp))

        with pytest.raises(MakerWorldUnavailableError):
            await svc.download_3mf("https://makerworld.bblmw.com/makerworld/model/X/Y/foo.3mf?exp=1&key=k")

    @pytest.mark.asyncio
    async def test_exceeds_size_cap_raises(self):
        svc = MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))
        resp = MagicMock()
        resp.status_code = 200

        # Cap is 200 MB — emit one "chunk" that reports exceeding it.
        oversized = _MAX_3MF_BYTES + 1

        async def _chunks():
            # Emit a bytes object whose ``len()`` is oversized, without
            # actually allocating 200 MB in the test process.
            yield b"\x00" * oversized

        resp.aiter_bytes = lambda: _chunks()
        svc._client.stream = MagicMock(return_value=self._stream_ctx(resp))

        with pytest.raises(MakerWorldUnavailableError, match="cap"):
            await svc.download_3mf("https://makerworld.bblmw.com/makerworld/model/X/Y/foo.3mf?exp=1&key=k")


class TestS3UrllibDownload:
    """Module-level ``_download_s3_urllib`` — the verbatim-URL path for S3."""

    @pytest.mark.asyncio
    async def test_returns_bytes_and_filename(self):
        from backend.app.services.makerworld import _download_s3_urllib

        fake_resp = MagicMock()
        fake_resp.status = 200
        # Simulate urllib's file-like ``read(n)`` interface.
        fake_resp.read = MagicMock(side_effect=[b"hello", b""])
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=None)

        fake_opener = MagicMock()
        fake_opener.open = MagicMock(return_value=fake_resp)

        with patch("urllib.request.build_opener", return_value=fake_opener):
            data, filename = await _download_s3_urllib(
                "https://s3.us-west-2.amazonaws.com/b/k/file.3mf?sig=abc",
                "fallback.3mf",
            )
        assert data == b"hello"
        assert filename == "fallback.3mf"

    @pytest.mark.asyncio
    async def test_redirect_is_treated_as_error(self):
        """The ``_NoRedirect`` handler returns ``None`` from ``redirect_request``,
        which makes ``urllib`` raise ``HTTPError`` instead of following. The
        wrapper must surface that as ``MakerWorldUnavailableError``."""
        from backend.app.services.makerworld import _download_s3_urllib

        fake_opener = MagicMock()
        fake_opener.open = MagicMock(
            side_effect=HTTPError(
                "https://s3.example/redirect",
                302,
                "Found",
                {},  # type: ignore[arg-type]
                None,
            )
        )

        with (
            patch("urllib.request.build_opener", return_value=fake_opener),
            pytest.raises(MakerWorldUnavailableError),
        ):
            await _download_s3_urllib(
                "https://s3.us-west-2.amazonaws.com/b/k/file.3mf?sig=abc",
                "fallback.3mf",
            )

    @pytest.mark.asyncio
    async def test_non_200_raises_unavailable(self):
        from backend.app.services.makerworld import _download_s3_urllib

        fake_resp = MagicMock()
        fake_resp.status = 403
        fake_resp.read = MagicMock(return_value=b"")
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=None)

        fake_opener = MagicMock()
        fake_opener.open = MagicMock(return_value=fake_resp)

        with (
            patch("urllib.request.build_opener", return_value=fake_opener),
            pytest.raises(MakerWorldUnavailableError),
        ):
            await _download_s3_urllib(
                "https://s3.us-west-2.amazonaws.com/b/k/file.3mf?sig=abc",
                "fallback.3mf",
            )

    @pytest.mark.asyncio
    async def test_size_cap_enforced(self):
        from backend.app.services.makerworld import _download_s3_urllib

        fake_resp = MagicMock()
        fake_resp.status = 200
        # A single oversized chunk trips the cap on the first iteration.
        fake_resp.read = MagicMock(side_effect=[b"\x00" * (_MAX_3MF_BYTES + 1), b""])
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=None)

        fake_opener = MagicMock()
        fake_opener.open = MagicMock(return_value=fake_resp)

        with (
            patch("urllib.request.build_opener", return_value=fake_opener),
            pytest.raises(MakerWorldUnavailableError, match="cap"),
        ):
            await _download_s3_urllib(
                "https://s3.us-west-2.amazonaws.com/b/k/file.3mf?sig=abc",
                "fallback.3mf",
            )

    @pytest.mark.asyncio
    async def test_network_error_mapped_to_unavailable(self):
        from backend.app.services.makerworld import _download_s3_urllib

        fake_opener = MagicMock()
        fake_opener.open = MagicMock(side_effect=URLError("dns fail"))

        with (
            patch("urllib.request.build_opener", return_value=fake_opener),
            pytest.raises(MakerWorldUnavailableError),
        ):
            await _download_s3_urllib(
                "https://s3.us-west-2.amazonaws.com/b/k/file.3mf?sig=abc",
                "fallback.3mf",
            )


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
    async def test_does_not_follow_redirects(self, service):
        """Host allowlist is only enforced on the initial URL — a 302 from the
        CDN to any other host would otherwise bypass the allowlist. ``follow_
        redirects=False`` pins that behaviour in the wire contract."""
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "image/jpeg"}
        resp.content = b"\xff\xd8\xff\xe0JFIF"
        service._client.get.return_value = resp

        await service.fetch_thumbnail("https://makerworld.bblmw.com/makerworld/model/X/cover.jpg")
        assert service._client.get.call_args.kwargs["follow_redirects"] is False

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
