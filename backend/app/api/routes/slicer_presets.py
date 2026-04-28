"""Unified slicer-preset listing for the SliceModal (#wiki / Cloud-aware presets).

Returns the printer/process/filament options grouped by source tier in
priority order — cloud (per-user, live-fetched) > local (DB-backed
imports) > standard (slicer-bundled stock fallback). Name-based dedup is
applied so a preset that exists in multiple tiers only appears in the
highest-priority one. Cloud failure modes (signed out / expired / network)
are surfaced via a status field so the modal can render a precise banner
without faking an "ok with empty list" response.
"""

from __future__ import annotations

import hashlib
import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import get_stored_token
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.local_preset import LocalPreset
from backend.app.models.user import User
from backend.app.schemas.slicer_presets import (
    UnifiedPreset,
    UnifiedPresetsBySlot,
    UnifiedPresetsResponse,
)
from backend.app.services.bambu_cloud import (
    BambuCloudAuthError,
    BambuCloudError,
    BambuCloudService,
)
from backend.app.services.slicer_api import SlicerApiError, SlicerApiService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slicer", tags=["Slicer Presets"])


# In-process cache for the bundled-profile list. The slicer sidecar walks a
# read-only filesystem inside its own container, so the list only changes
# across sidecar rebuilds — a long TTL is safe and avoids a sidecar round-trip
# on every modal open. Per-user cache is unnecessary because bundled profiles
# are global.
_BUNDLED_TTL_S = 3600.0
_bundled_cache: tuple[float, dict[str, list[UnifiedPreset]]] | None = None

# Per-user cache for the cloud preset list. Cache key is (user_id, token_hash):
# keying on the token hash means a logout/login or token-change automatically
# invalidates the entry without needing the cloud-auth route handlers to call
# back into this module. 5 minutes balances "users see their freshly-saved
# presets quickly" against "a busy install doesn't hit the cloud once per
# modal open per user".
_CLOUD_TTL_S = 300.0
_cloud_cache: dict[tuple[int, str], tuple[float, dict[str, list[UnifiedPreset]]]] = {}


def _token_fingerprint(token: str) -> str:
    """Short stable hash of the cloud token for use as a cache-key component.
    Storing only the hash means we can safely keep multiple per-(user, token)
    entries without leaking the token via the in-process dict."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


_CLOUD_TYPE_TO_SLOT = {
    "filament": "filament",
    "printer": "printer",
    "print": "process",  # Bambu Cloud calls process presets "print"
}


def _empty_slots() -> dict[str, list[UnifiedPreset]]:
    return {"printer": [], "process": [], "filament": []}


async def _fetch_cloud_presets(db: AsyncSession, user: User | None) -> tuple[dict[str, list[UnifiedPreset]], str]:
    """Return (slots, cloud_status). Slots are empty when cloud_status != 'ok'.

    Defence-in-depth: even if a stored cloud_token survived a permission
    revocation (admin reset, legacy state), users without ``CLOUD_AUTH`` are
    treated as not-authenticated for this endpoint — the cloud tier never
    surfaces for them. This keeps the per-tier visibility consistent with the
    /cloud/* endpoint suite that already gates on CLOUD_AUTH.
    """
    if user is not None and not user.has_permission(Permission.CLOUD_AUTH.value):
        return _empty_slots(), "not_authenticated"

    token, _email, region = await get_stored_token(db, user)
    if not token:
        return _empty_slots(), "not_authenticated"

    user_key = user.id if user is not None else 0
    cache_key = (user_key, _token_fingerprint(token))
    now = time.monotonic()
    cached = _cloud_cache.get(cache_key)
    if cached and now - cached[0] < _CLOUD_TTL_S:
        return cached[1], "ok"

    cloud = BambuCloudService(region=region)
    cloud.set_token(token)
    try:
        raw = await cloud.get_slicer_settings()
    except BambuCloudAuthError:
        # Don't clear the token here — the cloud-status endpoint owns that
        # lifecycle. Just report expired so the UI can prompt re-auth.
        return _empty_slots(), "expired"
    except BambuCloudError as e:
        logger.warning("Cloud preset fetch failed for user %s: %s", user_key, e)
        return _empty_slots(), "unreachable"
    except Exception as e:  # noqa: BLE001 — defensive: never crash the modal
        logger.warning("Cloud preset fetch unexpected error for user %s: %s", user_key, e)
        return _empty_slots(), "unreachable"
    finally:
        await cloud.close()

    slots = _empty_slots()
    for cloud_type, slot in _CLOUD_TYPE_TO_SLOT.items():
        type_data = raw.get(cloud_type, {})
        # The cloud splits presets into "private" (the user's own) and "public"
        # (Bambu's stock cloud presets). Both are valid choices — surface them
        # in the natural order private → public so a user's customisations
        # appear above the stock entries with the same names. Stock entries
        # that share names with private ones get deduped out within the cloud
        # tier itself.
        seen_names: set[str] = set()
        for entry in type_data.get("private", []) + type_data.get("public", []):
            name = entry.get("name")
            setting_id = entry.get("setting_id") or entry.get("id")
            if not name or not setting_id or name in seen_names:
                continue
            seen_names.add(name)
            slots[slot].append(UnifiedPreset(id=setting_id, name=name, source="cloud"))

    _cloud_cache[cache_key] = (now, slots)
    return slots, "ok"


async def _fetch_local_presets(db: AsyncSession) -> dict[str, list[UnifiedPreset]]:
    """Local imports — no caching needed, single indexed DB read."""
    result = await db.execute(select(LocalPreset).order_by(LocalPreset.name))
    presets = result.scalars().all()
    slots = _empty_slots()
    type_to_slot = {"filament": "filament", "printer": "printer", "process": "process"}
    for p in presets:
        slot = type_to_slot.get(p.preset_type)
        if slot is None:
            continue
        slots[slot].append(UnifiedPreset(id=str(p.id), name=p.name, source="local"))
    return slots


async def _fetch_bundled_presets(db: AsyncSession) -> dict[str, list[UnifiedPreset]]:
    """Standard slicer-bundled profiles via the sidecar's /profiles/bundled."""
    global _bundled_cache
    now = time.monotonic()
    if _bundled_cache and now - _bundled_cache[0] < _BUNDLED_TTL_S:
        return _bundled_cache[1]

    api_url = await _resolve_slicer_api_url(db)
    if not api_url:
        # No sidecar configured at all — return empty rather than caching, so
        # users who configure one mid-session see results on next open.
        return _empty_slots()

    try:
        async with SlicerApiService(base_url=api_url) as svc:
            raw = await svc.list_bundled_profiles()
    except SlicerApiError as e:
        logger.info("Bundled preset fetch from sidecar at %s failed: %s", api_url, e)
        return _empty_slots()
    except Exception as e:  # noqa: BLE001 — never break the modal on sidecar issues
        logger.warning("Bundled preset fetch unexpected error: %s", e)
        return _empty_slots()

    slots = _empty_slots()
    for slot in ("printer", "process", "filament"):
        for entry in raw.get(slot, []) or []:
            name = entry.get("name")
            if not name:
                continue
            # Bundled presets are addressed by name (the slicer resolves them
            # by name during the `inherits:` walk), so name doubles as id.
            slots[slot].append(UnifiedPreset(id=name, name=name, source="standard"))

    _bundled_cache = (now, slots)
    return slots


async def _resolve_slicer_api_url(db: AsyncSession) -> str | None:
    """Pick the sidecar URL the bundled-listing fetch should hit.

    Mirrors the slice route's resolution at ``library.py:_run_slicer_with_fallback``:
    the user's ``preferred_slicer`` setting decides which sidecar Bambuddy
    talks to, and the per-install URL setting overrides the env default.
    A user who prefers Bambu Studio gets the *bambu-studio-api* sidecar's
    bundled list; a user who prefers OrcaSlicer gets the *orca-slicer-api*
    sidecar's bundled list. Without this branch the listing would always
    hit OrcaSlicer (port 3003) even for BambuStudio installs (port 3001),
    leaving the Standard tier permanently empty for them.
    """
    from backend.app.api.routes.settings import get_setting

    preferred = (await get_setting(db, "preferred_slicer")) or "bambu_studio"
    if preferred == "orcaslicer":
        configured = await get_setting(db, "orcaslicer_api_url")
        url = (configured or app_settings.slicer_api_url).strip()
    elif preferred == "bambu_studio":
        configured = await get_setting(db, "bambu_studio_api_url")
        url = (configured or app_settings.bambu_studio_api_url).strip()
    else:
        # Unknown preference — return None so the bundled tier is empty
        # rather than crashing the modal. The slice route raises 400 here;
        # we degrade silently because the modal's listing is informational.
        logger.warning("Unknown preferred_slicer setting: %r — bundled tier disabled", preferred)
        return None
    return url or None


def _dedupe_by_name(
    cloud: dict[str, list[UnifiedPreset]],
    local: dict[str, list[UnifiedPreset]],
    standard: dict[str, list[UnifiedPreset]],
) -> tuple[
    dict[str, list[UnifiedPreset]],
    dict[str, list[UnifiedPreset]],
    dict[str, list[UnifiedPreset]],
]:
    """Filter so each preset name appears in exactly one tier (cloud > local > standard).

    Order within each tier is preserved as-is — only "lower-priority duplicates"
    are dropped. A preset shared across tiers (e.g. "Bambu PLA Basic" in cloud
    public AND standard bundled) only renders once, in the cloud tier.
    """
    deduped_local = _empty_slots()
    deduped_standard = _empty_slots()
    for slot in ("printer", "process", "filament"):
        seen = {p.name for p in cloud[slot]}
        for p in local[slot]:
            if p.name in seen:
                continue
            deduped_local[slot].append(p)
            seen.add(p.name)
        for p in standard[slot]:
            if p.name in seen:
                continue
            deduped_standard[slot].append(p)
            seen.add(p.name)
    return cloud, deduped_local, deduped_standard


@router.get("/presets", response_model=UnifiedPresetsResponse)
async def list_unified_presets(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_UPLOAD),
) -> UnifiedPresetsResponse:
    """List slicer presets across cloud / local / standard tiers, deduped by name.

    Drives the SliceModal preset dropdowns. Permission gate matches the
    slice action itself (``LIBRARY_UPLOAD``) so any user who can slice can
    see the preset options for the dialog. The cloud branch is independently
    gated on ``CLOUD_AUTH`` inside ``_fetch_cloud_presets`` so a user with
    only ``LIBRARY_UPLOAD`` doesn't see cloud presets they shouldn't have
    access to.
    """
    cloud, cloud_status = await _fetch_cloud_presets(db, current_user)
    local = await _fetch_local_presets(db)
    standard = await _fetch_bundled_presets(db)

    cloud, local, standard = _dedupe_by_name(cloud, local, standard)

    return UnifiedPresetsResponse(
        cloud=UnifiedPresetsBySlot(**cloud),
        local=UnifiedPresetsBySlot(**local),
        standard=UnifiedPresetsBySlot(**standard),
        cloud_status=cloud_status,
    )
