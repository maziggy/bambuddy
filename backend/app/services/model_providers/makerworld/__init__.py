"""MakerWorld model provider package.

Exports the provider instance the registry consumes; the implementation lives
in the sibling modules (``service``, ``http``, ``url``, ``errors``, ``auth``).
"""

from backend.app.services.model_providers.makerworld.provider import (
    MakerWorldProvider,
    makerworld_provider,
)

__all__ = ["MakerWorldProvider", "makerworld_provider"]
