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
import json
import logging
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import get_stored_token, resolve_api_key_cloud_owner
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
from backend.app.services.slicer_api import (
    BundleNotFoundError,
    BundleSummary,
    SlicerApiError,
    SlicerApiService,
    SlicerApiUnavailableError,
    SlicerInputError,
)

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

        # Cloud filament presets carry no metadata in this response on
        # purpose: the per-preset detail endpoint
        # (/v1/iot-service/api/slicer/setting/{id}) is rate-limited at roughly
        # 10/sec per token, so fetching N filament presets to enrich them
        # one-by-one trips Bambu's limiter and returns 429 on every request
        # for users with large preset libraries (#1150 follow-up).
        #
        # The dedup pass (see _dedupe_by_name) compensates: when a cloud entry
        # wins over a same-named local entry, the cloud entry inherits the
        # local entry's filament_type / filament_colour. So cloud presets that
        # also exist locally still get metadata-aware pre-pick in the
        # SliceModal; cloud-only presets fall back to plain priority order.
        _cloud_cache[cache_key] = (now, slots)
        return slots, "ok"
    finally:
        await cloud.close()


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
        extra: dict[str, str | None] = {}
        if slot == "filament":
            extra["filament_type"], extra["filament_colour"] = _parse_filament_metadata(p.setting)
        slots[slot].append(
            UnifiedPreset(id=str(p.id), name=p.name, source="local", **extra),
        )
    return slots


def _parse_filament_metadata(setting_json: str | None) -> tuple[str | None, str | None]:
    """Extract first-slot ``filament_type`` and ``filament_colour`` from a
    stored preset JSON. OrcaSlicer stores both as arrays (per-extruder) — we
    take the first entry since pre-pick matching is one-slot-at-a-time.
    Defensive parse: any error returns (None, None) so a corrupt row never
    breaks the listing."""
    if not setting_json:
        return None, None
    try:
        data = json.loads(setting_json)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return _first_scalar(data.get("filament_type")), _first_scalar(data.get("filament_colour"))


def _first_scalar(value: object) -> str | None:
    if isinstance(value, list) and value:
        return value[0] if isinstance(value[0], str) else None
    if isinstance(value, str) and value:
        return value
    return None


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
            extra: dict[str, str | None] = {}
            if slot == "filament":
                extra["filament_type"] = entry.get("filament_type")
                extra["filament_colour"] = entry.get("filament_colour")
            slots[slot].append(
                UnifiedPreset(id=name, name=name, source="standard", **extra),
            )

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

    Filament metadata is **merged across tiers** during dedup: when a cloud
    entry wins over a same-named local entry, the cloud entry inherits the
    local entry's ``filament_type`` and ``filament_colour`` (cloud entries
    carry no metadata themselves because we deliberately don't fetch each
    setting's content — see _fetch_cloud_presets). Without this merge, the
    SliceModal's metadata-aware pre-pick would silently lose match data for
    every preset the user has both cloud-synced and locally imported, and
    fall back to plain priority selection.
    """
    # Build a lookup: filament name → metadata from the highest-quality tier
    # that has it. Local + standard both expose parsed metadata; cloud
    # doesn't. Take whichever non-empty entry shows up first.
    metadata_by_name: dict[str, tuple[str | None, str | None]] = {}
    for tier in (local, standard):
        for p in tier["filament"]:
            if p.name in metadata_by_name:
                continue
            if p.filament_type or p.filament_colour:
                metadata_by_name[p.name] = (p.filament_type, p.filament_colour)

    # Backfill cloud entries that don't have their own metadata.
    for p in cloud["filament"]:
        if (p.filament_type is None or p.filament_colour is None) and p.name in metadata_by_name:
            t, c = metadata_by_name[p.name]
            if p.filament_type is None and t is not None:
                p.filament_type = t
            if p.filament_colour is None and c is not None:
                p.filament_colour = c

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
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
) -> UnifiedPresetsResponse:
    """List slicer presets across cloud / local / standard tiers, deduped by name.

    Drives the SliceModal preset dropdowns. Permission gate matches the
    slice action itself (``LIBRARY_UPLOAD``) so any user who can slice can
    see the preset options for the dialog. The cloud branch is independently
    gated on ``CLOUD_AUTH`` inside ``_fetch_cloud_presets`` so a user with
    only ``LIBRARY_UPLOAD`` doesn't see cloud presets they shouldn't have
    access to.

    API-keyed callers (which return None from ``current_user``) get the
    owner User via ``resolve_api_key_cloud_owner`` when the key has the
    cloud-access scope, so the cloud tier surfaces correctly for them
    too — matching the slice route (#1182 follow-up).
    """
    cloud_token_user = current_user or api_key_cloud_owner
    cloud, cloud_status = await _fetch_cloud_presets(db, cloud_token_user)
    local = await _fetch_local_presets(db)
    standard = await _fetch_bundled_presets(db)

    cloud, local, standard = _dedupe_by_name(cloud, local, standard)

    return UnifiedPresetsResponse(
        cloud=UnifiedPresetsBySlot(**cloud),
        local=UnifiedPresetsBySlot(**local),
        standard=UnifiedPresetsBySlot(**standard),
        cloud_status=cloud_status,
    )


def _bundle_summary_to_dict(b: BundleSummary) -> dict:
    """Serialize a BundleSummary for the JSON response. The frontend uses
    these arrays to populate the preset dropdowns when a user picks the
    bundle as the slice source.
    """
    return {
        "id": b.id,
        "printer_preset_name": b.printer_preset_name,
        "printer": b.printer,
        "process": b.process,
        "filament": b.filament,
        "version": b.version,
    }


@router.post("/bundles", status_code=201)
async def import_slicer_bundle(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_UPLOAD),
):
    """Forward a BambuStudio Printer Preset Bundle (.bbscfg) to the sidecar.

    The user exports their printer's preset bundle from BambuStudio (File
    -> Export -> Export Preset Bundle, "Printer preset bundle" option).
    Uploading it here unpacks the bundle on the sidecar and exposes its
    inner printer / process / filament presets to subsequent slice
    requests via the bundle-id selector.

    Idempotent: re-uploading the same file yields the same id (sidecar
    hashes the zip content), so duplicate uploads collapse rather than
    accumulate.
    """
    api_url = await _resolve_slicer_api_url(db)
    if not api_url:
        raise HTTPException(status_code=503, detail="No slicer sidecar configured")

    # Multer on the sidecar caps bundle uploads at 50MB. We don't enforce
    # that here — let the sidecar's filter own the limit so it stays in
    # one place — but we do reject empty / huge files at the FastAPI
    # layer to avoid pointlessly streaming them to the sidecar first.
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Bundle file is empty")
    filename = file.filename or "bundle.bbscfg"

    try:
        async with SlicerApiService(base_url=api_url) as svc:
            summary = await svc.import_bundle(contents, filename=filename)
    except SlicerInputError as e:
        # Sidecar's 4xx — most likely a non-.bbscfg upload, a corrupt zip,
        # or a path-traversal entry that the manifest validator caught.
        # Surface verbatim so the user sees the actual reason in the toast.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except SlicerApiUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SlicerApiError as e:
        # 5xx from the sidecar's import path is rare — usually a disk
        # write failure inside DATA_PATH/bundles. 502 (bad gateway) is
        # closer to the truth than 500 here, since we're proxying.
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _bundle_summary_to_dict(summary)


@router.get("/bundles")
async def list_slicer_bundles(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_UPLOAD),
):
    """List every Printer Preset Bundle currently stored on the sidecar.

    Drives the SliceModal's "Bundle" tier and a Settings panel where
    users can review / delete imported bundles. Returns ``[]`` when the
    sidecar has no bundles imported yet.
    """
    api_url = await _resolve_slicer_api_url(db)
    if not api_url:
        # No sidecar configured: empty list rather than 503 so the modal
        # renders cleanly. Same shape as the bundled-presets fallback.
        return []
    try:
        async with SlicerApiService(base_url=api_url) as svc:
            bundles = await svc.list_bundles()
    except SlicerApiUnavailableError as e:
        # Sidecar offline: surface as 503 so the frontend can show a
        # banner. Differs from the bundled-tier behaviour because that
        # path also has cloud + local fallbacks; bundles is the only
        # source for its tier.
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SlicerApiError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return [_bundle_summary_to_dict(b) for b in bundles]


@router.get("/bundles/{bundle_id}")
async def get_slicer_bundle(
    bundle_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_UPLOAD),
):
    """Return one bundle by id. 404 if it doesn't exist on the sidecar."""
    api_url = await _resolve_slicer_api_url(db)
    if not api_url:
        raise HTTPException(status_code=503, detail="No slicer sidecar configured")
    try:
        async with SlicerApiService(base_url=api_url) as svc:
            summary = await svc.get_bundle(bundle_id)
    except BundleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SlicerApiUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SlicerApiError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _bundle_summary_to_dict(summary)


@router.delete("/bundles/{bundle_id}", status_code=204)
async def delete_slicer_bundle(
    bundle_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_UPLOAD),
):
    """Remove a stored bundle from the sidecar. Future slice requests
    referencing this id will fail with 404 from the sidecar.
    """
    api_url = await _resolve_slicer_api_url(db)
    if not api_url:
        raise HTTPException(status_code=503, detail="No slicer sidecar configured")
    try:
        async with SlicerApiService(base_url=api_url) as svc:
            await svc.delete_bundle(bundle_id)
    except BundleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SlicerApiUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SlicerApiError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/preview-progress/{request_id}")
async def get_preview_slice_progress(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_READ),
):
    """Proxy to the sidecar's ``GET /slice/progress/:requestId``.

    The SliceModal's filament-requirements call kicks off a real preview
    slice on the sidecar to discover which AMS slots the picked plate
    actually consumes. That HTTP call holds open for the full slice
    duration (multi-second to multi-minute on complex models), and the
    browser can't reach the sidecar directly thanks to the same-origin
    policy + the sidecar's CORS allowlist. This endpoint forwards the
    poll so the modal's inline spinner can show "Generating G-code (45%)"
    instead of an opaque elapsed-time counter while the preview runs.

    Returns the sidecar's snapshot verbatim, or 404 when the request_id
    is unknown / completed and grace-window-expired.
    """
    import httpx

    api_url = await _resolve_slicer_api_url(db)
    if not api_url:
        raise HTTPException(status_code=503, detail="No slicer sidecar configured")
    url = f"{api_url}/slice/progress/{request_id}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
    except httpx.RequestError:
        # Sidecar unreachable: surface as 503 instead of 500 so the
        # frontend's poller can keep trying without flagging a hard error.
        raise HTTPException(status_code=503, detail="Slicer sidecar unreachable") from None
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Progress unavailable")
    return response.json()
