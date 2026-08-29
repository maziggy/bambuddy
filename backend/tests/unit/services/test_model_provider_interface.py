"""Tests for the MakerWorld provider descriptor (``provider.py``).

The descriptor is the interface between the route layer and the per-request
service: identity, URL routing, auth requirements, and the factory that seeds
a service with the caller's stored Bambu Cloud bearer token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.core.permissions import Permission
from backend.app.services.model_providers import makerworld_provider
from backend.app.services.model_providers.base import ModelProvider
from backend.app.services.model_providers.makerworld.service import MakerWorldService


class TestMakerWorldProviderDescriptor:
    def test_identity_fields(self):
        assert makerworld_provider.source_type == "makerworld"
        assert makerworld_provider.display_name == "MakerWorld"
        assert makerworld_provider.host_patterns == ("makerworld.com",)
        assert makerworld_provider.can_download is True
        assert makerworld_provider.default_folder_name == "MakerWorld"

    def test_permissions(self):
        assert makerworld_provider.view_permission == Permission.MAKERWORLD_VIEW
        assert makerworld_provider.import_permission == Permission.MAKERWORLD_IMPORT

    def test_auth_descriptor(self):
        assert makerworld_provider.auth is not None
        assert makerworld_provider.auth.auth_type == "bambu_cloud_bearer"
        assert makerworld_provider.auth.credential_fields == ()

    def test_supports_url_host_suffix_match(self):
        assert makerworld_provider.supports_url("https://makerworld.com/en/models/1400373")
        assert makerworld_provider.supports_url("https://www.makerworld.com/models/1400373")
        assert makerworld_provider.supports_url("makerworld.com/models/1")

    def test_rejects_foreign_hosts_and_garbage(self):
        assert not makerworld_provider.supports_url("https://thingiverse.com/thing/123")
        assert not makerworld_provider.supports_url("https://makerworld.com.evil.example/x")
        assert not makerworld_provider.supports_url("")
        assert not makerworld_provider.supports_url(None)  # type: ignore[arg-type]
        assert not makerworld_provider.supports_url(123)  # type: ignore[arg-type]

    def test_thumbnail_hosts_is_the_cdn_allowlist(self):
        assert "makerworld.bblmw.com" in makerworld_provider.thumbnail_hosts()
        assert "public-cdn.bblmw.com" in makerworld_provider.thumbnail_hosts()

    def test_download_hosts_is_the_cdn_allowlist(self):
        """The download-guard SSRF seam mirrors the thumbnail one — a provider
        that fetches files server-side declares the hosts its service may
        fetch from (review round 3, note 2)."""
        assert "makerworld.bblmw.com" in makerworld_provider.download_hosts()
        assert "public-cdn.bblmw.com" in makerworld_provider.download_hosts()

    def test_parse_and_canonical_roundtrip(self):
        ref = makerworld_provider.parse_url("https://makerworld.com/en/models/1400373-slug#profileId-1452154")
        assert ref.external_id == "1400373"
        assert ref.sub_id == "1452154"
        assert ref.source_type == "makerworld"
        assert makerworld_provider.canonical_url(ref) == "https://makerworld.com/models/1400373#profileId-1452154"

    def test_canonical_plate_less_shape(self):
        ref = makerworld_provider.parse_url("https://makerworld.com/models/999")
        assert makerworld_provider.canonical_url(ref) == "https://makerworld.com/models/999"

    def test_source_url_filter_matches_model_and_any_plate(self):
        """The already-imported predicate must cover the whole-model key and
        every per-plate key — MakerWorld's canonical shape appends
        ``#profileId-{n}`` (review round 3: shape knowledge belongs to the
        provider, not the route)."""
        from sqlalchemy import String, column as sa_column

        expr = makerworld_provider.source_url_filter(sa_column("source_url", String), "1400373")
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "source_url = 'https://makerworld.com/models/1400373'" in sql
        assert "LIKE 'https://makerworld.com/models/1400373#profileId-%'" in sql

    def test_default_source_url_filter_is_exact_match_only(self):
        """A provider without plate-shaped keys inherits the default: exact
        match on its whole-model canonical URL, nothing else."""
        from sqlalchemy import String, column as sa_column

        expr = _WholeModelProvider().source_url_filter(sa_column("source_url", String), "42")
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert sql == "source_url = 'https://example.com/models/42'"
        assert "LIKE" not in sql


class _WholeModelProvider(ModelProvider):
    """Minimal concrete provider that keys dedupe at whole-model granularity
    only — exercises the inherited default ``source_url_filter``."""

    source_type = "wholemodel"
    display_name = "WholeModel"

    async def build_service(self, *, db, user, api_key_owner=None, client=None):
        raise NotImplementedError

    def parse_url(self, url):
        raise NotImplementedError

    def canonical_url(self, ref):
        return f"https://example.com/models/{ref.external_id}"


class TestBuildService:
    """build_service must reproduce exactly what the old route helper did:
    read the caller's stored Bambu Cloud token and wire the rejected-token
    callback so a 401 invalidates the shared credential app-wide."""

    @pytest.mark.asyncio
    async def test_seeds_token_and_auth_failure_callback(self):
        db = AsyncMock()
        user = AsyncMock()
        user.id = 7

        with (
            patch(
                "backend.app.services.model_providers.makerworld.provider.get_stored_token",
                AsyncMock(return_value=("tok-abc", "e@x.com", "global")),
            ),
            patch(
                "backend.app.services.model_providers.makerworld.provider.mark_cloud_token_invalid",
                AsyncMock(),
            ) as mark_invalid,
        ):
            svc = await makerworld_provider.build_service(db=db, user=user)
            assert isinstance(svc, MakerWorldService)
            assert svc._auth_token == "tok-abc"
            assert svc._user is user
            await svc._on_auth_failure()
            mark_invalid.assert_awaited_once_with(7)
            await svc.close()

    @pytest.mark.asyncio
    async def test_anonymous_user_still_gets_auth_callback(self):
        """No user ≠ nothing to invalidate. Auth-disabled single-user installs
        hold their token in global Settings (``get_stored_token(db, None)``
        reads it), so a rejection must still be recorded — ``user_id=None``
        writes the global flag (review blocker 5). The callback stays wired;
        only a *stray* non-expiry 401 keeps it a no-op."""
        db = AsyncMock()
        with (
            patch(
                "backend.app.services.model_providers.makerworld.provider.get_stored_token",
                AsyncMock(return_value=(None, None, "global")),
            ),
            patch(
                "backend.app.services.model_providers.makerworld.provider.mark_cloud_token_invalid",
                AsyncMock(),
            ) as mark_invalid,
        ):
            svc = await makerworld_provider.build_service(db=db, user=None)

            assert svc._auth_token is None
            assert svc._on_auth_failure is not None
            # Firing it records the *global* flag (user_id=None), not a per-user row.
            await svc._on_auth_failure()
            mark_invalid.assert_awaited_once_with(None)
        await svc.close()

    @pytest.mark.asyncio
    async def test_build_service_passes_declared_thumbnail_hosts(self):
        """The SSRF seam contract: ``fetch_thumbnail``'s allowlist must come
        from ``ModelProvider.thumbnail_hosts()`` via build_service — not from
        a hardcoded copy inside the service (review round 2, item 1)."""
        db = AsyncMock()
        with patch(
            "backend.app.services.model_providers.makerworld.provider.get_stored_token",
            AsyncMock(return_value=(None, None, "global")),
        ):
            svc = await makerworld_provider.build_service(db=db, user=None)

        assert svc._thumbnail_hosts == makerworld_provider.thumbnail_hosts()
        assert len(svc._thumbnail_hosts) > 0
        await svc.close()

    @pytest.mark.asyncio
    async def test_build_service_passes_declared_download_hosts(self):
        """Symmetric SSRF seam contract: ``download``'s allowlist must come
        from ``ModelProvider.download_hosts()`` via build_service — not from
        a hardcoded copy inside the service (review round 3, note 2)."""
        db = AsyncMock()
        with patch(
            "backend.app.services.model_providers.makerworld.provider.get_stored_token",
            AsyncMock(return_value=(None, None, "global")),
        ):
            svc = await makerworld_provider.build_service(db=db, user=None)

        assert svc._download_hosts == makerworld_provider.download_hosts()
        assert len(svc._download_hosts) > 0
        await svc.close()

    @pytest.mark.asyncio
    async def test_api_key_owner_is_the_fallback_identity(self):
        """API-keyed callers carry identity on the key (#1777) — build_service
        must use the key's owner when ``user`` is None."""
        db = AsyncMock()
        owner = AsyncMock()
        owner.id = 11

        with (
            patch(
                "backend.app.services.model_providers.makerworld.provider.get_stored_token",
                AsyncMock(return_value=("owner-tok", "owner@x.com", "global")),
            ),
            patch(
                "backend.app.services.model_providers.makerworld.provider.mark_cloud_token_invalid",
                AsyncMock(),
            ) as mark_invalid,
        ):
            svc = await makerworld_provider.build_service(db=db, user=None, api_key_owner=owner)
            assert svc._auth_token == "owner-tok"
            assert svc._user is owner
            await svc._on_auth_failure()
            mark_invalid.assert_awaited_once_with(11)
            await svc.close()
