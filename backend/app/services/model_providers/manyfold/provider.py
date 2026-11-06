"""Manyfold model provider (#1471).

Manyfold is a self-hosted library, so the provider has no fixed host and
claims no pasted URLs: models are browsed and searched on the Manyfold tab of
the Model Sources page, not pasted. The descriptor still carries what every
provider declares (identity, permissions, import folder) and builds the
per-request service from the stored connection.
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
from backend.app.services.model_providers.manyfold.config import load_config
from backend.app.services.model_providers.manyfold.service import ManyfoldService, ManyfoldUnavailableError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.models.user import User


class ManyfoldProvider(ModelProvider):
    source_type = "manyfold"
    display_name = "Manyfold"
    host_patterns = ()
    auth = ProviderAuthConfig(
        auth_type=ProviderAuthType.ACCESS_TOKEN,
        display_label="Manyfold OAuth application",
        description="Bambuddy signs in with the client ID and secret of an OAuth application created in Manyfold.",
        credential_fields=("url", "client_id", "client_secret"),
        setup_hint="In Manyfold, open Settings -> API and create an application with the 'public read' scopes.",
    )
    default_folder_name = "Manyfold"
    view_permission = Permission.MANYFOLD_VIEW
    import_permission = Permission.MANYFOLD_IMPORT

    async def build_service(
        self,
        *,
        db: AsyncSession,
        user: User | None,
        api_key_owner: User | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> ProviderService:
        # One connection for the whole install: the OAuth application's owner
        # in Manyfold decides what Bambuddy can see, not the Bambuddy user.
        return ManyfoldService(await load_config(db), client=client)

    def parse_url(self, url: str) -> ProviderResourceRef:
        raise ManyfoldUnavailableError("Manyfold models are browsed, not pasted")

    def canonical_url(self, ref: ProviderResourceRef) -> str:
        """``manyfold:<model>/<file>`` names one file of one model.

        Deliberately not the install's URL: moving Manyfold to another address
        must not make every earlier import look new.
        """
        if ref.sub_id:
            return f"manyfold:{ref.external_id}/{ref.sub_id}"
        return f"manyfold:{ref.external_id}"

    def source_url_filter(self, column, external_id: str):
        # Ids may contain "_", which LIKE would read as "any character".
        escaped = external_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return column.like(f"manyfold:{escaped}/%", escape="\\")


manyfold_provider = ManyfoldProvider()
