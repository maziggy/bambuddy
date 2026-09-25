"""Shared spine for the external filament-catalog clients (OFD, SpoolmanDB-Community).

Both clients do the same dance: download a community dataset, parse it into
lookup indexes, and keep the result in a versioned on-disk cache with a 24h
TTL, refreshed lazily on the next lookup once stale (this backend has no
scheduler/cron). This module owns that dance once — during PR #1895's review
the two independent copies repeatedly drifted ("client X has a guard client
Y lacks": the download size cap, the stale-cache fallback, the type guards
all had to be found and fixed twice). One hardened implementation, two thin
format adapters.

Guarantees provided here, for every client:

- **Bounded download**: streamed with a running byte cap, never an unbounded
  buffer of whatever upstream serves.
- **No event-loop stalls**: cache file reads/writes and the client's parse
  step run via ``asyncio.to_thread`` — a 24h refresh lands on some user's
  request, and that request shouldn't freeze every other one while a tarball
  is unpacked.
- **Atomic cache writes**: temp file + ``Path.replace()`` (POSIX rename(2)
  semantics, Windows-safe), so a killed process can't leave a half-written
  cache for the next boot to trip over.
- **Never cache empty**: a refresh that parses to zero entries raises inside
  the client's ``_download_and_build`` instead of clobbering a good cache
  with nothing and silently answering "no match" for a full TTL.
- **Stale beats nothing**: when a refresh fails (offline, upstream down), a
  stale-but-working disk cache is served instead of dropping to "no match";
  only a truly empty disk *and* missing seed re-raises.
- **Seed fallback**: an optional snapshot baked into the Docker image at
  build time (see ``backend/scripts/seed_spoolmandb_community_cache.py``).
  A fresh air-gapped install, which can never fill its cache from the
  network, falls back to the seed — frozen at image-build time, but far
  better than zero coverage. Using the seed persists it into DATA_DIR so
  later boots treat it as a normal stale cache.

Lookup order end-to-end: in-memory (TTL) → fresh disk cache → network
refresh → stale disk cache → baked-in seed → raise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from backend.app.core.paths import resolve_data_dir

logger = logging.getLogger(__name__)


def canon(barcode: str) -> str:
    """Canonical GTIN form for matching: digits only, leading zeros stripped.

    Makes a UPC-A (12-digit) barcode and its EAN-13 (leading-zero) form
    compare equal. Mirrors ``normalize_barcode`` in ``schemas/spool.py``'s
    numeric branch — duplicated rather than imported so the services layer
    doesn't reach into the schemas layer.
    """
    digits = re.sub(r"\D", "", barcode or "")
    return digits.lstrip("0") or "0"


def hex_to_rgba(color_hex) -> str | None:
    """Accept a hex string or a list (multi-color hexes) and return RRGGBBAA."""
    if isinstance(color_hex, list):
        color_hex = color_hex[0] if color_hex else None
    if not isinstance(color_hex, str):
        return None
    h = color_hex.lstrip("#")
    if len(h) == 6 and re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        return h.upper() + "FF"  # RRGGBBAA, opaque
    return None


async def stream_download(url: str, *, max_bytes: int, timeout: float, follow_redirects: bool = False) -> bytes:
    """Download ``url`` streamed with a running size cap.

    Never ``client.get(...).json()`` — that buffers the whole response with
    no ceiling, so a large or slow upstream response (malicious or just
    broken) could OOM the backend on the 24h auto-refresh.
    """
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
        chunks = bytearray()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > max_bytes:
                    raise ValueError(f"{url} exceeded {max_bytes} byte cap - aborting download")
        return bytes(chunks)


class CachedCatalogClient:
    """One external catalog: subclasses configure identity + parsing.

    Subclasses set ``name``, ``cache_filename``, ``ttl_seconds``,
    ``cache_version``, optionally ``seed_path()``, and implement
    ``_download_and_build() -> dict`` returning the client-specific payload
    (raising on empty results). Everything else — caching, TTL, locking,
    fallbacks — lives here.
    """

    name: str = "external-catalog"
    cache_filename: str = ""
    ttl_seconds: int = 24 * 3600
    cache_version: int = 1

    def __init__(self) -> None:
        self._payload: dict[str, Any] | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    # -- hooks ---------------------------------------------------------------

    async def _download_and_build(self) -> dict[str, Any]:
        """Fetch + parse the upstream dataset into the cacheable payload dict.

        Must raise (not return an empty payload) when the result contains no
        entries, so the never-cache-empty guarantee holds.
        """
        raise NotImplementedError

    def seed_path(self) -> Path | None:
        """Optional build-time snapshot baked into the image; None = no seed."""
        return None

    # -- disk cache ----------------------------------------------------------

    def _cache_path(self) -> Path:
        return resolve_data_dir() / self.cache_filename

    def _read_file_sync(self, path: Path) -> dict[str, Any] | None:
        """Parse a cache/seed file, checking its version but ignoring TTL —
        missing/corrupt/wrong-version are the only conditions that make a
        file truly unusable; staleness alone does not."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if data.get("cache_version") != self.cache_version:
                return None
            if not isinstance(data.get("payload"), dict):
                return None
            return data
        except Exception:
            return None

    def _write_file_sync(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Temp file + Path.replace(): atomic on POSIX (rename(2)) and
        # Windows-safe (replaces an existing destination), so a reader never
        # observes a half-written cache even if the process dies mid-write.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data))
        tmp_path.replace(path)

    async def _read_cache(self, *, ignore_ttl: bool) -> dict[str, Any] | None:
        data = await asyncio.to_thread(self._read_file_sync, self._cache_path())
        if data is None:
            return None
        if not ignore_ttl and time.time() - data.get("built_at", 0) > self.ttl_seconds:
            return None
        return data["payload"]

    async def _write_cache(self, payload: dict[str, Any], *, built_at: float | None = None) -> None:
        data = {
            "cache_version": self.cache_version,
            "built_at": time.time() if built_at is None else built_at,
            "payload": payload,
        }
        try:
            await asyncio.to_thread(self._write_file_sync, self._cache_path(), data)
        except Exception:
            logger.warning("Failed to write %s cache file", self.name, exc_info=True)

    async def _load_seed(self) -> dict[str, Any] | None:
        """Last-resort rung: the snapshot baked into the image at build time.

        On use, persist it into DATA_DIR (keeping the seed's own built_at, so
        it reads as stale) — later boots then treat it as a normal stale
        cache and keep trying to refresh past it.
        """
        path = self.seed_path()
        if path is None:
            return None
        data = await asyncio.to_thread(self._read_file_sync, path)
        if data is None:
            return None
        logger.warning("%s: no cache and refresh failed; falling back to baked-in seed %s", self.name, path)
        await self._write_cache(data["payload"], built_at=data.get("built_at", 0))
        return data["payload"]

    # -- load orchestration ----------------------------------------------------

    async def ensure_loaded(self) -> None:
        if self._payload is not None and (time.time() - self._loaded_at) < self.ttl_seconds:
            return
        async with self._lock:
            # Re-check after acquiring the lock — another request may have
            # already refreshed while we were waiting.
            if self._payload is not None and (time.time() - self._loaded_at) < self.ttl_seconds:
                return
            loaded = await self._read_cache(ignore_ttl=False)
            if loaded is None:
                try:
                    loaded = await self._download_and_build()
                    await self._write_cache(loaded)
                except Exception:
                    # Offline/upstream-down: a stale-but-working index beats
                    # no index at all, and the baked-in seed beats truly
                    # nothing. Only re-raise when every rung is empty — the
                    # one case where the caller must know the lookup couldn't
                    # be attempted at all.
                    loaded = await self._read_cache(ignore_ttl=True)
                    if loaded is None:
                        loaded = await self._load_seed()
                    if loaded is None:
                        raise
                    logger.warning("%s refresh failed; serving stale cache", self.name, exc_info=True)
            self._payload = loaded
            self._loaded_at = time.time()

    async def payload(self) -> dict[str, Any]:
        """The loaded payload (memory → disk cache → download → stale → seed)."""
        await self.ensure_loaded()
        return self._payload or {}
