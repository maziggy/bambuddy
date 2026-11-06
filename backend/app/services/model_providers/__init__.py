"""Model-provider interface + registry.

The shared seam for "import a model from a 3D model website". Providers
implement the interface (a ``ModelProvider`` descriptor + a per-request
``ProviderService`` transport) and register an instance here; the registry
routes pasted URLs to the owning provider via ``find_for_url``. MakerWorld
claims pasted URLs; Manyfold (#1471) is self-hosted and browsed, so it claims
none.
"""

from backend.app.services.model_providers.base import (
    ModelProvider,
    ProviderAuthConfig,
    ProviderAuthError,
    ProviderAuthType,
    ProviderDownload,
    ProviderDownloadInfo,
    ProviderError,
    ProviderForbiddenError,
    ProviderNotFoundError,
    ProviderResolvedModel,
    ProviderResourceRef,
    ProviderService,
    ProviderStatus,
    ProviderUnavailableError,
    ProviderUrlError,
)
from backend.app.services.model_providers.makerworld import makerworld_provider
from backend.app.services.model_providers.manyfold import manyfold_provider
from backend.app.services.model_providers.registry import ModelProviderRegistry, registry

registry.register(makerworld_provider)
registry.register(manyfold_provider)

__all__ = [
    "ModelProvider",
    "ModelProviderRegistry",
    "ProviderAuthConfig",
    "ProviderAuthError",
    "ProviderAuthType",
    "ProviderDownload",
    "ProviderDownloadInfo",
    "ProviderError",
    "ProviderForbiddenError",
    "ProviderNotFoundError",
    "ProviderResolvedModel",
    "ProviderResourceRef",
    "ProviderService",
    "ProviderStatus",
    "ProviderUnavailableError",
    "ProviderUrlError",
    "makerworld_provider",
    "manyfold_provider",
    "registry",
]
