"""Preview-slice cache for the SliceModal.

The slice modal needs the per-plate filament list before the user picks
profiles. For sliced files this lives in ``Metadata/slice_info.config`` and
the ``/filament-requirements`` endpoint can read it directly. For unsliced
project files it doesn't exist yet — only the slicer can produce it, since
Bambu Studio applies its own pruning to painted-face data at slice time.

This module wraps the sidecar's slice call so the endpoint can run a preview
slice, parse the result's slice_info, and return the actual filament list.
Two slice modes are supported:

  * "embedded settings" mode (default) — calls ``slice_without_profiles`` so
    the slicer falls back on the file's own ``Metadata/project_settings.config``.
    Used when the SliceModal opens before the user has picked a profile
    triplet and we just want the slot-mapping (which is a model property,
    independent of process settings).

  * "bundle" mode — when the caller passes a bundle id + per-category preset
    names, calls ``slice_with_bundle`` so the preview reflects the same
    triplet the real print will use. More accurate gram numbers; same slot
    mapping. Used after the SliceModal's Bundle tier resolves.

Results are cached by ``(kind, source_id, plate_id, content_hash, bundle_key)``
so different bundle picks on the same file don't collide and repeat opens
on the same plate + same bundle are instant. LRU eviction keeps the cache
bounded. Hash invalidation handles in-place file replacement; no TTL is
used because preview-slice output is deterministic for a given input.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import zipfile
from collections import OrderedDict
from io import BytesIO

import defusedxml.ElementTree as ET

from backend.app.services.slicer_api import (
    SlicerApiError,
    SlicerApiService,
)

logger = logging.getLogger(__name__)

_PREVIEW_CACHE_MAX = 256
# Cache key includes a bundle-context fingerprint (or "" when no bundle was
# supplied) so a "preview without profiles" result and a "preview with
# bundle X" result for the same file/plate occupy distinct entries instead
# of clobbering each other.
_PreviewCacheKey = tuple[str, int, int, str, str]
# Cache values: list[dict] on success, [] on parsed-but-empty (slicer
# returned a 3MF without filament data for this plate — caching the negative
# avoids burning 30s+ per modal open on a known-bad input).
_preview_cache: OrderedDict[_PreviewCacheKey, list[dict]] = OrderedDict()
# Per-key locks prevent N concurrent modal opens on the same (file, plate,
# bundle) from launching N redundant preview slices — only the first one
# runs, the rest wait and read from the cache. Locks are evicted alongside
# cache entries to keep the dict bounded; we do NOT cache transient sidecar
# failures (network errors etc.) so those retry naturally on next request.
_preview_locks: dict[_PreviewCacheKey, asyncio.Lock] = {}


def _content_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()[:16]


def _bundle_context_fingerprint(
    bundle_id: str | None,
    printer_name: str | None,
    process_name: str | None,
    filament_names: list[str] | None,
) -> str:
    """Derive a stable cache-key fragment for the bundle context. Empty
    string when no bundle is supplied — preserves cache compatibility with
    the no-bundle ("embedded settings") path so existing entries remain
    valid. SHA-256 prefix keeps the key short while collision-resistant
    enough for a 256-entry LRU.
    """
    if not (bundle_id and printer_name and process_name and filament_names):
        return ""
    parts = [bundle_id, printer_name, process_name, *filament_names]
    raw = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


async def get_preview_filaments(
    *,
    kind: str,
    source_id: int,
    plate_id: int,
    file_bytes: bytes,
    file_name: str,
    api_url: str,
    request_id: str | None = None,
    bundle_id: str | None = None,
    printer_name: str | None = None,
    process_name: str | None = None,
    filament_names: list[str] | None = None,
) -> list[dict] | None:
    """Run a preview slice for ``plate_id``, parse the resulting slice_info,
    and return the per-plate filament list.

    By default uses the file's embedded settings (``slice_without_profiles``).
    When all four ``bundle_*`` params are provided, uses ``slice_with_bundle``
    so the preview matches the profile triplet the real print will use —
    same slot mapping, more-accurate gram numbers. Partial bundle context
    (e.g. id without preset names) falls back to the embedded path rather
    than failing, so an in-progress modal selection doesn't surface errors.

    Returns ``None`` when the preview slice fails — the caller should fall
    back to whatever heuristic it has (typically the project_filaments +
    painted-face approach in ``threemf_tools``).
    """
    h = _content_hash(file_bytes)
    bundle_fp = _bundle_context_fingerprint(
        bundle_id,
        printer_name,
        process_name,
        filament_names,
    )
    key: _PreviewCacheKey = (kind, source_id, plate_id, h, bundle_fp)
    cached = _preview_cache.get(key)
    if cached is not None:
        _preview_cache.move_to_end(key)
        return cached

    lock = _preview_locks.setdefault(key, asyncio.Lock())
    async with lock:
        # Re-check after acquiring the lock — another coroutine may have
        # populated the cache while we were waiting on it.
        cached = _preview_cache.get(key)
        if cached is not None:
            _preview_cache.move_to_end(key)
            return cached

        try:
            async with SlicerApiService(base_url=api_url) as svc:
                if bundle_fp:
                    # All four bundle params present (guaranteed non-None by
                    # _bundle_context_fingerprint returning non-empty);
                    # the type-checker can't see that, so assert for narrowing.
                    assert bundle_id and printer_name and process_name
                    assert filament_names is not None
                    result = await svc.slice_with_bundle(
                        model_bytes=file_bytes,
                        model_filename=file_name,
                        bundle_id=bundle_id,
                        printer_name=printer_name,
                        process_name=process_name,
                        filament_names=filament_names,
                        plate=plate_id,
                        export_3mf=True,
                        request_id=request_id,
                    )
                else:
                    result = await svc.slice_without_profiles(
                        model_bytes=file_bytes,
                        model_filename=file_name,
                        plate=plate_id,
                        export_3mf=True,
                        request_id=request_id,
                    )
        except SlicerApiError as e:
            logger.warning(
                "Preview slice failed for %s/%s plate %s (bundle=%s): %s",
                kind,
                source_id,
                plate_id,
                bundle_id or "-",
                e,
            )
            return None
        except Exception as e:  # noqa: BLE001 — never break the modal on sidecar issues
            logger.warning("Preview slice unexpected error: %s", e)
            return None

        filaments = _parse_filaments_from_sliced_3mf(result.content, plate_id)
        # Negative-cache the parse failure: a slice that succeeds but yields
        # no parsable filament data for this plate is a deterministic
        # property of the input. Re-running the slice produces the same
        # result, just N seconds slower. Empty list signals "preview was
        # tried, no usable data" so the caller can fall through.
        cache_value: list[dict] = filaments if filaments is not None else []
        _preview_cache[key] = cache_value
        if len(_preview_cache) > _PREVIEW_CACHE_MAX:
            evicted_key, _ = _preview_cache.popitem(last=False)
            # Drop the matching lock so the dict doesn't grow forever.
            # Safe to discard: the lock isn't held here, and any later
            # request for the same key will mint a fresh lock.
            _preview_locks.pop(evicted_key, None)
        return filaments


def _parse_filaments_from_sliced_3mf(content: bytes, plate_id: int) -> list[dict] | None:
    """Extract ``<filament>`` entries for ``plate_id`` from a sliced 3MF's
    Metadata/slice_info.config. Returns ``None`` on any parse error so the
    caller knows to fall back."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            if "Metadata/slice_info.config" not in zf.namelist():
                return None
            data = zf.read("Metadata/slice_info.config").decode()
    except (zipfile.BadZipFile, OSError):
        return None

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    for plate_elem in root.findall(".//plate"):
        idx = None
        for meta in plate_elem.findall("metadata"):
            if meta.get("key") == "index":
                try:
                    idx = int(meta.get("value", ""))
                except (ValueError, TypeError):
                    pass
                break
        if idx != plate_id:
            continue
        out: list[dict] = []
        for f in plate_elem.findall("filament"):
            fid = f.get("id")
            if not fid:
                continue
            try:
                slot_id = int(fid)
            except (ValueError, TypeError):
                continue
            try:
                used_grams = float(f.get("used_g", "0"))
            except (ValueError, TypeError):
                used_grams = 0
            try:
                used_meters = float(f.get("used_m", "0"))
            except (ValueError, TypeError):
                used_meters = 0
            out.append(
                {
                    "slot_id": slot_id,
                    "type": f.get("type", ""),
                    "color": f.get("color", ""),
                    "used_grams": round(used_grams, 1),
                    "used_meters": used_meters,
                    "tray_info_idx": f.get("tray_info_idx", ""),
                },
            )
        return sorted(out, key=lambda x: x["slot_id"])
    return None
