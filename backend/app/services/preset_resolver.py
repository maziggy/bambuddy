"""Resolve a `PresetRef` (source + id) to the JSON-string content the
slicer-api sidecar's `/slice` endpoint expects.

Three sources, three paths:

- **local**   — read ``LocalPreset.setting`` from the DB. Existing pre-PR
                behaviour for the slicer integration; preserved verbatim
                so clients still sending bare integer ids see no change.
- **cloud**   — fetch ``BambuCloudService.get_setting_detail(id)`` for the
                caller's stored cloud token. Result is the full slicer-shape
                preset JSON the sidecar can ingest directly.
- **standard** — emit a stub ``{inherits: <name>, from: "system"}``. The
                 sidecar's `bambuddy/profile-resolver` branch already walks
                 ``inherits:`` against ``BUNDLED_PROFILES_PATH/<category>/<name>.json``
                 during ``materializeProfile`` and merges parent-then-child,
                 so the stub flattens out to the bundled content with no
                 round-trip needed for the JSON itself.

All three return the JSON as a *string* because that's what
``SlicerApiService.slice_with_profiles`` accepts as
``printer_profile_json`` etc. — the sidecar parses it once.
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import get_stored_token
from backend.app.core.permissions import Permission
from backend.app.models.local_preset import LocalPreset
from backend.app.models.user import User
from backend.app.schemas.slicer import PresetRef
from backend.app.services.bambu_cloud import (
    BambuCloudAuthError,
    BambuCloudError,
    BambuCloudService,
)

logger = logging.getLogger(__name__)


_SLOT_TO_BUNDLED_CATEGORY = {
    "printer": "machine",
    "process": "process",
    "filament": "filament",
}

# The CLI's --load-settings parser uses the JSON's `type` field to decide
# how to interpret each file (machine/process/filament). Without it the
# CLI logs `operator(): unknown config type ... in load-settings`,
# writes `error_string: "The input preset file is invalid and can not be
# parsed.", return_code: -5` to result.json, and exits 0 — which the
# Node sidecar's child_process treats as silent success producing no
# output, then bubbles up as a generic "Failed to slice the model" 5xx.
# Bambuddy then falls back to the embedded-settings path for every 3MF
# slice, silently using whatever printer the source file was originally
# bound to. Setting `type` correctly per slot fixes the silent fallback.
_SLOT_TO_PROFILE_TYPE = {
    "printer": "machine",
    "process": "process",
    "filament": "filament",
}


async def resolve_preset_ref(
    db: AsyncSession,
    user: User | None,
    ref: PresetRef,
    slot: str,
) -> str:
    """Return the JSON-string content for `ref` so the sidecar can ingest it.

    `slot` is one of ``"printer"`` / ``"process"`` / ``"filament"``; it's
    only used to generate friendly error messages and to pick the bundled
    category for the standard tier.

    Raises ``HTTPException`` for any caller-facing error (invalid id, wrong
    preset type, cloud auth failure, network error fetching cloud detail).
    """
    if ref.source == "local":
        return await _resolve_local(db, ref, slot)
    if ref.source == "cloud":
        return await _resolve_cloud(db, user, ref, slot)
    if ref.source == "standard":
        return _resolve_standard(ref, slot)
    raise HTTPException(
        status_code=400,
        detail=f"Unknown preset source for {slot}: {ref.source!r}",
    )


async def _resolve_local(db: AsyncSession, ref: PresetRef, slot: str) -> str:
    try:
        local_id = int(ref.id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid local preset id for {slot}: {ref.id!r}") from None
    preset = await db.get(LocalPreset, local_id)
    if preset is None or preset.preset_type != slot:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {slot} preset id (expected preset_type='{slot}')",
        )
    return preset.setting


async def _resolve_cloud(db: AsyncSession, user: User | None, ref: PresetRef, slot: str) -> str:
    """Fetch a single cloud preset detail. Permission gate matches the
    rest of the cloud surface (`CLOUD_AUTH`) so a user with `LIBRARY_UPLOAD`
    but no `CLOUD_AUTH` can't slice using cloud presets even if their
    ``User.cloud_token`` survived a permission revocation."""
    if user is not None and not user.has_permission(Permission.CLOUD_AUTH.value):
        raise HTTPException(
            status_code=403,
            detail=f"Cloud presets require the cloud:auth permission ({slot})",
        )

    token, _email, region = await get_stored_token(db, user)
    if not token:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cloud preset selected for {slot}, but no Bambu Cloud session is "
                "stored. Sign in to Bambu Cloud and retry."
            ),
        )

    cloud = BambuCloudService(region=region)
    cloud.set_token(token)
    try:
        detail = await cloud.get_setting_detail(ref.id)
    except BambuCloudAuthError:
        raise HTTPException(
            status_code=401,
            detail=(f"Bambu Cloud session expired while fetching {slot} preset. Sign in again and retry."),
        ) from None
    except BambuCloudError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Bambu Cloud unreachable while fetching {slot} preset: {e}",
        ) from e
    finally:
        await cloud.close()

    # `get_setting_detail` returns the wrapper envelope; the actual preset
    # JSON lives under `.setting`. The sidecar wants the preset content, not
    # the envelope.
    payload = detail.get("setting") if isinstance(detail, dict) else None
    if not isinstance(payload, dict):
        # Some endpoints return the preset at the top level instead of
        # nested under `setting`. Fall back to the whole response in that
        # case rather than failing — the sidecar will reject it cleanly if
        # the shape is genuinely wrong, and we log the unusual response.
        logger.info(
            "Cloud preset %r for %s returned unexpected shape, forwarding raw payload",
            ref.id,
            slot,
        )
        payload = detail
    return json.dumps(payload)


def _resolve_standard(ref: PresetRef, slot: str) -> str:
    """Build a minimal `{name, inherits, from, type}` stub. The sidecar's
    resolver walks `BUNDLED_PROFILES_PATH/<category>/<name>.json` and merges,
    yielding the full bundled preset without us round-tripping the content
    through Bambuddy."""
    if slot not in _SLOT_TO_BUNDLED_CATEGORY:
        raise HTTPException(status_code=400, detail=f"Unknown slot for standard preset: {slot!r}")
    return json.dumps(
        {
            # `name` must be set so the sidecar's compatibility checks see a
            # populated value. Reusing the bundled name keeps the resolved
            # profile's identity consistent with what the user picked.
            "name": ref.id,
            "inherits": ref.id,
            # `from: "system"` skips the User/system compatibility rejection
            # the resolver was designed to fix for OrcaSlicer GUI exports —
            # we never want a bundled preset to be treated as User-authored.
            "from": "system",
            # `type` is required by the CLI's --load-settings parser — see
            # _SLOT_TO_PROFILE_TYPE above for the silent-failure mode.
            "type": _SLOT_TO_PROFILE_TYPE[slot],
        }
    )
