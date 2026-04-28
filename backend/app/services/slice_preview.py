"""Preview-slice cache for the SliceModal.

The slice modal needs the per-plate filament list before the user picks
profiles. For sliced files this lives in ``Metadata/slice_info.config`` and
the ``/filament-requirements`` endpoint can read it directly. For unsliced
project files it doesn't exist yet — only the slicer can produce it, since
Bambu Studio applies its own pruning to painted-face data at slice time.

This module wraps the sidecar's ``slice_without_profiles`` call so the
endpoint can run a preview slice with the project's embedded settings,
parse the result's slice_info, and return the actual filament list. Results
are cached by ``(kind, source_id, plate_id, content_hash)`` so repeat
opens of the modal on the same plate are instant; LRU eviction keeps the
cache bounded. Hash invalidation handles in-place file replacement; no TTL
is used because preview-slice output is deterministic for a given file
content.
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
# Cache values: list[dict] on success, [] on parsed-but-empty (slicer
# returned a 3MF without filament data for this plate — caching the negative
# avoids burning 30s+ per modal open on a known-bad input).
_preview_cache: OrderedDict[tuple[str, int, int, str], list[dict]] = OrderedDict()
# Per-key locks prevent N concurrent modal opens on the same (file, plate)
# from launching N redundant preview slices — only the first one runs, the
# rest wait and read from the cache. Locks are evicted alongside cache
# entries to keep the dict bounded; we do NOT cache transient sidecar
# failures (network errors etc.) so those retry naturally on next request.
_preview_locks: dict[tuple[str, int, int, str], asyncio.Lock] = {}


def _content_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()[:16]


async def get_preview_filaments(
    *,
    kind: str,
    source_id: int,
    plate_id: int,
    file_bytes: bytes,
    file_name: str,
    api_url: str,
    request_id: str | None = None,
) -> list[dict] | None:
    """Run a preview slice for ``plate_id`` using the file's embedded settings,
    parse the resulting slice_info, and return the per-plate filament list.

    Returns ``None`` when the preview slice fails — the caller should fall
    back to whatever heuristic it has (typically the project_filaments +
    painted-face approach in ``threemf_tools``).
    """
    h = _content_hash(file_bytes)
    key = (kind, source_id, plate_id, h)
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
                result = await svc.slice_without_profiles(
                    model_bytes=file_bytes,
                    model_filename=file_name,
                    plate=plate_id,
                    export_3mf=True,
                    request_id=request_id,
                )
        except SlicerApiError as e:
            logger.warning(
                "Preview slice failed for %s/%s plate %s: %s",
                kind,
                source_id,
                plate_id,
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
