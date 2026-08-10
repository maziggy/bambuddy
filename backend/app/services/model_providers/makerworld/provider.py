"""MakerWorld model provider.

Static descriptor + per-request service factory for makerworld.com. The
``MakerWorldProvider`` instance is what gets registered in the shared
:class:`ModelProviderRegistry`; the actual API work lives in ``service.py``
(the per-request :class:`ProviderService`) and ``url.py`` (URL parsing and
canonicalisation). Credential handling is centralised here so route layers
never touch MakerWorld specifics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from backend.app.core.permissions import Permission
from backend.app.services.model_providers.base import (
    ModelProvider,
    ProviderAuthConfig,
    ProviderAuthType,
    ProviderResourceRef,
    ProviderService,
)
from backend.app.services.model_providers.makerworld import url as mw_url
from backend.app.services.model_providers.makerworld.auth import (
    get_stored_token,
    mark_cloud_token_invalid,
)
from backend.app.services.model_providers.makerworld.service import MakerWorldService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.models.user import User


class MakerWorldProvider(ModelProvider):
    """MakerWorld descriptor: identity, URL routing, auth requirements, and the
    factory that builds a per-request :class:`MakerWorldService` seeded with the
    caller's stored Bambu Cloud bearer token.
    """

    source_type = "makerworld"
    display_name = "MakerWorld"
    host_patterns = ("makerworld.com",)
    can_download = True
    auth = ProviderAuthConfig(
        auth_type=ProviderAuthType.BAMBU_CLOUD_BEARER,
        display_label="Bambu Cloud sign-in",
        description=(
            "MakerWorld downloads reuse the Bambu Cloud account already stored in Bambuddy — "
            "there is no separate MakerWorld sign-in."
        ),
        setup_hint="Open the Profiles page and sign in to Bambu Cloud.",
    )
    default_folder_name = "MakerWorld"
    view_permission = Permission.MAKERWORLD_VIEW
    import_permission = Permission.MAKERWORLD_IMPORT

    async def build_service(
        self,
        *,
        db: AsyncSession,
        user: User | None,
        api_key_owner: User | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> ProviderService:
        """Build a per-request service seeded with the caller's stored Bambu
        Cloud bearer, mirroring ``cloud.build_authenticated_cloud``.

        ``api_key_owner`` is the API key's owning user for API-keyed calls
        (see ``resolve_api_key_cloud_owner``); MakerWorld uses it as the
        fallback identity when ``user`` is None. Like the cloud integration, a
        rejected token is recorded so the whole app agrees the sign-in is dead
        rather than each feature failing on its own.
        """
        identity = user if user is not None else api_key_owner
        token, _email, _region = await get_stored_token(db, identity)
        user_id = identity.id if identity is not None else None
        return MakerWorldService(
            client=client,
            auth_token=token,
            user=identity,
            on_auth_failure=None if user_id is None else lambda: mark_cloud_token_invalid(user_id),
        )

    def parse_url(self, url: str) -> ProviderResourceRef:
        return mw_url.parse_url(url)

    def canonical_url(self, ref: ProviderResourceRef) -> str:
        return mw_url.canonical_url(ref)

    def thumbnail_hosts(self) -> tuple[str, ...]:
        return mw_url.MAKERWORLD_CDN_HOSTS


makerworld_provider = MakerWorldProvider()
