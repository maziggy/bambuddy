#!/usr/bin/env python3
"""Import spools from Spoolman into Bambuddy inventory.

Usage:
    python scripts/import_spoolman.py --spoolman-url http://localhost:7912 --bambuddy-url http://localhost:8000
    python scripts/import_spoolman.py --spoolman-url http://localhost:7912 --bambuddy-url http://localhost:8000 --api-key YOUR_KEY
    python scripts/import_spoolman.py --spoolman-url http://localhost:7912 --bambuddy-url http://localhost:8000 --dry-run
"""

import argparse
import sys

import requests


def fetch_spoolman_spools(spoolman_url: str) -> list[dict]:
    """Fetch all spools from Spoolman API."""
    url = f"{spoolman_url.rstrip('/')}/api/v1/spool"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def map_spool(sm_spool: dict) -> dict:
    """Map a Spoolman spool to a Bambuddy SpoolCreate payload."""
    filament = sm_spool.get("filament") or {}
    vendor = filament.get("vendor") or {}

    material = filament.get("material") or "PLA"
    color_hex = filament.get("color_hex") or ""
    # Spoolman color_hex is 6-char (#RRGGBB or RRGGBB), Bambuddy rgba is 8-char RRGGBBAA
    rgba = None
    if color_hex:
        color_hex = color_hex.lstrip("#")
        if len(color_hex) == 6:
            rgba = f"{color_hex}FF"
        elif len(color_hex) == 8:
            rgba = color_hex

    label_weight = int(filament.get("weight") or 1000)
    used_weight = float(sm_spool.get("used_weight") or 0)

    # Filament name from Spoolman (e.g. "eSun PLA+ Black")
    filament_name = filament.get("name") or ""
    # Vendor name (e.g. "eSun", "Bambu Lab")
    brand = vendor.get("name")

    # Color name - prefer filament color name if present
    color_name = filament.get("color_hex_name") or None

    # Cost: Spoolman stores price per spool, we need cost per kg
    cost_per_kg = None
    spool_price = sm_spool.get("price") or filament.get("price")
    if spool_price and label_weight > 0:
        cost_per_kg = round(float(spool_price) / (label_weight / 1000), 2)

    # Temperature range from filament settings
    nozzle_temp_min = filament.get("settings", {}).get("nozzle_temperature_min") if filament.get("settings") else None
    nozzle_temp_max = filament.get("settings", {}).get("nozzle_temperature_max") if filament.get("settings") else None

    # Extra fields
    extra = sm_spool.get("extra") or {}
    tag_uid = extra.get("tag") or None

    # Build note with Spoolman reference
    note_parts = []
    if sm_spool.get("comment"):
        note_parts.append(sm_spool["comment"])
    if sm_spool.get("lot_nr"):
        note_parts.append(f"Lot: {sm_spool['lot_nr']}")
    note_parts.append(f"Imported from Spoolman (ID: {sm_spool['id']})")
    note = " | ".join(note_parts)

    payload = {
        "material": material,
        "color_name": color_name,
        "rgba": rgba.upper() if rgba else None,
        "brand": brand,
        "label_weight": label_weight,
        "weight_used": used_weight,
        "note": note,
        "cost_per_kg": cost_per_kg,
        "tag_uid": tag_uid,
        "data_origin": "spoolman",
    }

    if filament_name:
        payload["subtype"] = filament_name

    if nozzle_temp_min is not None:
        payload["nozzle_temp_min"] = int(nozzle_temp_min)
    if nozzle_temp_max is not None:
        payload["nozzle_temp_max"] = int(nozzle_temp_max)

    return payload


def create_bambuddy_spool(bambuddy_url: str, spool_data: dict, api_key: str | None = None) -> dict:
    """Create a spool in Bambuddy inventory."""
    url = f"{bambuddy_url.rstrip('/')}/api/v1/inventory/spools"
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    resp = requests.post(url, json=spool_data, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Import spools from Spoolman into Bambuddy inventory")
    parser.add_argument("--spoolman-url", required=True, help="Spoolman URL (e.g. http://localhost:7912)")
    parser.add_argument("--bambuddy-url", required=True, help="Bambuddy URL (e.g. http://localhost:8000)")
    parser.add_argument("--api-key", help="Bambuddy API key (required if auth is enabled)")
    parser.add_argument("--dry-run", action="store_true", help="Print mapped spools without importing")
    parser.add_argument("--archived", action="store_true", help="Include archived Spoolman spools")
    args = parser.parse_args()

    print(f"Fetching spools from {args.spoolman_url}...")
    try:
        sm_spools = fetch_spoolman_spools(args.spoolman_url)
    except requests.RequestException as e:
        print(f"Error fetching from Spoolman: {e}", file=sys.stderr)
        sys.exit(1)

    if not args.archived:
        sm_spools = [s for s in sm_spools if not s.get("archived")]

    print(f"Found {len(sm_spools)} spools in Spoolman")

    if not sm_spools:
        print("Nothing to import.")
        return

    created = 0
    failed = 0

    for sm_spool in sm_spools:
        filament = sm_spool.get("filament") or {}
        vendor = (filament.get("vendor") or {}).get("name", "?")
        name = filament.get("name") or filament.get("material") or "Unknown"
        label = f"#{sm_spool['id']} {vendor} {name}"

        payload = map_spool(sm_spool)

        if args.dry_run:
            print(f"  [DRY RUN] {label}")
            for k, v in payload.items():
                if v is not None:
                    print(f"    {k}: {v}")
            print()
            continue

        try:
            result = create_bambuddy_spool(args.bambuddy_url, payload, args.api_key)
            print(f"  Imported {label} -> Bambuddy spool #{result['id']}")
            created += 1
        except requests.RequestException as e:
            print(f"  FAILED {label}: {e}", file=sys.stderr)
            failed += 1

    if not args.dry_run:
        print(f"\nDone: {created} imported, {failed} failed")


if __name__ == "__main__":
    main()
