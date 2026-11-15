"""Owned build plates and the plate on each printer (#1306).

A plate is a slicer base type plus an optional pattern (see
``services/build_plate_config``). The base types are fixed; the user ticks the
ones they own and adds patterns under them.

Permission model:

* Reading the plates needs :attr:`Permission.PRINTERS_READ`: the printer cards
  and print dialog show them to anyone who can see printers.
* Changing which plates are owned is a settings change
  (:attr:`Permission.SETTINGS_UPDATE`).
* Recording a plate swap on a printer is a physical, at-the-machine action, so
  it is gated like the other printer controls (:attr:`Permission.PRINTERS_CONTROL`)
  rather than needing rights to edit the printer's configuration.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.schemas.build_plate import (
    BuildPlateConfigResponse,
    BuildPlateConfigUpdate,
    InstalledPlateUpdate,
    PlatePatternInfo,
    PlateTypeInfo,
)
from backend.app.schemas.printer import PrinterResponse
from backend.app.services.build_plate_config import (
    PATTERN_SUGGESTIONS,
    PlateConfig,
    PlatePattern,
    load_plate_config,
    save_plate_config,
)
from backend.app.utils.bed_types import BED_TYPES, plate_label, same_pattern

logger = logging.getLogger(__name__)

router = APIRouter(tags=["build-plates"])


def _key(base_type: str, pattern: str | None) -> tuple[str, str]:
    return base_type, (pattern or "").strip().casefold()


async def _config_response(db: AsyncSession, config: PlateConfig) -> BuildPlateConfigResponse:
    """The owned plates, with where each is fitted and how many jobs wait for it.

    The counts let the settings page warn before unticking a plate in use.
    """
    printers = (
        await db.execute(
            select(Printer.id, Printer.installed_plate_type, Printer.installed_plate_pattern).where(
                Printer.installed_plate_type.is_not(None)
            )
        )
    ).all()
    jobs = (
        await db.execute(
            select(PrintQueueItem.required_plate_type, PrintQueueItem.required_plate_pattern)
            .where(PrintQueueItem.status == "pending")
            .where(PrintQueueItem.required_plate_type.in_(list(BED_TYPES)))
        )
    ).all()

    type_printers: dict[str, list[int]] = defaultdict(list)
    pattern_printers: dict[tuple[str, str], list[int]] = defaultdict(list)
    for printer_id, base_type, pattern in printers:
        if base_type is None:
            continue
        type_printers[base_type].append(printer_id)
        if pattern:
            pattern_printers[_key(base_type, pattern)].append(printer_id)
    type_jobs = Counter(base_type for base_type, _ in jobs if base_type)
    pattern_jobs = Counter(_key(base_type, pattern) for base_type, pattern in jobs if base_type and pattern)

    return BuildPlateConfigResponse(
        types=[
            PlateTypeInfo(
                key=key,
                label=label,
                owned=key in config.owned_types,
                installed_on=sorted(type_printers[key]),
                waiting_jobs=type_jobs[key],
            )
            for key, label in BED_TYPES.items()
        ],
        patterns=[
            PlatePatternInfo(
                base_type=p.base_type,
                name=p.name,
                label=plate_label(p.base_type, p.name),
                installed_on=sorted(pattern_printers[_key(p.base_type, p.name)]),
                waiting_jobs=pattern_jobs[_key(p.base_type, p.name)],
            )
            for p in config.patterns
        ],
        pattern_suggestions=list(PATTERN_SUGGESTIONS),
    )


@router.get("/build-plates", response_model=BuildPlateConfigResponse)
@router.get("/build-plates/", response_model=BuildPlateConfigResponse)
async def get_build_plates(
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
) -> BuildPlateConfigResponse:
    """Every plate type (ticked or not) and the patterns the user added."""
    return await _config_response(db, await load_plate_config(db))


@router.put("/build-plates", response_model=BuildPlateConfigResponse)
@router.put("/build-plates/", response_model=BuildPlateConfigResponse)
async def save_build_plates(
    payload: BuildPlateConfigUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
) -> BuildPlateConfigResponse:
    """Replace the owned plate types and patterns.

    Unticking a type only hides it from the pickers: printers keep the plate
    recorded on them and jobs keep the plate their file needs. Removing a
    pattern is different, because nothing can be set to it any more: printers
    that had it fitted keep their plate type without the pattern, and pending
    jobs that asked for it accept any plate of their type.
    """
    patterns: list[PlatePattern] = []
    for p in payload.patterns:
        if any(q.base_type == p.base_type and same_pattern(q.name, p.name) for q in patterns):
            continue  # the same name twice under one type is one pattern
        patterns.append(PlatePattern(base_type=p.base_type, name=p.name))

    old = await load_plate_config(db)
    for gone in old.patterns:
        if any(p.base_type == gone.base_type and same_pattern(p.name, gone.name) for p in patterns):
            continue
        await _drop_pattern(db, gone)

    config = PlateConfig(owned_types=list(dict.fromkeys(payload.types)), patterns=patterns)
    await save_plate_config(db, config)
    await db.commit()
    return await _config_response(db, config)


async def _drop_pattern(db: AsyncSession, pattern: PlatePattern) -> None:
    """Forget *pattern* on printers and pending jobs (any spelling of its name)."""
    printers = (
        await db.execute(
            select(Printer).where(
                Printer.installed_plate_type == pattern.base_type, Printer.installed_plate_pattern.is_not(None)
            )
        )
    ).scalars()
    for printer in printers:
        if same_pattern(printer.installed_plate_pattern, pattern.name):
            printer.installed_plate_pattern = None
    jobs = (
        await db.execute(
            select(PrintQueueItem).where(
                PrintQueueItem.status == "pending",
                PrintQueueItem.required_plate_type == pattern.base_type,
                PrintQueueItem.required_plate_pattern.is_not(None),
            )
        )
    ).scalars()
    for job in jobs:
        if same_pattern(job.required_plate_pattern, pattern.name):
            job.required_plate_pattern = None
    logger.info("Build plate pattern removed: %s", plate_label(pattern.base_type, pattern.name))


@router.put("/printers/{printer_id}/installed-plate", response_model=PrinterResponse)
async def set_installed_plate(
    printer_id: int,
    payload: InstalledPlateUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
) -> PrinterResponse:
    """Record which plate is now on a printer; ``plate_type: null`` stops tracking it."""
    printer = (await db.execute(select(Printer).where(Printer.id == printer_id))).scalar_one_or_none()
    if printer is None:
        raise HTTPException(404, "Printer not found")
    pattern_name = None
    if payload.pattern:
        if payload.plate_type is None:
            raise HTTPException(400, "A pattern needs a plate type")
        pattern = (await load_plate_config(db)).find_pattern(payload.plate_type, payload.pattern)
        if pattern is None:
            raise HTTPException(400, f"No {plate_label(payload.plate_type, payload.pattern)} plate is set up")
        pattern_name = pattern.name
    printer.installed_plate_type = payload.plate_type
    printer.installed_plate_pattern = pattern_name
    await db.commit()
    await db.refresh(printer)
    logger.info(
        "Printer %s plate set to %s", printer_id, plate_label(payload.plate_type, pattern_name) or "not tracked"
    )
    return PrinterResponse.model_validate(printer)
