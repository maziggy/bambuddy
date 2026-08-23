"""Tests for the MakerWorldService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError

import httpx
import pytest

from backend.app.services.model_providers.base import ProviderResourceRef
from backend.app.services.model_providers.makerworld.errors import (
    MakerWorldAuthError,
    MakerWorldForbiddenError,
    MakerWorldNotFoundError,
    MakerWorldUnavailableError,
    MakerWorldUrlError,
)
from backend.app.services.model_providers.makerworld.http import _MAX_3MF_BYTES, MAKERWORLD_API_BASE
from backend.app.services.model_providers.makerworld.service import MakerWorldService, set_shared_http_client
from backend.app.services.model_providers.makerworld.url import parse_url


class TestParseUrl:
    """MakerWorld URL extraction — tests parse_url directly."""

    def test_strips_locale_prefix_and_slug(self):
        ref = parse_url("https://makerworld.com/en/models/1400373-self-watering-seed-starter")
        assert ref.external_id == "1400373"
        assert ref.sub_id is None

    def test_extracts_profile_id_from_fragment(self):
        ref = parse_url("https://makerworld.com/en/models/1400373-slug#profileId-1452154")
        assert ref.external_id == "1400373"
        assert ref.sub_id == "1452154"

    def test_accepts_scheme_omitted(self):
        ref = parse_url("makerworld.com/models/999")
        assert ref.external_id == "999"
        assert ref.sub_id is None

    def test_accepts_subdomain(self):
        # Defensive: if MakerWorld ever stands up a regional subdomain, still accept it
        ref = parse_url("https://www.makerworld.com/en/models/42")
        assert ref.external_id == "42"
        assert ref.sub_id is None

    def test_rejects_non_makerworld_host(self):
        with pytest.raises(MakerWorldUrlError):
            parse_url("https://thingiverse.com/things/123")

    def test_rejects_malformed_url(self):
        # No /models/ segment anywhere in path
        with pytest.raises(MakerWorldUrlError):
            parse_url("https://makerworld.com/en/creators/foo")

    def test_rejects_empty(self):
        with pytest.raises(MakerWorldUrlError):
            parse_url("")


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
    async def test_sends_honest_bambuddy_user_agent(self, service):
        """The client identifies honestly as Bambuddy, not as Firefox.

        Earlier iterations of this code stripped ``x-bbl-*`` Bambu-app
        identification headers but kept a Firefox User-Agent. Verified
        2026-05-12 that MakerWorld treats ``Bambuddy/X.Y.Z`` identically to
        a Firefox UA at the Cloudflare edge — same response shape on
        ``/api/v1/design-service/*`` paths. Honest identification keeps us
        clearly outside Bambu Lab's "no falsified client identity" line
        from the 2026-05-12 cloud-access blog post.

        Referer is still sent because MakerWorld's CSRF / origin-check
        middleware uses it on some endpoints — that is functional, not
        client-impersonation.
        """
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": 1}
        service._client.get.return_value = resp

        await service.get_design(1)
        headers = service._client.get.call_args.kwargs["headers"]
        assert headers["User-Agent"].startswith("Bambuddy/")
        # Browser-impersonation strings must not creep back in
        assert "Mozilla" not in headers["User-Agent"]
        assert "Firefox" not in headers["User-Agent"]
        assert "Chrome" not in headers["User-Agent"]
        # Functional headers stay
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
    async def test_maps_401_without_token_to_auth_error(self, service):
        """No token was sent, so a 401 means "sign-in required" — not "your
        sign-in expired", and nothing gets marked dead (there is nothing to
        mark). The fixture's service carries no auth token."""
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"code": 1, "error": "Please log in"}
        service._client.get.return_value = resp

        with pytest.raises(MakerWorldAuthError) as exc_info:
            await service.get_design(1)
        assert "Bambu Cloud" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_401_with_token_reports_expired_and_hides_upstream_text(self):
        """Bambu answers a dead token with ``{"error": "Please login."}``. We used
        to forward that verbatim, which produced a "Please login." toast on a UI
        that simultaneously claimed the user was connected, and pointed at a
        Settings page that does not exist. Say what happened, name a real page,
        and record the credential as dead."""
        marked: list[bool] = []

        async def _on_auth_failure() -> None:
            marked.append(True)

        svc = MakerWorldService(
            client=MagicMock(spec=httpx.AsyncClient),
            auth_token="tok-abc",
            on_auth_failure=_on_auth_failure,
        )
        svc._client.get = AsyncMock()
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"code": 4, "error": "Please login.", "message": ""}
        svc._client.get.return_value = resp

        with pytest.raises(MakerWorldAuthError) as exc_info:
            await svc.get_design(1)

        message = str(exc_info.value)
        assert "Please login." not in message
        assert "expired" in message.lower()
        assert "Profiles" in message
        assert marked == [True], "a rejected token must be recorded as dead"

    @pytest.mark.asyncio
    async def test_transient_401_with_token_does_not_invalidate(self):
        """A 401 WITHOUT Bambu's expiry signature (endpoint/edge noise) must fail
        the request but NOT durably sign the user out — otherwise one stray 401
        from any single MakerWorld call kills the whole cloud integration."""
        marked: list[bool] = []

        async def _on_auth_failure() -> None:
            marked.append(True)

        svc = MakerWorldService(
            client=MagicMock(spec=httpx.AsyncClient),
            auth_token="tok-abc",
            on_auth_failure=_on_auth_failure,
        )
        svc._client.get = AsyncMock()
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"code": 1, "error": "forbidden"}
        svc._client.get.return_value = resp

        with pytest.raises(MakerWorldAuthError):
            await svc.get_design(1)

        assert marked == [], "a benign 401 must not record the credential as dead"

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


class TestResolve:
    """``resolve`` — the interface-level "URL → metadata + plate list" flow the
    /makerworld/resolve route drives. The per-instance printer-compatibility
    merge lives here (not in the route) so every future provider gets it from
    its own ``resolve`` implementation."""

    @pytest.fixture
    def service(self):
        return MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))

    @pytest.mark.asyncio
    async def test_merges_compatibility_from_design_into_instances(self, service):
        """Per-instance printer compatibility info lives on
        ``design.instances[].extention.modelInfo`` but not on
        ``/instances/hits``. Resolve enriches each hit with both
        ``compatibility`` (primary printer the instance was sliced for) and
        ``otherCompatibility`` (extra printers the uploader marked it
        compatible with) so the frontend can show "sliced for A1 / also
        marked compatible with: H2D, P1S".
        """
        design_payload = {
            "id": 1400373,
            "title": "Seed Starter",
            "instances": [
                {
                    "id": 1452154,
                    "extention": {
                        "modelInfo": {
                            "compatibility": ["A1"],
                            "otherCompatibility": ["H2D", "P1S"],
                        }
                    },
                },
                {
                    "id": 1452158,
                    "extention": {
                        "modelInfo": {
                            "compatibility": ["X1 Carbon"],
                            "otherCompatibility": [],
                        }
                    },
                },
            ],
        }
        instances_payload = {
            "total": 2,
            "hits": [
                {"id": 1452154, "profileId": 298919107, "title": "9 cells"},
                {"id": 1452158, "profileId": 298919564, "title": "12 cells"},
            ],
        }
        service.get_design = AsyncMock(return_value=design_payload)
        service.get_design_instances = AsyncMock(return_value=instances_payload)

        resolved = await service.resolve(ProviderResourceRef(source_type="makerworld", external_id="1400373"))
        by_id = {i["id"]: i for i in resolved.instances}
        assert by_id[1452154]["compatibility"] == ["A1"]
        assert by_id[1452154]["otherCompatibility"] == ["H2D", "P1S"]
        assert by_id[1452158]["compatibility"] == ["X1 Carbon"]
        assert by_id[1452158]["otherCompatibility"] == []
        assert resolved.design == design_payload

    @pytest.mark.asyncio
    async def test_handles_missing_compatibility_gracefully(self, service):
        """Older designs (or hits without a matching design.instances entry)
        must not crash resolve — they just don't get the compat fields."""
        design_payload = {"id": 1400373, "instances": [{"id": 1452154}]}  # no extention
        instances_payload = {
            "total": 2,
            "hits": [
                {"id": 1452154, "profileId": 298919107},
                {"id": 9999999, "profileId": 298919999},  # no design.instances match
            ],
        }
        service.get_design = AsyncMock(return_value=design_payload)
        service.get_design_instances = AsyncMock(return_value=instances_payload)

        resolved = await service.resolve(ProviderResourceRef(source_type="makerworld", external_id="1400373"))
        # First instance: design entry exists but no extention → fields absent or None.
        first = next(i for i in resolved.instances if i["id"] == 1452154)
        assert first.get("compatibility") is None
        assert first.get("otherCompatibility") is None
        # Second instance: no design entry at all → no enrichment, no crash.
        second = next(i for i in resolved.instances if i["id"] == 9999999)
        assert "compatibility" not in second or second["compatibility"] is None

    @pytest.mark.asyncio
    async def test_normalises_null_and_non_list_hits_to_empty(self, service):
        service.get_design = AsyncMock(return_value={"id": 1400373})
        service.get_design_instances = AsyncMock(return_value={"total": 0, "hits": None})

        resolved = await service.resolve(ProviderResourceRef(source_type="makerworld", external_id="1400373"))
        assert resolved.instances == []


class TestGetDownload:
    """``get_download`` — the interface-level "resource → signed 3MF URL"
    flow the /makerworld/import route drives. The provider-specific dance
    lives here: the iot-service endpoint needs the *alphanumeric* modelId
    (not the integer design id), the profile falls back in two tiers, and
    three malformed-upstream shapes must map to UnavailableError (502)."""

    @pytest.fixture
    def service(self):
        return MakerWorldService(client=MagicMock(spec=httpx.AsyncClient))

    def _design(self, **overrides):
        design = {
            "id": 1400373,
            "modelId": "US2bb73b106683e5",
            "instances": [{"profileId": 298919107, "title": "9 cells"}],
        }
        design.update(overrides)
        return design

    def _manifest(self, url="https://makerworld.bblmw.com/x.3mf?exp=1", name="benchy.3mf"):
        return {"url": url, "name": name}

    async def _run(self, service, ref):
        return await service.get_download(ref)

    @pytest.mark.asyncio
    async def test_resolves_alphanumeric_model_id_and_explicit_profile(self, service):
        """Explicit profile_id flows through; get_profile_download receives
        the alphanumeric modelId from the design, not the integer id."""
        service.get_design = AsyncMock(return_value=self._design())
        manifest = self._manifest()
        service.get_profile_download = AsyncMock(return_value=manifest)

        info = await self._run(
            service, ProviderResourceRef(source_type="makerworld", external_id="1400373", sub_id="298919107")
        )

        service.get_profile_download.assert_awaited_once_with(298919107, "US2bb73b106683e5")
        assert info.url == manifest["url"]
        assert info.suggested_filename == "benchy.3mf"
        # The enriched ref carries the resolved profile for the dedupe key.
        assert info.ref.sub_id == "298919107"

    @pytest.mark.asyncio
    async def test_falls_back_to_first_design_instance_profile(self, service):
        """No profile given → first ``design.instances[].profileId`` wins."""
        service.get_design = AsyncMock(return_value=self._design())
        service.get_profile_download = AsyncMock(return_value=self._manifest())

        info = await self._run(service, ProviderResourceRef(source_type="makerworld", external_id="1400373"))

        service.get_profile_download.assert_awaited_once_with(298919107, "US2bb73b106683e5")
        assert info.ref.sub_id == "298919107"

    @pytest.mark.asyncio
    async def test_second_tier_falls_back_to_instances_envelope(self, service):
        """Design carries no usable profileId → the ``/design/{id}/instances``
        envelope is consulted before giving up."""
        service.get_design = AsyncMock(return_value=self._design(instances=[{"title": "no profileId here"}]))
        service.get_design_instances = AsyncMock(return_value={"total": 1, "hits": [{"profileId": 298919564}]})
        service.get_profile_download = AsyncMock(return_value=self._manifest())

        info = await self._run(service, ProviderResourceRef(source_type="makerworld", external_id="1400373"))

        service.get_design_instances.assert_awaited_once_with(1400373)
        service.get_profile_download.assert_awaited_once_with(298919564, "US2bb73b106683e5")
        assert info.ref.sub_id == "298919564"

    @pytest.mark.asyncio
    async def test_missing_alphanumeric_model_id_is_unavailable(self, service):
        """A design without the ``modelId`` field can't reach iot-service."""
        service.get_design = AsyncMock(return_value={"id": 1400373})

        with pytest.raises(MakerWorldUnavailableError, match="modelId"):
            await self._run(service, ProviderResourceRef(source_type="makerworld", external_id="1400373"))

    @pytest.mark.asyncio
    async def test_no_profiles_anywhere_is_unavailable(self, service):
        service.get_design = AsyncMock(return_value=self._design(instances=[]))
        service.get_design_instances = AsyncMock(return_value={"total": 0, "hits": []})

        with pytest.raises(MakerWorldUnavailableError, match="no instances"):
            await self._run(service, ProviderResourceRef(source_type="makerworld", external_id="1400373"))

    @pytest.mark.asyncio
    async def test_manifest_without_url_is_unavailable(self, service):
        service.get_design = AsyncMock(return_value=self._design())
        service.get_profile_download = AsyncMock(return_value={"name": "benchy.3mf"})

        with pytest.raises(MakerWorldUnavailableError, match="download URL"):
            await self._run(service, ProviderResourceRef(source_type="makerworld", external_id="1400373"))


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
            "backend.app.services.model_providers.makerworld.service._download_s3_urllib",
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
        from backend.app.services.model_providers.makerworld.http import _download_s3_urllib

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
        from backend.app.services.model_providers.makerworld.http import _download_s3_urllib

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
        from backend.app.services.model_providers.makerworld.http import _download_s3_urllib

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
        from backend.app.services.model_providers.makerworld.http import _download_s3_urllib

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
        from backend.app.services.model_providers.makerworld.http import _download_s3_urllib

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


class TestSharedHttpClient:
    """The app-scoped httpx client registered via ``set_shared_http_client``
    must be reused by per-request services (one shared connection pool, same
    pattern as ``bambu_cloud``). The setter has to live in the same module as
    the service class, or the import-time snapshot never sees the lifespan's
    late registration and every request spins up its own client."""

    @pytest.mark.asyncio
    async def test_reuses_registered_client(self):
        client = MagicMock(spec=httpx.AsyncClient)
        set_shared_http_client(client)
        try:
            svc = MakerWorldService()
            assert svc._client is client
            assert svc._owns_client is False
            # close() must NOT close a client it doesn't own
            await svc.close()
            client.aclose.assert_not_called()
        finally:
            set_shared_http_client(None)

    @pytest.mark.asyncio
    async def test_creates_and_owns_own_client_when_none_registered(self):
        set_shared_http_client(None)
        svc = MakerWorldService()
        assert svc._owns_client is True
        await svc.close()
        assert svc._client.is_closed
