"""Model-provider interface.

A *model provider* is a website that hosts 3D printer models (MakerWorld,
Thingiverse, Printables, ...) whose files Bambuddy can resolve and import
into the library. This module defines the contract every provider must
fulfil — the split being:

  * :class:`ModelProvider` — the static, provider-wide descriptor: identity
    (``source_type``, ``display_name``), URL routing (``host_patterns``),
    the auth it needs (or explicitly doesn't), and a factory that builds a
    per-request :class:`ProviderService` seeded with the caller's stored
    credentials.
  * :class:`ProviderService` — one HTTP client per request, mirroring the
    ``BambuCloudService`` construction pattern: resolve a model URL to
    metadata + importable files, resolve + fetch a concrete download, and
    proxy thumbnail images. Providers are *thin transports*: shared concerns
    (library dedupe, folder auto-creation, ``save_3mf_bytes_to_library``)
    stay in the route layer so every provider benefits from them.

The interface deliberately covers everything the MakerWorld integration
needs today (see ``model_providers/makerworld/``) so that adding a new site
is: implement ``ModelProvider`` + ``ProviderService``, register it, and the
shared import API routes pasted URLs to it via ``registry.find_for_url``.

Only interoperability — not affiliated with or endorsed by MakerWorld or any
other provider, and not intended to circumvent any access control.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from backend.app.core.compat import StrEnum

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.core.permissions import Permission
    from backend.app.models.user import User


class ProviderAuthType(StrEnum):
    """The kind of credentials a model provider may (optionally) require."""

    NONE = "none"
    ACCESS_TOKEN = "access_token"
    USERNAME_PASSWORD = "username_password"
    BAMBU_CLOUD_BEARER = "bambu_cloud_bearer"  # MakerWorld today: shared Bambu Cloud token
    COOKIE = "cookie"  # reserved for sites without a first-party API


@dataclass(frozen=True)
class ProviderAuthConfig:
    """Declarative description of a provider's authentication requirement.

    Describes *what* the provider needs so the UI can prompt for it; the
    actual storage/retrieval of credentials stays provider-specific for now
    (MakerWorld reads the Bambu Cloud token the user already configured).
    ``credential_fields`` names the inputs a future generic credential vault
    would collect (e.g. ``("access_token",)`` or ``("username", "password")``).
    """

    auth_type: ProviderAuthType
    display_label: str
    description: str = ""
    credential_fields: tuple[str, ...] = ()
    setup_hint: str = ""


@dataclass(frozen=True)
class ProviderResourceRef:
    """Provider-agnostic handle for one model resource.

    ``external_id`` is the provider-native model identifier (MakerWorld's
    integer design id as a string); ``sub_id`` is an optional secondary key
    such as MakerWorld's ``profileId`` for a specific plate.
    """

    source_type: str
    external_id: str
    sub_id: str | None = None
    original_url: str | None = None


@dataclass
class ProviderStatus:
    """Whether the caller can use this provider right now.

    ``auth_error`` carries a human-readable reason when the caller is signed
    in but the stored credential has been rejected (e.g. expired); ``None``
    when there is no error to report.
    """

    authenticated: bool
    can_download: bool
    auth_error: str | None = None


@dataclass
class ProviderResolvedModel:
    """Result of resolving a model URL.

    ``design`` and ``instances`` are provider-specific dicts passed through
    verbatim — the frontend reads fields a provider may add over time, so we
    don't re-shape them here. ``already_imported_library_ids`` is filled in
    by the route layer (it owns the library query).
    """

    ref: ProviderResourceRef
    design: dict[str, Any]
    instances: list[dict[str, Any]] = field(default_factory=list)
    already_imported_library_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderDownloadInfo:
    """A concrete, short-lived download for one file/plate.

    ``ref`` may be enriched by the provider with the ``sub_id`` it resolved
    (e.g. the actual MakerWorld profile selected when the caller omitted
    one) so the route can build the canonical dedupe URL.
    """

    ref: ProviderResourceRef
    url: str
    suggested_filename: str


@dataclass
class ProviderDownload:
    """Downloaded file bytes plus the final suggested filename."""

    file_bytes: bytes
    filename: str


class ProviderError(Exception):
    """Base exception for model-provider API errors."""


class ProviderAuthError(ProviderError):
    """Raised when a provider requires credentials and we have none (or the
    stored one was rejected). True auth failure."""


class ProviderForbiddenError(ProviderError):
    """Raised when a provider refuses access despite valid authentication —
    content-gated (purchase/points required, region restricted, ...)."""


class ProviderNotFoundError(ProviderError):
    """Raised when a model / file / profile doesn't exist."""


class ProviderUnavailableError(ProviderError):
    """Raised on 5xx, network errors, or malformed payloads."""


class ProviderUrlError(ProviderError):
    """Raised when a URL isn't a model page of this provider."""


class ModelProvider(ABC):
    """Static descriptor + factory for one model-hosting site.

    Instances are shared (one per provider); all mutable state lives in the
    per-request :class:`ProviderService` built by :meth:`build_service`.
    """

    source_type: str
    display_name: str
    host_patterns: tuple[str, ...] = ()
    can_download: bool = True
    auth: ProviderAuthConfig | None = None
    default_folder_name: str | None = None
    view_permission: Permission | None = None
    import_permission: Permission | None = None

    @abstractmethod
    async def build_service(
        self,
        *,
        db: AsyncSession,
        user: User | None,
        api_key_owner: User | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> ProviderService:
        """Build a per-request service seeded with the caller's credentials.

        ``api_key_owner`` is the API key's owning user for API-keyed calls
        (see ``resolve_api_key_cloud_owner``); providers use it as the
        fallback identity when ``user`` is None.
        """

    @abstractmethod
    def parse_url(self, url: str) -> ProviderResourceRef:
        """Extract a :class:`ProviderResourceRef` from a model URL.

        Raises :class:`ProviderUrlError` when the URL isn't a model page of
        this provider.
        """

    @abstractmethod
    def canonical_url(self, ref: ProviderResourceRef) -> str:
        """Stable dedupe key for a resource (library ``source_url``).

        All URL variants of the same resource must collapse to this string;
        different resources (e.g. different plates of one model) must differ.
        """

    def supports_url(self, url: str) -> bool:
        """Whether ``url`` points at this provider (host-suffix match).

        Accepts scheme-less input (``makerworld.com/models/1``) the same way
        :meth:`parse_url` does, so ``find_for_url`` routes exactly the URLs
        the provider will then accept.
        """
        if not url or not isinstance(url, str):
            return False
        candidate = url.strip()
        if "://" not in candidate:
            candidate = "https://" + candidate
        try:
            host = (urlparse(candidate).hostname or "").lower()
        except ValueError:
            return False
        return any(host == pattern or host.endswith("." + pattern) for pattern in self.host_patterns)

    def thumbnail_hosts(self) -> tuple[str, ...]:
        """Hosts whose image URLs may be proxied by ``fetch_thumbnail``.

        Serves as the SSRF allowlist for the provider's image proxy; empty
        means the provider has no server-side thumbnail proxy.
        """
        return ()


class ProviderService(ABC):
    """Per-request client for a single provider.

    Built by :meth:`ModelProvider.build_service`, never constructed directly.
    Providers must be closed after use (:meth:`close`); the shared connection
    pool is only closed by the owner.
    """

    @abstractmethod
    async def close(self) -> None:
        """Close the client if this service instance owns it."""

    @abstractmethod
    async def get_status(self, db: AsyncSession) -> ProviderStatus:
        """Report whether the caller can use this provider (credential state)."""

    @abstractmethod
    async def resolve(self, ref: ProviderResourceRef) -> ProviderResolvedModel:
        """Fetch metadata + the importable file/plate list for a resource."""

    @abstractmethod
    async def get_download(self, ref: ProviderResourceRef, instance_id: int | None = None) -> ProviderDownloadInfo:
        """Resolve the concrete download for a resource/file.

        May need provider-specific lookups (e.g. MakerWorld's alphanumeric
        ``modelId``) and may enrich ``ref`` with the resolved ``sub_id``.
        Raises ``ProviderAuthError`` when the provider requires credentials
        and the caller has none.
        """

    @abstractmethod
    async def download(self, info: ProviderDownloadInfo) -> ProviderDownload:
        """Fetch the file bytes for a :class:`ProviderDownloadInfo`."""

    @abstractmethod
    async def fetch_thumbnail(self, url: str) -> tuple[bytes, str]:
        """Proxy a provider CDN image, returning ``(bytes, content_type)``.

        Must restrict the upstream host to :meth:`ModelProvider.thumbnail_hosts`
        (SSRF guard).
        """
