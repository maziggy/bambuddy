#!/usr/bin/env python3
"""Debug script to investigate Bambu Cloud preset API responses."""

import asyncio
import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy import create_engine, text

from backend.app.core.config import settings

# Test preset IDs (from the warning logs)
TEST_IDS = ["GFG02", "GFL05", "GFA00", "GFA02", "GFA06"]


def get_token_from_db() -> str | None:
    """Get the stored token from the database."""
    db_path = settings.base_dir / "bambuddy.db"
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        result = conn.execute(text("SELECT value FROM settings WHERE key = 'bambu_cloud_token'"))
        row = result.fetchone()

        if row and row[0]:
            return row[0]
    return None


async def test_preset(setting_id: str, token: str, base_url: str = "https://api.bambulab.com"):
    """Test fetching a single preset and show full response."""
    url = f"{base_url}/v1/iot-service/api/slicer/setting/{setting_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    print(f"\n{'=' * 60}")
    print(f"Testing preset: {setting_id}")
    print(f"URL: {url}")
    print(f"{'=' * 60}")

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)

        print(f"Status: {response.status_code}")
        print("\nResponse body:")
        try:
            data = response.json()
            print(json.dumps(data, indent=2))
        except Exception:
            print(response.text)

    return response.status_code


async def main():
    # Get token from DB
    token = get_token_from_db()

    if not token:
        print("Could not find token in database.")
        print("Make sure you're logged into Bambu Cloud in Bambuddy.")
        sys.exit(1)

    print(f"Found token in database (length: {len(token)})")

    # Allow testing specific preset IDs from command line
    test_ids = sys.argv[1:] if len(sys.argv) > 1 else TEST_IDS

    # Test each preset
    for preset_id in test_ids:
        await test_preset(preset_id, token)


if __name__ == "__main__":
    asyncio.run(main())
