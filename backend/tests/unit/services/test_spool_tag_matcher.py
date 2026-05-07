"""Tests for spool_tag_matcher service — RFID auto-assign and relationship loading."""

import pytest
from sqlalchemy import inspect

from backend.app.models.color_catalog import ColorCatalogEntry
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


@pytest.mark.asyncio
async def test_get_spool_by_tag_first_char_variance_same_length(db_session):
    """Match spool when scanned tag differs only in first character.

    Handles case where same physical tag reports different first bytes
    across different readers (e.g., "A45012F" stored, "B45012F" scanned).
    Both tags have same length and differ only in first char.
    """
    spool = Spool(
        material="PLA",
        tag_uid="A4501234CCDDEE88",  # First tag variant
        label_weight=1000,
        core_weight=250,
    )
    spool.k_profiles = []
    spool.assignments = []
    db_session.add(spool)
    await db_session.commit()

    # Scan with different first character — should still match
    found = await get_spool_by_tag(db_session, "B4501234CCDDEE88", "")
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_get_spool_by_tag_first_char_variance_short_uid(db_session):
    """Match spool when 8-char scanned tag differs only in first character.

    Handles short UID (8 char) from 4-byte readers with first-char variance.
    The stored tag is longer (16 char), but the first 8 chars of the stored tag
    should match the scanned 8-char UID with first-char tolerance.
    """
    spool = Spool(
        material="PLA",
        tag_uid="A4501234CCDDEE88",  # 16-char stored tag
        label_weight=1000,
        core_weight=250,
    )
    spool.k_profiles = []
    spool.assignments = []
    db_session.add(spool)
    await db_session.commit()

    # Scan with 8-char short UID whose first char differs but remaining 7 match
    # the first 8 chars of the stored tag: stored[:8] = "A4501234",
    # scanned = "B4501234" → first-char variance on short UID
    found = await get_spool_by_tag(db_session, "B4501234", "")
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_get_spool_by_tag_short_uid_exact_match_preferred(db_session):
    """Prefer exact match over first-char variance match."""
    # Spool with exact 8-char UID match
    spool_exact = Spool(
        material="PLA",
        tag_uid="B4501234",
        label_weight=1000,
        core_weight=250,
    )
    spool_exact.k_profiles = []
    spool_exact.assignments = []
    db_session.add(spool_exact)

    # Spool that would match via first-char variance
    spool_variance = Spool(
        material="PETG",
        tag_uid="A4501234",
        label_weight=1000,
        core_weight=250,
    )
    spool_variance.k_profiles = []
    spool_variance.assignments = []
    db_session.add(spool_variance)
    await db_session.commit()

    # Exact match should win over variance match
    found = await get_spool_by_tag(db_session, "B4501234", "")
    assert found is not None
    assert found.id == spool_exact.id


@pytest.mark.asyncio
async def test_get_spool_by_tag_no_false_positive_different_suffix(db_session):
    """Don't match tags with different suffixes just because first char varies."""
    spool = Spool(
        material="PLA",
        tag_uid="AABBCCDD11223344",
        label_weight=1000,
        core_weight=250,
    )
    spool.k_profiles = []
    spool.assignments = []
    db_session.add(spool)
    await db_session.commit()

    # Scan with different suffix (only first char is same) — should NOT match
    found = await get_spool_by_tag(db_session, "AABBCCDD11223355", "")
    assert found is None, "Should not match when suffix differs"


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


# -- find_matching_untagged_spool: #918 regressions ------------------------


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_null_subtype_fallback(db_session):
    """#918: Quick-Add spool (subtype=NULL) matches when AMS reports a subtype.

    The form's Quick-Add mode only requires `material`, so bulk-logged spools
    have subtype=NULL. Before the fix, the strict `subtype = 'Basic'` filter
    excluded these rows and the system created duplicates on first AMS read.
    """
    spool = Spool(
        material="PLA",
        subtype=None,  # Quick-Add bulk entry
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    # Tray reports "PLA Basic" → subtype parsed as "Basic"
    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_prefers_exact_subtype_over_null(db_session):
    """#918: When both an exact-subtype and a NULL-subtype row match, exact wins.

    The NULL fallback exists only as a backstop for Quick-Add bulk-logged
    spools — if the user did the work to record subtype="Basic", it must
    take precedence over a vague "PLA" record, even if the latter is older.
    """
    import asyncio

    null_spool = Spool(
        material="PLA",
        subtype=None,  # Older but vague — should NOT win
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(null_spool)
    await db_session.flush()

    await asyncio.sleep(0.05)

    exact_spool = Spool(
        material="PLA",
        subtype="Basic",  # Newer but specific — should win
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(exact_spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert found.id == exact_spool.id


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_rejects_non_bambu_brand(db_session):
    """#918: A same-color non-Bambu spool must NOT attract a Bambu UUID.

    Without the brand filter, a Polymaker untagged spool of matching
    material/color would silently acquire a Bambu RFID UUID, leaving the
    user with brand="Polymaker" but a Bambu Lab tray UUID — corrupt data.
    """
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand="Polymaker",  # NOT Bambu — must be rejected
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is None


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_accepts_null_brand(db_session):
    """#918: Quick-Add spools with brand=NULL still match a Bambu RFID read.

    Quick-Add doesn't require brand, so a user bulk-logging Bambu spools may
    leave it empty. The matcher allows NULL brand because the alternative
    (forcing every Quick-Add spool to be tagged "Bambu") is the exact
    friction the auto-matcher exists to remove.
    """
    spool = Spool(
        material="PLA",
        subtype="Basic",
        rgba="FFFFFFFF",
        brand=None,  # Quick-Add left brand blank
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_accepts_bambu_brand_variants(db_session):
    """#918: Both 'Bambu' (form dropdown) and 'Bambu Lab' (catalog) match.

    DEFAULT_BRANDS in the form lists 'Bambu'; the catalog uses 'Bambu Lab'.
    Users can pick either. The fuzzy %bambu% LIKE handles both, plus
    'BambuLab', 'bambu lab', etc.
    """
    for brand_value in ("Bambu", "Bambu Lab", "BambuLab", "bambu lab"):
        spool = Spool(
            material="PLA",
            subtype="Basic",
            rgba="FFFFFFFF",
            brand=brand_value,
            label_weight=1000,
            core_weight=250,
        )
        db_session.add(spool)
        await db_session.commit()

        found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
        assert found is not None, f"brand={brand_value!r} should match"
        assert found.id == spool.id

        # Clean up so the next iteration starts fresh.
        await db_session.delete(spool)
        await db_session.commit()


@pytest.mark.asyncio
async def test_find_matching_untagged_spool_null_subtype_with_null_brand(db_session):
    """#918: Pure Quick-Add row (brand=NULL, subtype=NULL) matches.

    This is the exact scenario from Arn0uDz's report: 20 spools logged via
    Quick Add, then placed in the AMS one at a time. Before the fix every
    insertion duplicated; after the fix the first matching row is reused.
    """
    spool = Spool(
        material="PLA",
        subtype=None,
        rgba="FFFFFFFF",
        brand=None,
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    found = await find_matching_untagged_spool(db_session, SAMPLE_TRAY)
    assert found is not None
    assert found.id == spool.id


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


# -- gradient / multi-color subtype detection --------------------------------


@pytest.mark.asyncio
async def test_create_spool_gradient_from_tray_id_name(db_session):
    """PLA Basic with M* color code → subtype='Gradient'."""
    tray = {
        **SAMPLE_TRAY,
        "tray_sub_brands": "PLA Basic",
        "tray_id_name": "A00-M2",  # Ocean to Meadow
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.material == "PLA"
    assert spool.subtype == "Gradient"


@pytest.mark.asyncio
async def test_create_spool_dual_color_from_tray_id_name(db_session):
    """PLA Silk with A05-M* color code → subtype='Dual Color'."""
    tray = {
        **SAMPLE_TRAY,
        "tray_sub_brands": "PLA Silk",
        "tray_id_name": "A05-M1",  # South Beach
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.material == "PLA"
    assert spool.subtype == "Dual Color"


@pytest.mark.asyncio
async def test_create_spool_tri_color_from_tray_id_name(db_session):
    """PLA Silk with A05-T* color code → subtype='Tri Color'."""
    tray = {
        **SAMPLE_TRAY,
        "tray_sub_brands": "PLA Silk",
        "tray_id_name": "A05-T3",  # Neon City
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.material == "PLA"
    assert spool.subtype == "Tri Color"


@pytest.mark.asyncio
async def test_create_spool_silk_plus_subtype(db_session):
    """PLA Silk+ preserves 'Silk+' subtype (no gradient override)."""
    tray = {
        **SAMPLE_TRAY,
        "tray_sub_brands": "PLA Silk+",
        "tray_id_name": "A06-D0",  # Titan Gray — D code, not M/T
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.material == "PLA"
    assert spool.subtype == "Silk+"


@pytest.mark.asyncio
async def test_create_spool_standard_not_affected(db_session):
    """Standard filaments with D/K/etc codes are not affected."""
    tray = {
        **SAMPLE_TRAY,
        "tray_sub_brands": "PLA Basic",
        "tray_id_name": "A00-D3",  # Dark Gray
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.material == "PLA"
    assert spool.subtype == "Basic"


# -- color resolution (#857) -------------------------------------------------


@pytest.mark.asyncio
async def test_color_resolves_from_catalog_not_suffix_fallback(db_session):
    """Regression for #857 — A17-R1 (PLA Translucent Cherry Pink) must NOT resolve
    to 'Scarlet Red' just because 'R1' also appears in PLA Matte.

    The old resolver fell back to a suffix lookup table when the exact tray_id_name
    wasn't mapped, which produced wrong names across material families. Cross-family
    suffix codes are not globally unique, so only the catalog hex lookup is safe.
    """
    # Seed the catalog with the entry that the Cherry Pink hex should hit.
    db_session.add(
        ColorCatalogEntry(
            manufacturer="Bambu Lab",
            color_name="Cherry Pink",
            hex_color="#F5B6CD",
            material="PLA Translucent",
            is_default=True,
        )
    )
    await db_session.flush()

    tray = {
        **SAMPLE_TRAY,
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Translucent",
        "tray_color": "F5B6CDFF",
        "tray_id_name": "A17-R1",
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.color_name == "Cherry Pink"


@pytest.mark.asyncio
async def test_color_name_is_none_when_catalog_miss_and_code_unreadable(db_session):
    """When the hex isn't in the catalog and tray_id_name is a code ('X##-Y#'),
    color_name must stay None rather than falling through to a wrong suffix match.
    A missing name is preferable to a confidently-wrong one.
    """
    tray = {
        **SAMPLE_TRAY,
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Translucent",
        "tray_color": "F5B6CDFF",  # not seeded
        "tray_id_name": "A17-R1",
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.color_name is None


@pytest.mark.asyncio
async def test_ivory_white_pla_matte_resolves_to_ivory_not_jade(db_session):
    """Regression for #1227 — #FFFFFF is shared by Jade White (PLA Basic),
    Ivory White (PLA Matte), and White (PLA Silk) in the Bambu catalog. The
    matcher must filter by `tray_sub_brands` so a new Ivory White PLA Matte
    roll doesn't auto-name as Jade White just because PLA Basic was inserted
    first.
    """
    # Seed in the order from catalog_defaults.py — PLA Basic first.
    db_session.add(
        ColorCatalogEntry(
            manufacturer="Bambu Lab",
            color_name="Jade White",
            hex_color="#FFFFFF",
            material="PLA Basic",
            is_default=True,
        )
    )
    db_session.add(
        ColorCatalogEntry(
            manufacturer="Bambu Lab",
            color_name="Ivory White",
            hex_color="#FFFFFF",
            material="PLA Matte",
            is_default=True,
        )
    )
    await db_session.flush()

    tray = {
        **SAMPLE_TRAY,
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Matte",
        "tray_color": "FFFFFFFF",
        "tray_id_name": "A01-W1",
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.color_name == "Ivory White", (
        "PLA Matte White must resolve to 'Ivory White', not the PLA Basic 'Jade White' that shares the same hex"
    )


@pytest.mark.asyncio
async def test_pla_silk_white_resolves_to_white_not_jade(db_session):
    """Same shared-hex bug as #1227 but for the third collision: PLA Silk
    White at #FFFFFF must not get the PLA Basic 'Jade White' name either.
    """
    db_session.add(
        ColorCatalogEntry(
            manufacturer="Bambu Lab",
            color_name="Jade White",
            hex_color="#FFFFFF",
            material="PLA Basic",
            is_default=True,
        )
    )
    db_session.add(
        ColorCatalogEntry(
            manufacturer="Bambu Lab",
            color_name="White",
            hex_color="#FFFFFF",
            material="PLA Silk",
            is_default=True,
        )
    )
    await db_session.flush()

    tray = {
        **SAMPLE_TRAY,
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Silk",
        "tray_color": "FFFFFFFF",
        "tray_id_name": "A05-W0",
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.color_name == "White"


@pytest.mark.asyncio
async def test_jade_white_pla_basic_still_resolves_correctly(db_session):
    """Happy-path regression guard for #1227: the PLA Basic Jade White case
    that worked before the fix must still work after it. Catalog has all
    three #FFFFFF entries; the PLA Basic spool must still get 'Jade White'.
    """
    for color_name, material in [
        ("Jade White", "PLA Basic"),
        ("Ivory White", "PLA Matte"),
        ("White", "PLA Silk"),
    ]:
        db_session.add(
            ColorCatalogEntry(
                manufacturer="Bambu Lab",
                color_name=color_name,
                hex_color="#FFFFFF",
                material=material,
                is_default=True,
            )
        )
    await db_session.flush()

    tray = {
        **SAMPLE_TRAY,
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Basic",
        "tray_color": "FFFFFFFF",
        "tray_id_name": "A00-W0",
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.color_name == "Jade White"


@pytest.mark.asyncio
async def test_unknown_material_falls_back_to_hex_only_lookup(db_session):
    """When `tray_sub_brands` is empty (third-party spool / OpenTag tag without
    a Bambu material variant), the material filter is dropped and the lookup
    falls back to hex-only. The deterministic ORDER BY keeps the result
    reproducible across SQLite/PostgreSQL.
    """
    db_session.add(
        ColorCatalogEntry(
            manufacturer="Bambu Lab",
            color_name="Jade White",
            hex_color="#FFFFFF",
            material="PLA Basic",
            is_default=True,
        )
    )
    db_session.add(
        ColorCatalogEntry(
            manufacturer="Bambu Lab",
            color_name="Ivory White",
            hex_color="#FFFFFF",
            material="PLA Matte",
            is_default=True,
        )
    )
    await db_session.flush()

    tray = {
        **SAMPLE_TRAY,
        "tray_type": "PLA",
        "tray_sub_brands": "",  # third-party tag, no material variant
        "tray_color": "FFFFFFFF",
        "tray_id_name": "",
    }
    spool = await create_spool_from_tray(db_session, tray)
    # Either is acceptable so long as the result is deterministic; the first-
    # inserted row (Jade White) wins via ORDER BY id.
    assert spool.color_name == "Jade White"


@pytest.mark.asyncio
async def test_color_name_falls_back_to_readable_tray_id_name(db_session):
    """If tray_id_name is a human-readable label (no code pattern), use it when the
    catalog has no entry for the hex. Preserves behavior for third-party spools whose
    firmware puts a readable string in tray_id_name instead of a Bambu code.
    """
    tray = {
        **SAMPLE_TRAY,
        "tray_color": "123456FF",  # not in catalog
        "tray_id_name": "Custom Purple",  # no '-', readable
    }
    spool = await create_spool_from_tray(db_session, tray)
    assert spool.color_name == "Custom Purple"


@pytest.mark.asyncio
async def test_find_matching_untagged_gradient_spool(db_session):
    """find_matching_untagged_spool matches gradient subtype from tray_id_name."""
    spool = Spool(
        material="PLA",
        subtype="Gradient",
        rgba="FFFFFFFF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
    )
    db_session.add(spool)
    await db_session.commit()

    tray = {
        **SAMPLE_TRAY,
        "tray_sub_brands": "PLA Basic",
        "tray_id_name": "A00-M2",
    }
    found = await find_matching_untagged_spool(db_session, tray)
    assert found is not None
    assert found.id == spool.id


@pytest.mark.asyncio
async def test_find_matching_untagged_gradient_no_match_basic(db_session):
    """A 'Basic' spool does NOT match a Gradient tray (different subtype)."""
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

    tray = {
        **SAMPLE_TRAY,
        "tray_sub_brands": "PLA Basic",
        "tray_id_name": "A00-M2",  # Gradient
    }
    found = await find_matching_untagged_spool(db_session, tray)
    assert found is None
