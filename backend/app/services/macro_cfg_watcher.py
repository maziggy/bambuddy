"""Startup scanner and on-save sync for .cfg macro files.

Responsibilities:
  - On startup: scan macros_dir for all *.cfg files, upsert MacroCfgFile rows,
    upsert/orphan Macro rows to match what's parsed.
  - After a PUT /cfg-files/{id} save: re-parse that one file and sync its macros.

No filesystem watcher daemon runs — sync is triggered explicitly on save.
The scan is idempotent and can be re-run at any time.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session
from backend.app.models.macro import Macro, MacroCfgFile
from backend.app.services.macro_cfg_parser import parse

logger = logging.getLogger(__name__)


def _macros_dir() -> Path:
    d = Path(app_settings.macros_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def scan_all() -> None:
    """Scan the macros directory and sync every .cfg file into the DB."""
    d = _macros_dir()
    cfg_files = sorted(d.glob("*.cfg"))
    logger.info("Macro cfg scan: found %d .cfg files in %s", len(cfg_files), d)
    for path in cfg_files:
        try:
            await sync_file(path.relative_to(d).as_posix(), stem=path.stem)
        except Exception:
            logger.exception("Failed to sync macro cfg file: %s", path)


async def sync_file(relative_path: str, stem: str | None = None) -> MacroCfgFile:
    """Parse one .cfg file and sync its macros into the DB.

    relative_path — path relative to macros_dir, e.g. 'my_macros.cfg'
    stem          — optional display name override; defaults to filename stem

    Returns the upserted MacroCfgFile row.
    """
    d = _macros_dir()
    full_path = d / relative_path
    display_name = stem or Path(relative_path).stem

    text = full_path.read_text(encoding="utf-8") if full_path.exists() else ""
    parse_result = parse(text)

    file_level_error: str | None = "; ".join(parse_result.errors) if parse_result.errors else None

    async with async_session() as db:
        # Upsert MacroCfgFile row
        result = await db.execute(select(MacroCfgFile).where(MacroCfgFile.file_path == relative_path))
        cfg_file = result.scalar_one_or_none()
        if cfg_file is None:
            cfg_file = MacroCfgFile(name=display_name, file_path=relative_path)
            db.add(cfg_file)
            await db.flush()
        else:
            cfg_file.name = display_name
        cfg_file.parse_error = file_level_error

        # Load existing macros for this file
        existing_result = await db.execute(select(Macro).where(Macro.cfg_file_id == cfg_file.id))
        existing: dict[str, Macro] = {m.name: m for m in existing_result.scalars()}

        # Names present in this parse pass
        parsed_names: set[str] = set()

        for pm in parse_result.macros:
            parsed_names.add(pm.name)
            if pm.error:
                # Block exists but is broken — mark as error, don't overwrite description
                if pm.name in existing:
                    existing[pm.name].status = "error"
                else:
                    broken = Macro(
                        name=pm.name,
                        cfg_file_id=cfg_file.id,
                        status="error",
                    )
                    db.add(broken)
                continue

            if pm.name in existing:
                macro = existing[pm.name]
                if macro.status == "orphaned":
                    # Block came back — reactivate. Preserve trigger/cron/printer settings.
                    logger.info("Macro '%s' reactivated in %s", pm.name, relative_path)
                macro.status = "active"
                macro.description = pm.description
                macro.cfg_file_id = cfg_file.id
            else:
                # Check if a macro with this name exists in ANOTHER file (cross-file collision)
                collision_result = await db.execute(select(Macro).where(Macro.name == pm.name))
                collision = collision_result.scalar_one_or_none()
                if collision is not None:
                    logger.warning(
                        "Macro name '%s' in %s conflicts with existing macro from cfg_file_id=%s — skipping",
                        pm.name,
                        relative_path,
                        collision.cfg_file_id,
                    )
                    file_level_error = (file_level_error or "") + (
                        f"; Name conflict: '{pm.name}' already defined in another file"
                    )
                    cfg_file.parse_error = file_level_error
                    continue

                new_macro = Macro(
                    name=pm.name,
                    description=pm.description,
                    cfg_file_id=cfg_file.id,
                    status="active",
                )
                db.add(new_macro)

        # Orphan any macros that were in this file but are no longer present
        for name, macro in existing.items():
            if name not in parsed_names and macro.status != "orphaned":
                logger.info("Macro '%s' orphaned (removed from %s)", name, relative_path)
                macro.status = "orphaned"

        await db.commit()
        await db.refresh(cfg_file)
        return cfg_file


async def delete_file_from_db(relative_path: str) -> None:
    """Mark all macros in a deleted/missing cfg file as orphaned and remove the file row."""
    async with async_session() as db:
        result = await db.execute(select(MacroCfgFile).where(MacroCfgFile.file_path == relative_path))
        cfg_file = result.scalar_one_or_none()
        if cfg_file is None:
            return
        # Orphan all its macros before cascade-delete removes them
        macros_result = await db.execute(select(Macro).where(Macro.cfg_file_id == cfg_file.id))
        for macro in macros_result.scalars():
            macro.status = "orphaned"
            macro.cfg_file_id = None  # detach so they survive the file row deletion
        await db.flush()
        await db.delete(cfg_file)
        await db.commit()
