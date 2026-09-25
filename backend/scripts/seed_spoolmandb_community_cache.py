"""Bake a SpoolmanDB-Community snapshot into the Docker image at build time.

Bambuddy is frequently self-hosted and sometimes fully air-gapped. The
SpoolmanDB-Community client fills its cache from the network at runtime, so
a fresh offline install would otherwise have zero external barcode/catalog
coverage forever. This script runs during ``docker build`` (see the
Dockerfile layer just before ``COPY backend/``) and writes a snapshot the
client uses as its last-resort fallback rung: live cache → network refresh →
stale cache → **this seed** → give up.

It reuses the client's own download/parse/build path
(``download_and_build_payload``), so the seed always has the exact shape and
version semantics of a real cache payload. SpoolmanDB-Community is
MIT-licensed, so redistributing the snapshot inside the image is clean.

A build-time network hiccup must not fail the official image build: any
failure logs and exits 0, producing an image without a seed — identical to
pre-seed behavior, and strictly worse only for air-gapped installs. The seed
file is generated, never committed (see .gitignore).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from backend.app.services.spoolmandb_community_client import (
    SEED_CACHE_VERSION,
    _SpoolmanDbCommunityClient,
    download_and_build_payload,
)


async def _main() -> int:
    seed_path = _SpoolmanDbCommunityClient().seed_path()
    try:
        payload = await download_and_build_payload()
    except Exception as exc:  # noqa: BLE001 - a seed is best-effort by design
        print(f"SpoolmanDB-Community seed skipped (download/parse failed): {exc}", file=sys.stderr)
        return 0

    seed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = seed_path.with_suffix(seed_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"cache_version": SEED_CACHE_VERSION, "built_at": time.time(), "payload": payload}))
    tmp_path.replace(seed_path)

    gtins = len(payload.get("gtin_index", {}))
    skus = len(payload.get("sku_index", {}))
    brands = len({v.get("manufacturer") for v in payload.get("variants", []) if v.get("manufacturer")})
    size_mb = Path(seed_path).stat().st_size / (1024 * 1024)
    print(f"Wrote SpoolmanDB-Community seed: {gtins} GTINs, {skus} SKUs, {brands} brands ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
