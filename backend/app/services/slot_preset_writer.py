"""Shared upsert for the slot_preset_mappings row that drives the AMS slot
card's displayed preset name.

Three call sites must keep this row in sync with the currently-assigned spool:

- ``api.routes.inventory.apply_spool_to_slot_via_mqtt`` (internal manual assign)
- ``services.spool_tag_matcher.auto_assign_spool`` (internal RFID auto-assign)
- ``main.auto_sync_spoolman_ams_trays`` (Spoolman RFID-driven sync)

If any of them skips this row, the slot card surfaces the previous spool's
preset name because the PrintersPage display chain consults
slot_preset_mappings.preset_name first — it overrides cloudInfo.name and the
spool's own slicer_filament_name.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.slot_preset import SlotPresetMapping
from backend.app.models.spool import Spool
from backend.app.utils.filament_ids import filament_id_to_setting_id

logger = logging.getLogger(__name__)


async def upsert_slot_preset(
    *,
    db: AsyncSession,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    preset_id: str,
    preset_name: str,
    preset_source: str = "cloud",
) -> None:
    """Primitive upsert. No-op when ``preset_id`` is empty (the column is
    NOT NULL on the model, and an empty string isn't a useful key to
    overwrite by). Soft-fails on DB errors so a broken upsert never
    cascades into the surrounding spool-assign flow.
    """
    if not preset_id:
        return
    try:
        existing = await db.execute(
            select(SlotPresetMapping).where(
                SlotPresetMapping.printer_id == printer_id,
                SlotPresetMapping.ams_id == ams_id,
                SlotPresetMapping.tray_id == tray_id,
            )
        )
        mapping = existing.scalar_one_or_none()
        if mapping:
            mapping.preset_id = preset_id
            mapping.preset_name = preset_name
            mapping.preset_source = preset_source
        else:
            mapping = SlotPresetMapping(
                printer_id=printer_id,
                ams_id=ams_id,
                tray_id=tray_id,
                preset_id=preset_id,
                preset_name=preset_name,
                preset_source=preset_source,
            )
            db.add(mapping)
        await db.commit()
    except Exception as e:
        logger.warning(
            "Failed to save slot preset mapping for printer=%d ams=%d tray=%d: %s",
            printer_id,
            ams_id,
            tray_id,
            e,
        )


async def upsert_slot_preset_for_spool(
    *,
    db: AsyncSession,
    spool: Spool,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    tray_info_idx: str = "",
    tray_sub_brands: str = "",
    tray_type: str = "",
    setting_id: str = "",
) -> None:
    """Convenience wrapper for internal-mode call sites — derives the
    (preset_id, preset_name, preset_source) triple from a ``Spool`` ORM object,
    then defers to ``upsert_slot_preset``.

    Local numeric ``spool.slicer_filament`` (e.g. ``"50"``) → ``local_50``;
    cloud-form ids (GFS… / GFA… via ``filament_id_to_setting_id`` on the
    tray's ``tray_info_idx``) → standard setting_id form.
    """
    preset_name = spool.slicer_filament_name or tray_sub_brands or tray_type
    preset_source = "cloud"
    sf = spool.slicer_filament or ""
    if sf:
        base_sf_mapping = sf.split("_")[0] if "_" in sf else sf
        try:
            int(base_sf_mapping)
            preset_id = f"local_{base_sf_mapping}"
            preset_source = "local"
        except (ValueError, TypeError):
            preset_id = filament_id_to_setting_id(tray_info_idx) if tray_info_idx else setting_id
    else:
        preset_id = filament_id_to_setting_id(tray_info_idx) if tray_info_idx else ""

    await upsert_slot_preset(
        db=db,
        printer_id=printer_id,
        ams_id=ams_id,
        tray_id=tray_id,
        preset_id=preset_id,
        preset_name=preset_name,
        preset_source=preset_source,
    )


async def upsert_slot_preset_for_spoolman_spool(
    *,
    db: AsyncSession,
    spoolman_spool: dict,
    tray_info_idx: str,
    tray_sub_brands: str,
    tray_type: str,
    printer_id: int,
    ams_id: int,
    tray_id: int,
) -> None:
    """Spoolman-mode counterpart. Derives ``preset_name`` from the Spoolman
    filament's display name (falling back to material if absent) and
    ``preset_id`` from the AMS-reported tray_info_idx (the cloud filament
    id the printer is currently using). ``preset_source`` is always
    ``"cloud"`` since Spoolman doesn't carry a local-preset concept.

    The ``spoolman_spool`` dict is the shape returned by
    ``SpoolmanClient.sync_ams_tray`` — ``spool["filament"]["name"]`` etc.
    """
    filament = spoolman_spool.get("filament") or {}
    preset_name = filament.get("name") or filament.get("material") or tray_sub_brands or tray_type
    preset_id = filament_id_to_setting_id(tray_info_idx) if tray_info_idx else ""

    await upsert_slot_preset(
        db=db,
        printer_id=printer_id,
        ams_id=ams_id,
        tray_id=tray_id,
        preset_id=preset_id,
        preset_name=preset_name or "",
        preset_source="cloud",
    )
