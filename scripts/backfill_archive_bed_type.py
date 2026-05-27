#!/usr/bin/env python3
"""Backfill bed_type on existing archives from their 3MF files.

Newly-ingested archives capture curr_bed_type at parse time. Archives created
before this column existed have bed_type=NULL — this script re-opens each
archive's 3MF on disk and populates bed_type from slice_info.config (and
project_settings.config as a fallback).

Usage:
    # From the bambuddy directory:
    python scripts/backfill_archive_bed_type.py

    # Or via docker:
    docker exec -it bambuddy python scripts/backfill_archive_bed_type.py

    # Preview without writing:
    python scripts/backfill_archive_bed_type.py --dry-run
"""

import argparse
import asyncio
import json
import os
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_dotenv() -> Path | None:
    """Populate os.environ from project-root .env so the script picks up the
    same DATABASE_URL / DATA_DIR the backend uses, regardless of how the shell
    was launched. Backend's config.py reads DATABASE_URL at import time, so we
    must do this BEFORE importing anything from backend.app."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Don't override values already set in the shell (matches docker / systemd
        # convention: explicit env wins over .env file).
        os.environ.setdefault(key, value)
    return env_file


_loaded_env = _load_dotenv()

from sqlalchemy import select  # noqa: E402

from backend.app.core.config import settings  # noqa: E402
from backend.app.core.database import async_session, init_db  # noqa: E402
from backend.app.models.archive import PrintArchive  # noqa: E402


def _describe_db() -> str:
    """Redact credentials from a DATABASE_URL for safe display."""
    url = settings.database_url
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


def extract_bed_type(file_path: Path) -> str | None:
    """Pull curr_bed_type from a 3MF file. Returns the raw slicer string."""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            names = zf.namelist()

            # Primary source: slice_info.config (XML)
            if "Metadata/slice_info.config" in names:
                try:
                    root = ET.fromstring(zf.read("Metadata/slice_info.config").decode())
                    plate = root.find(".//plate")
                    if plate is not None:
                        for meta in plate.findall("metadata"):
                            if meta.get("key") == "curr_bed_type":
                                value = meta.get("value")
                                if value:
                                    return value.strip()
                except ET.ParseError:
                    pass

            # Fallback: project_settings.config (JSON)
            if "Metadata/project_settings.config" in names:
                try:
                    data = json.loads(zf.read("Metadata/project_settings.config").decode())
                    val = data.get("curr_bed_type")
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                except json.JSONDecodeError:
                    pass
    except (zipfile.BadZipFile, OSError):
        return None
    return None


async def backfill(dry_run: bool = False):
    print("=" * 60)
    print("Archive bed_type backfill")
    print("=" * 60)
    print(f"Database: {_describe_db()}")
    print(f".env loaded: {_loaded_env}" if _loaded_env else ".env: not found (using shell env only)")
    print()
    if dry_run:
        print("DRY RUN MODE - No changes will be written")
        print()

    # Ensure the bed_type column exists. Safe to run against a live DB —
    # init_db() is idempotent and is what the backend runs at every startup.
    await init_db()

    async with async_session() as db:
        result = await db.execute(select(PrintArchive).where(PrintArchive.bed_type.is_(None)))
        archives = result.scalars().all()
        print(f"Found {len(archives)} archives with bed_type=NULL")
        print()

        updated = 0
        skipped_missing = 0
        skipped_no_value = 0

        for archive in archives:
            if not archive.file_path:
                skipped_missing += 1
                continue
            file_path = settings.base_dir / archive.file_path
            if not file_path.exists():
                skipped_missing += 1
                continue

            bed_type = extract_bed_type(file_path)
            if not bed_type:
                skipped_no_value += 1
                continue

            print(f"  [{archive.id}] {archive.print_name or archive.filename}: -> {bed_type}")
            if not dry_run:
                archive.bed_type = bed_type
            updated += 1

        if not dry_run:
            await db.commit()

        print()
        print("-" * 60)
        print(f"Updated: {updated}")
        print(f"Skipped (file missing): {skipped_missing}")
        print(f"Skipped (no bed_type in 3MF): {skipped_no_value}")
        if dry_run and updated:
            print()
            print("Run without --dry-run to apply.")


def main():
    parser = argparse.ArgumentParser(description="Backfill bed_type on existing archives")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
