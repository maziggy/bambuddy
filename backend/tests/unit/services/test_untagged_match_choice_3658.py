"""The RFID matcher asks instead of guessing between spools that differ (#3658).

``find_matching_untagged_spool`` used to give a newly read tag to the oldest
untagged spool of its material and colour. That is right for identical sealed
spools (#918), and a guess when one of them is open: the tag of the opened
spool in the AMS went to a sealed one, and the slot's usage with it.
"""

import pytest

from backend.app.models.spool import Spool
from backend.app.services.spool_tag_matcher import (
    find_matching_untagged_spool,
    find_untagged_candidates,
    untagged_match_is_ambiguous,
)

TRAY = {
    "tray_type": "PETG",
    "tray_sub_brands": "PETG HF",
    "tray_color": "179CB0FF",
    "tray_id_name": "",
    "tag_uid": "AABBCCDD11223344",
    "tray_uuid": "AABBCCDD11223344AABBCCDD11223344",
    "tray_info_idx": "GFG00",
}


async def _spool(db_session, *, subtype: str | None = "HF", weight_used: float = 0, **kwargs) -> Spool:
    spool = Spool(
        material="PETG",
        subtype=subtype,
        rgba="179CB0FF",
        brand="Bambu Lab",
        label_weight=1000,
        core_weight=250,
        weight_used=weight_used,
        **kwargs,
    )
    db_session.add(spool)
    await db_session.commit()
    return spool


@pytest.mark.asyncio
async def test_identical_sealed_spools_still_go_oldest_first(db_session):
    """The bulk-logged sealed boxes of #918 keep needing no clicks."""
    first = await _spool(db_session)
    await _spool(db_session)

    found = await find_matching_untagged_spool(db_session, TRAY)

    assert found is not None
    assert found.id == first.id


@pytest.mark.asyncio
async def test_an_opened_spool_among_the_candidates_links_none(db_session):
    """The issue's case: two sealed spools logged first, then the opened one."""
    sealed_a = await _spool(db_session)
    sealed_b = await _spool(db_session)
    opened = await _spool(db_session, weight_used=175)

    candidates = await find_untagged_candidates(db_session, TRAY)

    assert [s.id for s in candidates] == [sealed_a.id, sealed_b.id, opened.id]
    assert untagged_match_is_ambiguous(candidates)
    assert await find_matching_untagged_spool(db_session, TRAY) is None


@pytest.mark.asyncio
async def test_one_opened_spool_alone_is_no_choice(db_session):
    """A single candidate is linked whatever it has used: there is nothing to choose."""
    opened = await _spool(db_session, weight_used=300)

    found = await find_matching_untagged_spool(db_session, TRAY)

    assert found is not None
    assert found.id == opened.id


@pytest.mark.asyncio
async def test_only_the_best_group_counts(db_session):
    """An exact-subtype spool outranks NULL-subtype ones; their usage is not a reason to ask."""
    exact = await _spool(db_session)
    await _spool(db_session, subtype=None, weight_used=500)
    await _spool(db_session, subtype=None)

    candidates = await find_untagged_candidates(db_session, TRAY)

    assert [s.id for s in candidates] == [exact.id]
    found = await find_matching_untagged_spool(db_session, TRAY)
    assert found is not None
    assert found.id == exact.id


@pytest.mark.asyncio
async def test_null_subtype_group_asks_when_its_spools_differ(db_session):
    """Quick Add leaves the subtype empty, so the fallback group is the one that differs most often."""
    quick_sealed = await _spool(db_session, subtype=None)
    quick_opened = await _spool(db_session, subtype=None, weight_used=120)

    candidates = await find_untagged_candidates(db_session, TRAY)

    assert [s.id for s in candidates] == [quick_sealed.id, quick_opened.id]
    assert untagged_match_is_ambiguous(candidates)


@pytest.mark.asyncio
async def test_tagged_archived_and_other_colours_are_no_candidates(db_session):
    from datetime import datetime, timezone

    keep = await _spool(db_session)
    await _spool(db_session, weight_used=50, tag_uid="0102030405060708")
    await _spool(db_session, weight_used=50, archived_at=datetime.now(timezone.utc))
    other_colour = Spool(
        material="PETG", subtype="HF", rgba="FFFFFFFF", brand="Bambu Lab", label_weight=1000, weight_used=50
    )
    db_session.add(other_colour)
    await db_session.commit()

    candidates = await find_untagged_candidates(db_session, TRAY)

    assert [s.id for s in candidates] == [keep.id]
    assert not untagged_match_is_ambiguous(candidates)


def test_ambiguity_rule():
    sealed = Spool(material="PETG", label_weight=1000, weight_used=0)
    also_sealed = Spool(material="PETG", label_weight=1000, weight_used=None)
    opened = Spool(material="PETG", label_weight=1000, weight_used=1.5)

    assert not untagged_match_is_ambiguous([])
    assert not untagged_match_is_ambiguous([opened])
    assert not untagged_match_is_ambiguous([sealed, also_sealed])
    assert untagged_match_is_ambiguous([sealed, opened])


@pytest.mark.asyncio
async def test_usage_past_the_first_fifty_candidates_requires_a_choice(db_session):
    rows = [
        Spool(material="PETG", subtype="HF", rgba="179CB0FF", label_weight=750, weight_used=0 if index < 50 else 175)
        for index in range(51)
    ]
    db_session.add_all(rows)
    await db_session.commit()
    assert len(await find_untagged_candidates(db_session, TRAY)) == 51
    assert await find_matching_untagged_spool(db_session, TRAY) is None
