#!/usr/bin/env python3
"""Update the created_at date for a specific archive."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text

from backend.app.core.config import settings


def update_archive_date(archive_id: int, new_date: datetime) -> bool:
    """Update created_at for an archive."""
    db_path = settings.base_dir / "bambuddy.db"
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        # Check if archive exists
        result = conn.execute(
            text("SELECT id, filename, created_at FROM print_archives WHERE id = :id"),
            {"id": archive_id},
        )
        row = result.fetchone()

        if not row:
            print(f"Archive ID {archive_id} not found!")
            return False

        print(f"Archive: {row[1]}")
        print(f"Current date: {row[2]}")
        print(f"New date: {new_date}")

        # Update
        conn.execute(
            text("UPDATE print_archives SET created_at = :date WHERE id = :id"),
            {"id": archive_id, "date": new_date},
        )
        conn.commit()
        print("✓ Updated successfully!")
        return True


def main():
    parser = argparse.ArgumentParser(description="Update archive created_at date")
    parser.add_argument("archive_id", type=int, help="Archive ID to update")
    parser.add_argument(
        "date",
        type=str,
        help="New date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
    )

    args = parser.parse_args()

    # Parse date
    try:
        if " " in args.date:
            new_date = datetime.strptime(args.date, "%Y-%m-%d %H:%M:%S")
        else:
            new_date = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("Invalid date format. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
        sys.exit(1)

    update_archive_date(args.archive_id, new_date)


if __name__ == "__main__":
    main()
