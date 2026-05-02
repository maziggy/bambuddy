"""Startup scanner and on-save sync for .cfg macro files.

Responsibilities:
  - On startup: scan macros_dir for all *.cfg files, upsert MacroCfgFile rows,
    upsert/delete Macro rows to match what's parsed.
  - After a PUT /cfg-files/{id} save: re-parse that one file and sync its macros.

No filesystem watcher daemon runs — sync is triggered explicitly on save.
The scan is idempotent and can be re-run at any time.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, select

from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session
from backend.app.models.macro import Macro, MacroCfgFile
from backend.app.services.macro_cfg_parser import parse

logger = logging.getLogger(__name__)


def _macros_dir() -> Path:
    d = Path(app_settings.macros_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _resolve_printer_id(db, printer_name: str | None) -> int | None:
    """Look up a printer by name; return its id or None."""
    if not printer_name:
        return None
    from backend.app.models.printer import Printer

    result = await db.execute(select(Printer).where(Printer.name == printer_name))
    printer = result.scalar_one_or_none()
    if printer is None:
        logger.warning("Macro cfg: printer '%s' not found, leaving printer_id unset", printer_name)
    return printer.id if printer else None


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

    Macros present in the file are upserted (with trigger, cron, printer from config lines).
    Macros removed from the file are deleted outright.

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

        parsed_names: set[str] = set()

        for pm in parse_result.macros:
            parsed_names.add(pm.name)
            if pm.error:
                if pm.name in existing:
                    pass  # leave existing row unchanged, just keep it
                else:
                    db.add(
                        Macro(
                            name=pm.name,
                            cfg_file_id=cfg_file.id,
                            trigger_type="manual",
                        )
                    )
                continue

            printer_id = await _resolve_printer_id(db, pm.printer_name)

            if pm.name in existing:
                macro = existing[pm.name]
                macro.description = pm.description
                macro.trigger_type = pm.trigger_type
                macro.cron_expression = pm.cron_expression
                macro.printer_id = printer_id
            else:
                # Check for cross-file name collision
                collision_result = await db.execute(select(Macro).where(Macro.name == pm.name))
                collision = collision_result.scalar_one_or_none()
                if collision is not None:
                    logger.warning(
                        "Macro name '%s' in %s conflicts with cfg_file_id=%s — skipping",
                        pm.name,
                        relative_path,
                        collision.cfg_file_id,
                    )
                    conflict_msg = f"Name conflict: '{pm.name}' already defined in another file"
                    cfg_file.parse_error = f"{file_level_error}; {conflict_msg}" if file_level_error else conflict_msg
                    continue

                db.add(
                    Macro(
                        name=pm.name,
                        description=pm.description,
                        cfg_file_id=cfg_file.id,
                        trigger_type=pm.trigger_type,
                        cron_expression=pm.cron_expression,
                        printer_id=printer_id,
                    )
                )

        # Delete macros that are no longer in the file
        removed = [name for name in existing if name not in parsed_names]
        if removed:
            logger.info("Deleting removed macros from %s: %s", relative_path, removed)
            await db.execute(delete(Macro).where(Macro.cfg_file_id == cfg_file.id, Macro.name.in_(removed)))

        await db.commit()
        await db.refresh(cfg_file)
        return cfg_file


async def delete_file_from_db(relative_path: str) -> None:
    """Delete the MacroCfgFile row (cascade deletes all its macros)."""
    async with async_session() as db:
        result = await db.execute(select(MacroCfgFile).where(MacroCfgFile.file_path == relative_path))
        cfg_file = result.scalar_one_or_none()
        if cfg_file is None:
            return
        await db.delete(cfg_file)
        await db.commit()
