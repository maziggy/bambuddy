"""Tests for spool_tag_matcher service — RFID auto-assign and relationship loading."""

import pytest
from sqlalchemy import inspect

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.spool_tag_matcher import (
    auto_assign_spool,
    create_spool_from_tray,
    find_matching_untagged_spool,
    get_spool_by_tag,
    is_bambu_tag,
    is_valid_tag,
    link_tag_to_inventory_spool,
)

# -- helpers -----------------------------------------------------------------

SAMPLE_TRAY = {
    "tray_type": "PLA",
    "tray_sub_brands": "PLA Basic",
    "tray_color": "FFFFFFFF",
    "tray_id_name": "",
    "tag_uid": "AABBCCDD11223344",
    "tray_uuid": "AABBCCDD11223344AABBCCDD11223344",
    "tray_info_idx": "GFL99",
    "nozzle_temp_min": 190,
    "nozzle_temp_max": 230,
    "tray_weight": "1000",
    "remain": 80,
}


def _relationship_is_loaded(obj, attr_name: str) -> bool:
    """Check if a relationship attribute has been eagerly loaded (not lazy)."""
    return attr_name in inspect(obj).dict


# -- is_valid_tag / is_bambu_tag --------------------------------------------


def test_is_valid_tag_with_real_uid():
    assert is_valid_tag("AABBCCDD11223344", "") is True


def test_is_valid_tag_with_real_uuid():
    assert is_valid_tag("", "AABBCCDD11223344AABBCCDD11223344") is True


def test_is_valid_tag_all_zeros():
    assert is_valid_tag("0000000000000000", "00000000000000000000000000000000") is False


def test_is_valid_tag_empty():
    assert is_valid_tag("", "") is False


def test_is_bambu_tag_with_uuid():
    assert is_bambu_tag("", "AABBCCDD11223344AABBCCDD11223344", "") is True


def test_is_bambu_tag_with_uid_and_preset():
    assert is_bambu_tag("AABBCCDD11223344", "", "GFL99") is True


def test_is_bambu_tag_uid_only_no_preset():
    """A tag UID alone (no UUID, no preset) is NOT considered a Bambu tag."""
    assert is_bambu_tag("AABBCCDD11223344", "", "") is False


# -- create_spool_from_tray -------------------------------------------------


@pytest.mark.asyncio
async def test_create_spool_from_tray_basic(db_session):
    """Created spool has correct material and tag fields."""
    spool = await create_spool_from_tray(db_session, SAMPLE_TRAY)
    await db_session.commit()

    assert spool.id is not None
    assert spool.material == "PLA"
    assert spool.brand == "Bambu Lab"
    assert spool.tag_uid == "AABBCCDD11223344"
    assert spool.tray_uuid == "AABBCCDD11223344AABBCCDD11223344"
    assert spool.data_origin == "rfid_auto"


@pytest.mark.asyncio
async def test_create_spool_from_tray_weight_from_remain(db_session):
    """weight_used is calculated from the AMS remain percentage."""
    spool = await create_spool_from_tray(db_session, SAMPLE_TRAY)
    # remain=80 → 20% used → 200g of 1000g
    assert spool.weight_used == 200.0


@pytest.mark.asyncio
async def test_create_spool_from_tray_relationships_loaded(db_session):
    """Both k_profiles and assignments must be eagerly initialized.

    If these are lazy, db.add(SpoolAssignment(spool_id=spool.id)) triggers
    a back_populates lazy load outside the async greenlet → greenlet_spawn error.
    Regression test for #612.
    """
    spool = await create_spool_from_tray(db_session, SAMPLE_TRAY)

    assert _relationship_is_loaded(spool, "k_profiles"), "k_profiles not eagerly initialized"
    assert _relationship_is_loaded(spool, "assignments"), "assignments not eagerly initialized"
    assert spool.k_profiles == []
    assert spool.assignments == []


# -- get_spool_by_tag -------------------------------------------------------


@pytest.mark.asyncio
async def test_get_spool_by_tag_by_uuid(db_session):
    """Look up a spool by tray_uuid."""
    spool = Spool(
        material="PLA",
        tray_uuid="AABBCCDD11223344AABBCCDD11223344",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await get_spool_by_tag(db_session, "", "AABBCCDD11223344AABBCCDD11223344")
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_get_spool_by_tag_by_uid(db_session):
    """Fall back to tag_uid when tray_uuid doesn't match."""
    spool = Spool(
        material="PETG",
        tag_uid="1122334455667788",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await get_spool_by_tag(db_session, "1122334455667788", "")
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_get_spool_by_tag_skips_archived(db_session):
    """Archived spools are not returned."""
    from datetime import datetime

    spool = Spool(
        material="PLA",
        tray_uuid="AABBCCDD11223344AABBCCDD11223344",
        label_weight=1000,
        core_weight=250,
        archived_at=datetime.now(),
    )
    db_session.add(spool)
    await db_session.commit()

    found = await get_spool_by_tag(db_session, "", "AABBCCDD11223344AABBCCDD11223344")
    assert found is None


@pytest.mark.asyncio
async def test_get_spool_by_tag_relationships_loaded(db_session):
    """Both k_profiles and assignments must be eagerly loaded.

    Regression test for #612 — without selectinload(Spool.assignments),
    accessing spool.assignments after get_spool_by_tag triggers a lazy load
    in async context → greenlet_spawn error.
    """
    spool = Spool(
        material="PLA",
        tray_uuid="AABBCCDD11223344AABBCCDD11223344",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()
    # Expire to clear in-session state — forces selectinload to actually load
    db_session.expire(spool)

    found = await get_spool_by_tag(db_session, "", "AABBCCDD11223344AABBCCDD11223344")
    assert found is not None
    assert _relationship_is_loaded(found, "k_profiles"), "k_profiles not eagerly loaded"
    assert _relationship_is_loaded(found, "assignments"), "assignments not eagerly loaded"


@pytest.mark.asyncio
async def test_get_spool_by_tag_returns_none_for_zeros(db_session):
    """Zero-value tags return None."""
    found = await get_spool_by_tag(db_session, "0000000000000000", "00000000000000000000000000000000")
    assert found is None


# -- auto_assign_spool (SpoolAssignment creation) ---------------------------


@pytest.mark.asyncio
async def test_auto_assign_creates_assignment(db_session, printer_factory):
    """auto_assign_spool creates a SpoolAssignment for the given slot."""
    from unittest.mock import MagicMock

    printer = await printer_factory()
    spool = await create_spool_from_tray(db_session, SAMPLE_TRAY)
    await db_session.commit()

    mock_pm = MagicMock()
    mock_pm.get_status.return_value = None
    mock_pm.get_client.return_value = None

    assignment = await auto_assign_spool(
        printer_id=printer.id,
        ams_id=0,
        tray_id=2,
        spool=spool,
        printer_manager=mock_pm,
        db=db_session,
    )
    await db_session.commit()

    assert assignment.spool_id == spool.id
    assert assignment.printer_id == printer.id
    assert assignment.ams_id == 0
    assert assignment.tray_id == 2


@pytest.mark.asyncio
async def test_auto_assign_replaces_existing(db_session, printer_factory):
    """auto_assign_spool removes old assignment for the same slot."""
    from unittest.mock import MagicMock

    from sqlalchemy import select

    printer = await printer_factory()

    # Create two spools
    spool1 = Spool(material="PLA", label_weight=1000, core_weight=250)
    spool1.k_profiles = []
    spool1.assignments = []
    db_session.add(spool1)
    await db_session.flush()

    spool2 = Spool(material="PETG", label_weight=1000, core_weight=250)
    spool2.k_profiles = []
    spool2.assignments = []
    db_session.add(spool2)
    await db_session.flush()

    mock_pm = MagicMock()
    mock_pm.get_status.return_value = None
    mock_pm.get_client.return_value = None

    # Assign spool1 to slot
    await auto_assign_spool(printer.id, 0, 0, spool1, mock_pm, db_session)
    await db_session.commit()

    # Assign spool2 to same slot — should replace
    await auto_assign_spool(printer.id, 0, 0, spool2, mock_pm, db_session)
    await db_session.commit()

    result = await db_session.execute(
        select(SpoolAssignment).where(
            SpoolAssignment.printer_id == printer.id,
            SpoolAssignment.ams_id == 0,
            SpoolAssignment.tray_id == 0,
        )
    )
    assignments = result.scalars().all()
    assert len(assignments) == 1
    assert assignments[0].spool_id == spool2.id


@pytest.mark.asyncio
async def test_auto_assign_no_greenlet_error_new_spool(db_session, printer_factory):
    """Creating a SpoolAssignment for a newly created spool must not trigger
    a lazy load on spool.assignments (greenlet_spawn error).

    Regression test for #612: db.add(SpoolAssignment) resolves
    back_populates synchronously. If spool.assignments is uninitialized,
    SQLAlchemy attempts a lazy load outside the async greenlet.
    """
    from unittest.mock import MagicMock

    printer = await printer_factory()
    spool = await create_spool_from_tray(db_session, SAMPLE_TRAY)
    # Don't commit yet — keep spool in same session state as production flow

    mock_pm = MagicMock()
    mock_pm.get_status.return_value = None
    mock_pm.get_client.return_value = None

    # This must NOT raise MissingGreenlet / greenlet_spawn error
    assignment = await auto_assign_spool(
        printer_id=printer.id,
        ams_id=0,
        tray_id=0,
        spool=spool,
        printer_manager=mock_pm,
        db=db_session,
    )
    await db_session.commit()

    assert assignment is not None
    assert assignment.spool_id == spool.id


@pytest.mark.asyncio
async def test_auto_assign_no_greenlet_error_existing_spool(db_session, printer_factory):
    """Creating a SpoolAssignment for an existing spool (from get_spool_by_tag)
    must not trigger a lazy load on spool.assignments.

    Regression test for #612.
    """
    from unittest.mock import MagicMock

    printer = await printer_factory()

    # Create spool directly (simulating one that was created in a previous session)
    spool = Spool(
        material="PLA",
        tray_uuid="AABBCCDD11223344AABBCCDD11223344",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()
    # Expire to clear in-session state — simulates fresh query
    db_session.expire(spool)

    # Look up via get_spool_by_tag (must eagerly load relationships)
    found = await get_spool_by_tag(db_session, "", "AABBCCDD11223344AABBCCDD11223344")
    assert found is not None

    mock_pm = MagicMock()
    mock_pm.get_status.return_value = None
    mock_pm.get_client.return_value = None

    # This must NOT raise MissingGreenlet / greenlet_spawn error
    assignment = await auto_assign_spool(
        printer_id=printer.id,
        ams_id=0,
        tray_id=0,
        spool=found,
        printer_manager=mock_pm,
        db=db_session,
    )
    await db_session.commit()

    assert assignment is not None
    assert assignment.spool_id == found.id


# -- find_matching_untagged_spool -------------------------------------------


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_exact_match(db_session):
    """Finds an untagged spool with matching material, subtype, and color."""
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_skips_tagged(db_session):
    """Spools that already have a tag_uid are not matched."""
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
        tag_uid="1122334455667788",
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is None


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_skips_uuid_tagged(db_session):
    """Spools that already have a tray_uuid are not matched."""
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
        tray_uuid="AABBCCDD11223344AABBCCDD11223344",
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is None


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_skips_archived(db_session):
    """Archived spools are not matched."""
    from datetime import datetime

    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
        archived_at=datetime.now(),
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is None


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_wrong_material(db_session):
    """Material mismatch returns None."""
    spool = Spool(
        material="PETG",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is None


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_wrong_color(db_session):
    """Color (rgba) mismatch returns None."""
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FF0000FF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is None


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_wrong_subtype(db_session):
    """Subtype mismatch returns None (PLA Matte vs PLA Basic)."""
    spool = Spool(
        material="PLA",
        subtype="Matte",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is None


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_fifo(db_session):
    """When multiple match, returns the oldest (FIFO)."""
    import asyncio

    spool_old = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool_old)
    await db_session.flush()

    # Small delay to ensure different created_at
    await asyncio.sleep(0.05)

    spool_new = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool_new)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert found.id == spool_old.id


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_case_insensitive(db_session):
    """Matching is case-insensitive for material and rgba."""
    spool = Spool(
        material="pla",
        subtype="basic",
        rgba="ffffffff",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_no_subtype(db_session):
    """Tray without subtype matches spool without subtype."""
    tray = {**SAMPLE_TRAY, "tray_sub_brands": "PLA", "tray_type": "PLA"}
    spool = Spool(
        material="PLA",
        subtype=None,
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, tray)
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_relationships_loaded(db_session):
    """Matched spool has k_profiles and assignments eagerly loaded."""
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()
    db_session.expire(spool)

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert _relationship_is_loaded(found, "k_profiles")
    assert _relationship_is_loaded(found, "assignments")


# -- link_tag_to_inventory_spool -------------------------------------------


@pytest.mark.asyncio
async def test_link_tag_to_inventory_spool(db_session):
    """Links RFID tag data to an existing spool."""
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.flush()

    await link_tag_to_inventory_spool(db_session, spool, SAMPLE_TRAY)
    await db_session.commit()

    assert spool.tag_uid == "AABBCCDD11223344"
    assert spool.tray_uuid == "AABBCCDD11223344AABBCCDD11223344"
    assert spool.data_origin == "rfid_linked"
    assert spool.tag_type == "bambulab"
    assert spool.slicer_filament == "GFL99"


@pytest.mark.asyncio
async def test_link_tag_preserves_existing_slicer_filament(db_session):
    """Does not overwrite an existing slicer_filament preset."""
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
        slicer_filament="CUSTOM01",
        slicer_filament_name="My Custom PLA",
    )
    db_session.add(spool)
    await db_session.flush()

    await link_tag_to_inventory_spool(db_session, spool, SAMPLE_TRAY)
    await db_session.commit()

    assert spool.slicer_filament == "CUSTOM01"
    assert spool.slicer_filament_name == "My Custom PLA"
