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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import get_stored_token, resolve_api_key_cloud_owner
from backend.app.api.routes.orca_cloud import (
    _ORCA_TYPE_TO_BAMBU,
    _build_authenticated_service as _build_orca_service,
    _load_credentials as _load_orca_credentials,
)
from backend.app.core.auth import RequirePermissionIfAuthEnabled, require_ownership_permission
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
from backend.app.services.orca_cloud import (
    OrcaCloudAuthError,
    OrcaCloudError,
)
from backend.app.services.slicer_api import (
    SlicerApiError,
    SlicerApiService,
)
from backend.app.utils.printer_models import PRINTER_MODEL_MAP

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

# Same shape for Orca Cloud — keyed on (user_id, access_token-fingerprint).
_orca_cloud_cache: dict[tuple[int, str], tuple[float, dict[str, list[UnifiedPreset]]]] = {}


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


async def _fetch_cloud_presets(
    db: AsyncSession, user: User | None, *, refresh: bool = False
) -> tuple[dict[str, list[UnifiedPreset]], str]:
    """Return (slots, cloud_status). Slots are empty when cloud_status != 'ok'.

    Defence-in-depth: even if a stored cloud_token survived a permission
    revocation (admin reset, legacy state), users without ``CLOUD_AUTH`` are
    treated as not-authenticated for this endpoint — the cloud tier never
    surfaces for them. This keeps the per-tier visibility consistent with the
    /cloud/* endpoint suite that already gates on CLOUD_AUTH.

    ``refresh=True`` skips the in-process cache for this call (used by the
    SliceModal's manual Refresh button so a user who just deleted a preset
    in Bambu Studio / Handy can pick up the change without waiting for the
    5-minute TTL to expire). The fresh result is still written back to the
    cache so subsequent non-refresh callers benefit.
    """
    if user is not None and not user.has_permission(Permission.CLOUD_AUTH.value):
        return _empty_slots(), "not_authenticated"

    token, _email, region = await get_stored_token(db, user)
    if not token:
        return _empty_slots(), "not_authenticated"

    user_key = user.id if user is not None else 0
    cache_key = (user_key, _token_fingerprint(token))
    now = time.monotonic()
    if not refresh:
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
        # The metadata-enrich pass (see _enrich_cloud_metadata) compensates:
        # a Bambu Cloud entry without its own filament_type/colour inherits
        # those values from a same-named local / orca_cloud / standard entry
        # so it can still score for type/colour matches in pickFilamentForSlot.
        _cloud_cache[cache_key] = (now, slots)
        return slots, "ok"
    finally:
        await cloud.close()


async def _fetch_orca_cloud_presets(
    db: AsyncSession, user: User | None, *, refresh: bool = False
) -> tuple[dict[str, list[UnifiedPreset]], str]:
    """Mirror of :func:`_fetch_cloud_presets` but for Orca Cloud. Same status
    vocabulary (``ok`` / ``not_authenticated`` / ``expired`` / ``unreachable``),
    same caching shape, same defence-in-depth permission gate
    (``orca_cloud:auth`` rather than ``cloud:auth``).

    Filament metadata (``filament_type`` / ``filament_colour``) is extracted
    from the profile's inline ``content`` dict — unlike Bambu Cloud where
    we'd have to fetch each setting separately and hit a rate limit, Orca's
    ``/sync/pull`` already returns full content per profile, so the metadata
    enrichment is free here.
    """
    if user is not None and not user.has_permission(Permission.ORCA_CLOUD_AUTH.value):
        return _empty_slots(), "not_authenticated"

    creds = await _load_orca_credentials(db, user)
    if not creds.token:
        return _empty_slots(), "not_authenticated"

    user_key = user.id if user is not None else 0
    cache_key = (user_key, _token_fingerprint(creds.token))
    now = time.monotonic()
    if not refresh:
        cached = _orca_cloud_cache.get(cache_key)
        if cached and now - cached[0] < _CLOUD_TTL_S:
            return cached[1], "ok"

    try:
        svc = await _build_orca_service(db, user)
    except HTTPException as e:
        # ``_build_orca_service`` raises 401 when the token is missing,
        # the refresh-token rotation failed, or the JIT refresh hit Orca's
        # backend and got rejected; 502 when Orca is unreachable. Translate
        # to the status vocabulary the SliceModal expects.
        if e.status_code == 401:
            return _empty_slots(), "expired"
        return _empty_slots(), "unreachable"

    try:
        try:
            raw_profiles = await svc.list_profiles()
        except OrcaCloudAuthError:
            return _empty_slots(), "expired"
        except OrcaCloudError as e:
            logger.warning("Orca Cloud preset fetch failed for user %s: %s", user_key, e)
            return _empty_slots(), "unreachable"
        except Exception as e:  # noqa: BLE001 — defensive: never crash the modal
            logger.warning("Orca Cloud preset fetch unexpected error for user %s: %s", user_key, e)
            return _empty_slots(), "unreachable"

        slots = _empty_slots()
        for entry in raw_profiles:
            content = entry.get("content") if isinstance(entry, dict) else None
            if not isinstance(content, dict):
                continue
            slot = _ORCA_TYPE_TO_BAMBU.get(str(content.get("type", "")))
            if slot is None:
                continue
            preset_id = entry.get("id")
            name = entry.get("name") or preset_id
            if not preset_id or not name:
                continue
            filament_type: str | None = None
            filament_colour: str | None = None
            if slot == "filament":
                # Bambu/Orca filament profiles store these as single-element
                # arrays (the historical multi-extruder shape). Extract the
                # first non-empty element for both.
                ft = content.get("filament_type")
                if isinstance(ft, list) and ft and isinstance(ft[0], str):
                    filament_type = ft[0]
                elif isinstance(ft, str):
                    filament_type = ft
                fc = content.get("default_filament_colour")
                if isinstance(fc, list) and fc and isinstance(fc[0], str):
                    filament_colour = fc[0]
                elif isinstance(fc, str):
                    filament_colour = fc
            preset = UnifiedPreset(
                id=str(preset_id),
                name=str(name),
                source="orca_cloud",
                filament_type=filament_type,
                filament_colour=filament_colour,
            )
            if slot in ("process", "filament"):
                # The profile's own compatible-printer list, straight out of
                # the content Orca already hands us (#2628). Without it the
                # SliceModal falls back to reading the printer out of the
                # profile NAME — and a profile whose name carries no model
                # ("Overture PLA Matte @0.2") then reads as "can't tell",
                # which the picker treats as usable and auto-picks for a
                # printer the profile was never built for.
                preset.compatible_printers = _content_compatible_printers(content)
            slots[slot].append(preset)
        _orca_cloud_cache[cache_key] = (now, slots)
        return slots, "ok"
    finally:
        await svc.close()


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
        preset = UnifiedPreset(id=str(p.id), name=p.name, source="local")
        if slot == "filament":
            preset.filament_type, preset.filament_colour = _parse_filament_metadata(p.setting)
        if slot in ("process", "filament"):
            # Precise compatibility link — the slicer's own compatible_printers
            # list, captured at import time. Lets the SliceModal filter the
            # process / filament dropdowns by the selected printer without
            # falling back to the uploaded-bundle index.
            preset.compatible_printers = _parse_compatible_printers(p.compatible_printers)
        slots[slot].append(preset)
    return slots


def _content_compatible_printers(content: dict) -> list[str] | None:
    """Pull ``compatible_printers`` out of an inline profile content dict.

    Orca profiles carry it as a list of printer-preset names (the same shape
    ``orca_profiles.py`` stores on import); a single-printer profile may store
    a bare string. Returns ``None`` for missing / empty / malformed values so
    the caller leaves the field unset and the SliceModal falls back to the
    name-based matcher, rather than treating "no data" as "compatible with
    nothing".
    """
    raw = content.get("compatible_printers")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    names = [s.strip() for s in raw if isinstance(s, str) and s.strip()]
    return names or None


def _parse_compatible_printers(raw: str | None) -> list[str] | None:
    """``LocalPreset.compatible_printers`` stores a JSON array of printer-preset
    names. Return the parsed list, or ``None`` on missing / malformed data so
    the SliceModal falls back to the uploaded-bundle index for that preset."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    names = [s for s in data if isinstance(s, str) and s.strip()]
    return names or None


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


async def _fetch_bundled_presets(db: AsyncSession, *, refresh: bool = False) -> dict[str, list[UnifiedPreset]]:
    """Standard slicer-bundled profiles via the sidecar's /profiles/bundled.

    ``refresh=True`` skips the in-process cache; see _fetch_cloud_presets for
    the same shape and rationale.
    """
    global _bundled_cache
    now = time.monotonic()
    if not refresh and _bundled_cache and now - _bundled_cache[0] < _BUNDLED_TTL_S:
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


def _enrich_cloud_metadata(
    orca_cloud: dict[str, list[UnifiedPreset]],
    cloud: dict[str, list[UnifiedPreset]],
    local: dict[str, list[UnifiedPreset]],
    standard: dict[str, list[UnifiedPreset]],
) -> tuple[
    dict[str, list[UnifiedPreset]],
    dict[str, list[UnifiedPreset]],
    dict[str, list[UnifiedPreset]],
    dict[str, list[UnifiedPreset]],
]:
    """Backfill Bambu Cloud filament metadata; do NOT dedup tiers.

    Every tier surfaces its full list — a name that exists in both ``local``
    and ``orca_cloud`` shows up in BOTH dropdown groups so the user can pick
    either source. Tier ORDER (``local > orca_cloud > cloud > standard``)
    is communicated by the SliceModal's group rendering and by the
    name-collision fallback in ``findPresetByName``; this function does not
    enforce it.

    Filament metadata merge: a Bambu Cloud entry without its own
    ``filament_type`` / ``filament_colour`` (Bambu Cloud doesn't surface
    these in the list response for rate-limiting reasons — see
    :func:`_fetch_cloud_presets`) inherits values from a same-named entry
    in ``local`` / ``orca_cloud`` / ``standard``. This is the only reason
    this function exists post-#1712 — without the enrich the Bambu Cloud
    tier can't score in ``pickFilamentForSlot``.

    Compatibility merge (#2628): the same name bridge carries
    ``compatible_printers`` onto any process / filament entry that lacks it.
    Bambu Cloud never ships the list, so a profile whose NAME carries no
    printer model reads as "compatibility unknown" — which the SliceModal
    treats as usable and auto-picks for whatever printer is selected. When
    the very same profile is also present as a local import or an Orca Cloud
    profile, that copy states the truth; borrowing it turns the auto-pick
    into a correctly-rejected mismatch. Only ever fills a gap: an entry that
    carries its own list keeps it.
    """
    # Build a name → metadata lookup from the tiers that carry it (local,
    # orca_cloud, standard). Bambu cloud is intentionally skipped — it
    # doesn't populate filament_type/colour in the list response. Take
    # whichever non-empty entry shows up first.
    metadata_by_name: dict[str, tuple[str | None, str | None]] = {}
    for tier in (local, orca_cloud, standard):
        for p in tier["filament"]:
            if p.name in metadata_by_name:
                continue
            if p.filament_type or p.filament_colour:
                metadata_by_name[p.name] = (p.filament_type, p.filament_colour)

    # Backfill Bambu Cloud entries that don't have their own metadata.
    for p in cloud["filament"]:
        if (p.filament_type is None or p.filament_colour is None) and p.name in metadata_by_name:
            t, c = metadata_by_name[p.name]
            if p.filament_type is None and t is not None:
                p.filament_type = t
            if p.filament_colour is None and c is not None:
                p.filament_colour = c

    # Compatibility bridge (#2628). Runs over both slots that carry the
    # list, and in both directions between the cloud tiers — whichever copy
    # of a profile knows its printers teaches the ones that don't.
    for slot in ("process", "filament"):
        compat_by_name: dict[str, list[str]] = {}
        for tier in (local, orca_cloud, cloud, standard):
            for p in tier[slot]:
                if p.compatible_printers and p.name not in compat_by_name:
                    compat_by_name[p.name] = p.compatible_printers
        if not compat_by_name:
            continue
        for tier in (orca_cloud, cloud):
            for p in tier[slot]:
                if not p.compatible_printers:
                    borrowed = compat_by_name.get(p.name)
                    if borrowed:
                        p.compatible_printers = list(borrowed)

    return orca_cloud, cloud, local, standard


@router.get("/printer-models")
def list_printer_models() -> dict[str, str]:
    """Canonical Bambu printer-model registry, surfaced for the SliceModal.

    Returns the backend's ``PRINTER_MODEL_MAP`` unmodified: keys are the long
    "Bambu Lab <model>" form that appears in 3MF metadata and in slicer
    printer-preset names, values are the normalized short codes used in
    BambuStudio's `@BBL <code>` cloud-preset filenames. The frontend uses this
    mapping to classify cloud / standard presets against the selected printer
    when no slicer bundle has been uploaded that covers the preset (#1325
    follow-up) - avoiding a second, manually-maintained model table on the
    frontend. No auth gate: this is a static reference dictionary, not
    user data.
    """
    return dict(PRINTER_MODEL_MAP)


@router.get("/presets", response_model=UnifiedPresetsResponse)
async def list_unified_presets(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.LIBRARY_UPLOAD),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
    refresh: bool = Query(
        False,
        description=(
            "Bypass the in-process cloud and bundled-preset caches for this "
            "request. The SliceModal's Refresh button sets this so users who "
            "deleted a preset in Bambu Studio or Bambu Handy don't have to "
            "wait for the 5-minute cloud-cache TTL to expire."
        ),
    ),
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
    orca_cloud, orca_cloud_status = await _fetch_orca_cloud_presets(db, cloud_token_user, refresh=refresh)
    cloud, cloud_status = await _fetch_cloud_presets(db, cloud_token_user, refresh=refresh)
    local = await _fetch_local_presets(db)
    standard = await _fetch_bundled_presets(db, refresh=refresh)

    orca_cloud, cloud, local, standard = _enrich_cloud_metadata(orca_cloud, cloud, local, standard)

    return UnifiedPresetsResponse(
        orca_cloud=UnifiedPresetsBySlot(**orca_cloud),
        cloud=UnifiedPresetsBySlot(**cloud),
        local=UnifiedPresetsBySlot(**local),
        standard=UnifiedPresetsBySlot(**standard),
        cloud_status=cloud_status,
        orca_cloud_status=orca_cloud_status,
    )


@router.get("/preview-progress/{request_id}")
async def get_preview_slice_progress(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    _: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
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
