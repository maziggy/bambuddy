"""Bambuddy administrative CLI.

Invoked via ``python -m backend.app.cli <subcommand>``.

Currently provides ``kiosk-bootstrap`` for creating the SpoolBuddy kiosk
API key during install (see ``spoolbuddy/install/install.sh``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.core.auth import generate_api_key
from backend.app.core.database import async_session as default_session_maker, init_db
from backend.app.core.db_dialect import upsert_setting
from backend.app.models.api_key import APIKey
from backend.app.models.settings import Settings

DEFAULT_KIOSK_KEY_NAME = "spoolbuddy-kiosk"


class KioskBootstrapError(RuntimeError):
    """Raised when an existing kiosk key would be silently overwritten."""


async def kiosk_bootstrap(
    name: str,
    *,
    force: bool,
    session_maker: async_sessionmaker | None = None,
    ensure_schema: bool = True,
) -> str:
    """Create (or rotate) an API key for the SpoolBuddy kiosk and return it.

    The returned value is the one-time full key string; callers are responsible
    for writing it somewhere secure — it cannot be retrieved again.
    """
    if ensure_schema and session_maker is None:
        await init_db()

    maker = session_maker or default_session_maker

    async with maker() as db:
        existing = (await db.execute(select(APIKey).where(APIKey.name == name))).scalar_one_or_none()

        if existing and not force:
            raise KioskBootstrapError(
                f"API key {name!r} already exists (prefix={existing.key_prefix}). Re-run with --force to rotate."
            )

        if existing:
            await db.delete(existing)
            await db.flush()

        full_key, key_hash, key_prefix = generate_api_key()
        row = APIKey(
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            can_queue=False,
            can_control_printer=False,
            can_read_status=True,
            printer_ids=None,
            enabled=True,
            expires_at=None,
        )
        db.add(row)

        # Mark first-run setup as completed so the kiosk URL loads directly
        # instead of being force-redirected to /setup by AuthContext. Without
        # this, a bundled SpoolBuddy/Bambuddy install boots into the Bambuddy
        # first-run wizard (touch-only Pi has no keyboard to complete it).
        # Users who want authentication enable it later from the admin UI; the
        # API key we just created is already valid so the kiosk keeps working.
        await upsert_setting(db, Settings, "setup_completed", "true")

        await db.commit()
        return full_key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.cli",
        description="Bambuddy administrative commands",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    kiosk = sub.add_parser(
        "kiosk-bootstrap",
        help="Create an API key for the SpoolBuddy kiosk",
        description=(
            "Create (or rotate with --force) an API key scoped for the SpoolBuddy "
            "kiosk. The full key is printed to stdout — capture it into "
            "spoolbuddy/.env as SPOOLBUDDY_API_KEY."
        ),
    )
    kiosk.add_argument(
        "--name",
        default=DEFAULT_KIOSK_KEY_NAME,
        help=f"Key name in the DB (default: {DEFAULT_KIOSK_KEY_NAME})",
    )
    kiosk.add_argument(
        "--force",
        action="store_true",
        help="Rotate an existing key with the same name (deletes the old one)",
    )

    args = parser.parse_args(argv)

    if args.command == "kiosk-bootstrap":
        try:
            key = asyncio.run(kiosk_bootstrap(args.name, force=args.force))
        except KioskBootstrapError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(key)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
