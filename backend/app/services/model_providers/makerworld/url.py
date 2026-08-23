"""MakerWorld URL parsing and canonicalisation.

Extracts ``(model_id, profile_id_or_None)`` from model URLs and builds the
stable dedupe key used as the library ``source_url``. Rejects non-makerworld
hosts — this is the input-validation surface, so it is deliberately strict.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from backend.app.services.model_providers.base import ProviderResourceRef
from backend.app.services.model_providers.makerworld.errors import MakerWorldUrlError

MAKERWORLD_HOST = "makerworld.com"  # Used only for URL parsing (input validation)

_MODEL_ID_RE = re.compile(r"/models/(\d+)")
_PROFILE_ID_RE = re.compile(r"#profileId[-=](\d+)")


def parse_url(url: str) -> ProviderResourceRef:
    """Extract a :class:`ProviderResourceRef` from a MakerWorld URL.

    Accepts any of:
      - ``https://makerworld.com/en/models/1400373``
      - ``https://makerworld.com/en/models/1400373-slug-with-dashes``
      - ``https://makerworld.com/en/models/1400373#profileId-1452154``
      - ``makerworld.com/models/1400373`` (scheme optional)

    Rejects non-makerworld hosts.
    """
    if not url or not isinstance(url, str):
        raise MakerWorldUrlError("URL is empty or not a string")
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        raise MakerWorldUrlError(f"Could not parse URL: {exc}") from exc

    host = (parsed.hostname or "").lower()
    if host != MAKERWORLD_HOST and not host.endswith("." + MAKERWORLD_HOST):
        raise MakerWorldUrlError(f"Not a MakerWorld URL (host={host!r}); expected makerworld.com")

    model_match = _MODEL_ID_RE.search(parsed.path)
    if not model_match:
        raise MakerWorldUrlError("URL does not contain a /models/{id} segment")
    model_id = int(model_match.group(1))

    profile_id: int | None = None
    if parsed.fragment:
        profile_match = _PROFILE_ID_RE.search("#" + parsed.fragment)
        if profile_match:
            profile_id = int(profile_match.group(1))

    return ProviderResourceRef(
        source_type="makerworld",
        external_id=str(model_id),
        sub_id=str(profile_id) if profile_id is not None else None,
        original_url=url,
    )


def canonical_url(ref: ProviderResourceRef) -> str:
    """Build a stable dedupe key for a MakerWorld resource.

    Dedupe is keyed per *plate* (profile) rather than per model, since the
    download returns a specific plate — not the full multi-plate zip — so two
    different plates of the same design should become two separate library
    entries. Canonical shape uses the locale-free path with the
    ``#profileId-`` fragment so all URL variants of the same plate still
    collapse (e.g. ``/en/models/123-slug?from=search#profileId-456`` and
    ``/de/models/123#profileId-456`` both map to
    ``https://makerworld.com/models/123#profileId-456``). Plate-less imports
    (legacy or whole-design) keep the old model-only shape for backwards
    compatibility with existing rows.
    """
    if ref.sub_id:
        return f"https://makerworld.com/models/{ref.external_id}#profileId-{ref.sub_id}"
    return f"https://makerworld.com/models/{ref.external_id}"
