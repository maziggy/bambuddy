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

    def test_parse_and_canonical_roundtrip(self):
        ref = makerworld_provider.parse_url("https://makerworld.com/en/models/1400373-slug#profileId-1452154")
        assert ref.external_id == "1400373"
        assert ref.sub_id == "1452154"
        assert ref.source_type == "makerworld"
        assert makerworld_provider.canonical_url(ref) == "https://makerworld.com/models/1400373#profileId-1452154"

    def test_canonical_plate_less_shape(self):
        ref = makerworld_provider.parse_url("https://makerworld.com/models/999")
        assert makerworld_provider.canonical_url(ref) == "https://makerworld.com/models/999"


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
    async def test_anonymous_user_gets_no_auth_callback(self):
        """No user, no token → nothing to invalidate; the callback must be
        absent so ``_note_auth_failure`` stays a no-op."""
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
        assert svc._on_auth_failure is None
        mark_invalid.assert_not_awaited()
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
