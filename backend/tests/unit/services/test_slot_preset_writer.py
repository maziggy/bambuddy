"""Tests for the shared slot_preset_mappings writer.

The writer keeps PrintersPage's slot-card preset_name in sync with the
currently-assigned spool from three different inventory paths:

* internal manual assign (inventory.apply_spool_to_slot_via_mqtt)
* internal RFID auto-assign (spool_tag_matcher.auto_assign_spool)
* Spoolman RFID sync (main.auto_sync_spoolman_ams_trays)

Internal-mode regression coverage lives in test_spool_tag_matcher.py
(which exercises the call site end-to-end). This file focuses on the
helper-level contracts — local-preset id formatting, soft-fail on
no preset_id, and the Spoolman derivation path.
"""

import pytest
from sqlalchemy import select

from backend.app.models.slot_preset import SlotPresetMapping
from backend.app.services.slot_preset_writer import (
    upsert_slot_preset,
    upsert_slot_preset_for_spoolman_spool,
)


@pytest.mark.asyncio
async def test_upsert_no_op_when_preset_id_empty(db_session, printer_factory):
    """An empty preset_id is not a useful key — the model's column is NOT
    NULL and an empty string would overwrite the user's last good preset
    with garbage. Skip without raising."""
    printer = await printer_factory()
    await upsert_slot_preset(
        db=db_session,
        printer_id=printer.id,
        ams_id=0,
        tray_id=0,
        preset_id="",
        preset_name="ignored",
    )
    result = await db_session.execute(select(SlotPresetMapping).where(SlotPresetMapping.printer_id == printer.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_upsert_inserts_then_updates(db_session, printer_factory):
    """First call inserts, second call on same (printer, ams, tray) updates
    in place rather than violating the unique constraint."""
    printer = await printer_factory()
    await upsert_slot_preset(
        db=db_session,
        printer_id=printer.id,
        ams_id=0,
        tray_id=1,
        preset_id="GFSA50",
        preset_name="Bambu PLA-CF",
        preset_source="cloud",
    )
    await upsert_slot_preset(
        db=db_session,
        printer_id=printer.id,
        ams_id=0,
        tray_id=1,
        preset_id="GFSA00",
        preset_name="Bambu PLA Basic",
        preset_source="cloud",
    )

    rows = (
        (await db_session.execute(select(SlotPresetMapping).where(SlotPresetMapping.printer_id == printer.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].preset_id == "GFSA00"
    assert rows[0].preset_name == "Bambu PLA Basic"


# -- Spoolman derivation ----------------------------------------------------


@pytest.mark.asyncio
async def test_spoolman_helper_uses_filament_name_and_tray_info_idx(db_session, printer_factory):
    """A Spoolman spool with a typical filament shape — name + material —
    should land in the row with the AMS-reported tray_info_idx as
    preset_id (normalised to setting_id form) and the filament name as
    preset_name."""
    printer = await printer_factory()
    spoolman_spool = {
        "id": 42,
        "filament": {
            "id": 7,
            "name": "Bambu PLA-CF Burgundy Red",
            "material": "PLA-CF",
        },
    }
    await upsert_slot_preset_for_spoolman_spool(
        db=db_session,
        spoolman_spool=spoolman_spool,
        tray_info_idx="GFA50",
        tray_sub_brands="PLA-CF",
        tray_type="PLA",
        printer_id=printer.id,
        ams_id=1,
        tray_id=2,
    )
    mapping = (
        await db_session.execute(
            select(SlotPresetMapping).where(
                SlotPresetMapping.printer_id == printer.id,
                SlotPresetMapping.ams_id == 1,
                SlotPresetMapping.tray_id == 2,
            )
        )
    ).scalar_one()
    assert mapping.preset_id == "GFSA50"
    assert mapping.preset_name == "Bambu PLA-CF Burgundy Red"
    assert mapping.preset_source == "cloud"


@pytest.mark.asyncio
async def test_spoolman_helper_falls_back_to_material_when_name_missing(db_session, printer_factory):
    """Some Spoolman setups have unnamed filaments — fall back through
    material → tray_sub_brands → tray_type so we never write an empty
    preset_name."""
    printer = await printer_factory()
    spoolman_spool = {"id": 99, "filament": {"material": "PETG"}}
    await upsert_slot_preset_for_spoolman_spool(
        db=db_session,
        spoolman_spool=spoolman_spool,
        tray_info_idx="GFG00",
        tray_sub_brands="PETG Basic",
        tray_type="PETG",
        printer_id=printer.id,
        ams_id=0,
        tray_id=0,
    )
    mapping = (
        await db_session.execute(select(SlotPresetMapping).where(SlotPresetMapping.printer_id == printer.id))
    ).scalar_one()
    assert mapping.preset_name == "PETG"


@pytest.mark.asyncio
async def test_spoolman_helper_overwrites_stale_internal_row(db_session, printer_factory):
    """Mirror of the internal-mode regression: pre-seed a stale row with a
    previous spool's name, run the Spoolman helper, verify the row now
    reflects the freshly-synced Spoolman spool. This is the bug shape
    that would surface on a Spoolman user with a manually-set preset."""
    printer = await printer_factory()
    db_session.add(
        SlotPresetMapping(
            printer_id=printer.id,
            ams_id=1,
            tray_id=2,
            preset_id="GFSA06_09",
            preset_name="Bambu PLA Silk+",
            preset_source="cloud",
        )
    )
    await db_session.commit()

    await upsert_slot_preset_for_spoolman_spool(
        db=db_session,
        spoolman_spool={"id": 42, "filament": {"name": "Bambu PLA-CF"}},
        tray_info_idx="GFA50",
        tray_sub_brands="PLA-CF",
        tray_type="PLA",
        printer_id=printer.id,
        ams_id=1,
        tray_id=2,
    )
    mapping = (
        await db_session.execute(
            select(SlotPresetMapping).where(
                SlotPresetMapping.printer_id == printer.id,
                SlotPresetMapping.ams_id == 1,
                SlotPresetMapping.tray_id == 2,
            )
        )
    ).scalar_one()
    assert mapping.preset_name == "Bambu PLA-CF"
    assert mapping.preset_id == "GFSA50"


@pytest.mark.asyncio
async def test_spoolman_helper_skips_when_tray_info_idx_unknown(db_session, printer_factory):
    """No tray_info_idx → no preset_id → upsert skips. Caller must not
    write an empty-string preset_id (would clobber any existing row)."""
    printer = await printer_factory()
    await upsert_slot_preset_for_spoolman_spool(
        db=db_session,
        spoolman_spool={"id": 1, "filament": {"name": "Random"}},
        tray_info_idx="",
        tray_sub_brands="",
        tray_type="PLA",
        printer_id=printer.id,
        ams_id=0,
        tray_id=0,
    )
    result = await db_session.execute(select(SlotPresetMapping).where(SlotPresetMapping.printer_id == printer.id))
    assert result.scalar_one_or_none() is None
