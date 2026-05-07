#!/usr/bin/env python3
"""Seed Bambuddy with a quick visual set of filament effect spools.

Usage:
    python scripts/fill_spool_effects.py --bambuddy-url http://localhost:8000
    python scripts/fill_spool_effects.py --bambuddy-url http://localhost:8000 --api-key YOUR_KEY

This script creates stock spools for every effect type defined in the `SPOOLS` list below,
using the bulk endpoint for creation:
    POST /api/v1/inventory/spools/bulk
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import requests


API_PATH_BULK_CREATE = "/api/v1/inventory/spools/bulk"

@dataclass(frozen=True)
class TestSpool:
    "Class representing a spool definition and count for the test spool set"
    effect_type: str
    colors: dict[str, str]
    quantity: int = 1


SPOOLS: list[TestSpool] = [
    TestSpool(
        effect_type="sparkle",
        colors={"dodger blue": "1E90FFFF"},
        quantity=2,
    ),
    TestSpool(
        effect_type="sparkle",
        colors={"dark red": "8B0000FF"},
    ),
    TestSpool(
        effect_type="wood",
        colors={"brown": "A47251FF"},
    ),
    TestSpool(
        effect_type="marble",
        colors={"slate": "4F5D75FF"},
    ),
    TestSpool(
        effect_type="glow",
        colors={"mint-pop": "6CD4BCFF"},
    ),
    TestSpool(
        effect_type="matte",
        colors={"charcoal": "2B2D42FF"},
    ),
    TestSpool(
        effect_type="silk",
        colors={"rose": "FF8FA3FF"},
    ),
    TestSpool(
        effect_type="galaxy",
        colors={"indigo": "4361EEFF"},
    ),
    TestSpool(
        effect_type="rainbow",
        colors={"amber": "FFBF69FF"},
    ),
    TestSpool(
        effect_type="metal",
        colors={"petrol-blue": "2D9CDBFF"},
    ),
    TestSpool(
        effect_type="translucent",
        colors={"cream": "FFF3E2AA"},
    ),
    TestSpool(
        effect_type="gradient",
        colors={
            "dark olive green": "556B2FFF",
            "goldenrod": "DAA520FF",
        },
    ),
    TestSpool(
        effect_type="dual-color",
        colors={
            "plum": "7B2CBFFF",
            "saffron": "F2C94CFF",
        },
    ),
    TestSpool(
        effect_type="tri-color",
        colors={
            "coral": "FF6B6BFF",
            "seafoam": "80ED99FF",
            "indigo": "4361EEFF",
        },
    ),
    TestSpool(
        effect_type="multicolor",
        colors={
            "sunset-orange": "EC984CFF",
            "mint-pop": "6CD4BCFF",
            "violet-bloom": "A66EB9FF",
            "raspberry-dawn": "D87694FF",
        },
    ),
    TestSpool(
        effect_type="multicolor",
        colors={
            "deep-navy": "1B1F3BFF",
            "cream": "FFF3E2FF",
            "rose": "FF8FA3FF",
            "teal": "2EC4B6FF",
            "amber": "FFBF69FF",
        },
    ),
]


def build_spool_payload(variant: TestSpool) -> dict:
    "Function to build the payload for a single spool variant"
    color_values = list(variant.colors.values())
    color_names = list(variant.colors.keys())
    return {
        "material": "PLA",
        "subtype": variant.effect_type,
        "brand": "Generic",
        "color_name": ", ".join(color_names),
        "rgba": color_values[0],
        "extra_colors": ",".join(color_values),
        "effect_type": variant.effect_type,
        "label_weight": 1000,
        "core_weight": 250,
        "weight_used": 0,
        "core_weight_catalog_id": None,
        "slicer_filament": None,
        "slicer_filament_name": None,
        "nozzle_temp_min": None,
        "nozzle_temp_max": None,
        "note": "Dev effect overview seed",
        "cost_per_kg": None,
        "category": None,
        "low_stock_threshold_pct": None,
    }


def create_bulk_spools(
    bambuddy_url: str,
    spool_data: dict,
    quantity: int,
    api_key: str | None,
    timeout: int,
) -> list[dict]:
    "Function that creates multiple spools using the bulk API endpoint and returns the list of created spools"
    url = f"{bambuddy_url.rstrip('/')}{API_PATH_BULK_CREATE}"
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key
    payload = {"spool": spool_data, "quantity": quantity}
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Bulk endpoint returned unexpected response format: expected a list of created spools")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Create development stock spools for every effect type")
    parser.add_argument("--bambuddy-url", required=True, help="Bambuddy URL (e.g. http://localhost:8000)")
    parser.add_argument("--api-key", help="Bambuddy API key (required if auth is enabled)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds (default: 30)")
    args = parser.parse_args()

    created = 0
    failed = 0

    for variant in SPOOLS:
        payload = build_spool_payload(variant)
        try:
            created_spools = create_bulk_spools(
                args.bambuddy_url,
                payload,
                quantity=variant.quantity,
                api_key=args.api_key,
                timeout=args.timeout,
            )
            ids = [str(item.get("id", "?")) for item in created_spools]
            print(
                f"  Created effect={variant.effect_type:<11} qty={len(created_spools)} "
                f"ids={','.join(ids)}"
            )
            created += len(created_spools)
        except (requests.RequestException, ValueError) as exc:
            print(f"  FAILED effect={variant.effect_type}: {exc}", file=sys.stderr)
            failed += variant.quantity

    print(f"\nDone: {created} created, {failed} failed")


if __name__ == "__main__":
    main()
