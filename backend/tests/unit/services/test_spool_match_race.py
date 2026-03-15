"""Tests for concurrent spool matching race condition prevention.

Verifies that two simultaneous on_ams_change calls for identical spool types
(e.g., both load white PLA Basic) don't both claim the same inventory spool.
The _spool_match_lock in main.py serializes find+link+commit so the second
caller sees the first's committed tag_uid and falls through to create a new spool.
"""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.spool import Spool
from backend.app.services.spool_tag_matcher import (
    create_spool_from_tray,
    find_matching_inventory_spool,
    link_tag_to_spool,
)


@pytest.fixture
def spool_match_lock():
    """Create a fresh asyncio.Lock for each test (avoids cross-loop binding)."""
    return asyncio.Lock()


def _make_tray(tag_uid: str, tray_uuid: str) -> dict:
    """Build a BL tray dict with the given tag identifiers."""
    return {
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Basic",
        "tray_color": "FFFFFFFF",
        "tray_id_name": "",
        "tag_uid": tag_uid,
        "tray_uuid": tray_uuid,
        "tray_info_idx": "GFL99",
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
        "tray_weight": "1000",
        "remain": 100,
    }


TRAY_A = _make_tray("AAAAAAAAAAAAAAAA", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1")
TRAY_B = _make_tray("BBBBBBBBBBBBBBBB", "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2")


# -- Race condition: lock serializes find+link+commit -----------------------


@pytest.mark.asyncio
async def test_lock_prevents_double_match_of_same_spool(test_engine, spool_match_lock):
    """Two concurrent sessions must NOT both claim the same untagged spool.

    Simulates the critical section of on_ams_change: find_matching_inventory_spool →
    link_tag_to_spool → commit, protected by _spool_match_lock.

    Without the lock both coroutines would find spool.tag_uid IS NULL, both would
    call link_tag_to_spool, and the second commit would silently overwrite the
    first's tag_uid. With the lock the second coroutine sees the first's committed
    tag_uid and correctly falls through to create_spool_from_tray.
    """
    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Seed one untagged white PLA Basic spool (the target both callers will race for)
    async with session_maker() as seed_db:
        candidate = Spool(
            material="PLA",
            subtype="Basic",
            rgba="FFFFFFFF",
            brand="Bambu Lab",
            label_weight=1000,
            core_weight=250,
            weight_used=0,
        )
        candidate.k_profiles = []
        candidate.assignments = []
        seed_db.add(candidate)
        await seed_db.commit()
        candidate_id = candidate.id

    results: dict[str, int | None] = {}

    async def simulate_ams_match(label: str, tray_data: dict):
        """Simulate the critical section of on_ams_change for one printer."""
        async with session_maker() as db:
            async with spool_match_lock:
                spool = await find_matching_inventory_spool(db, tray_data)
                if spool:
                    link_tag_to_spool(spool, tray_data)
                else:
                    spool = await create_spool_from_tray(db, tray_data)
                await db.commit()
            results[label] = spool.id

    # Run both "printers" concurrently
    await asyncio.gather(
        simulate_ams_match("printer_a", TRAY_A),
        simulate_ams_match("printer_b", TRAY_B),
    )

    # The two printers must have been assigned DIFFERENT spools
    assert results["printer_a"] != results["printer_b"], (
        f"Both printers matched the same spool id={results['printer_a']}! "
        "The lock should have prevented this."
    )

    # Exactly one should have matched the pre-existing candidate
    matched_ids = {results["printer_a"], results["printer_b"]}
    assert candidate_id in matched_ids, (
        "Neither printer matched the pre-seeded inventory spool"
    )

    # Verify each spool has the correct tag_uid from its respective tray
    async with session_maker() as db:
        for label, tray in [("printer_a", TRAY_A), ("printer_b", TRAY_B)]:
            spool = await db.get(Spool, results[label])
            assert spool is not None
            assert spool.tag_uid == tray["tag_uid"], (
                f"{label}: expected tag_uid={tray['tag_uid']}, got {spool.tag_uid}"
            )


@pytest.mark.asyncio
async def test_race_without_lock_both_find_same_spool(test_engine):
    """Demonstrates the race condition when the lock is NOT used.

    Without serialization, both sessions read tag_uid IS NULL before either
    commits, so both match the same candidate spool. This test verifies the
    race exists (proving the lock is necessary) by intentionally skipping it.
    """
    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Seed one untagged white PLA Basic spool
    async with session_maker() as seed_db:
        candidate = Spool(
            material="PLA",
            subtype="Basic",
            rgba="FFFFFFFF",
            brand="Bambu Lab",
            label_weight=1000,
            core_weight=250,
            weight_used=0,
        )
        candidate.k_profiles = []
        candidate.assignments = []
        seed_db.add(candidate)
        await seed_db.commit()
        candidate_id = candidate.id

    # Use an Event to force both coroutines to query simultaneously
    both_queried = asyncio.Event()
    query_count = 0
    results: dict[str, int | None] = {}

    async def simulate_unlocked(label: str, tray_data: dict):
        nonlocal query_count
        async with session_maker() as db:
            spool = await find_matching_inventory_spool(db, tray_data)
            query_count += 1
            # Wait until both have queried before either commits
            if query_count >= 2:
                both_queried.set()
            else:
                await both_queried.wait()

            if spool:
                link_tag_to_spool(spool, tray_data)
            else:
                spool = await create_spool_from_tray(db, tray_data)
            await db.commit()
            results[label] = spool.id

    await asyncio.gather(
        simulate_unlocked("printer_a", TRAY_A),
        simulate_unlocked("printer_b", TRAY_B),
    )

    # WITHOUT the lock, both found the same candidate — demonstrating the bug
    assert results["printer_a"] == results["printer_b"] == candidate_id, (
        "Expected both printers to race for the same spool (demonstrating the bug), "
        f"but got printer_a={results['printer_a']}, printer_b={results['printer_b']}"
    )

    # The last writer wins — the candidate now has one printer's tag, not the other's
    async with session_maker() as db:
        spool = await db.get(Spool, candidate_id)
        # tag_uid is either TRAY_A's or TRAY_B's — one was silently overwritten
        assert spool.tag_uid in (TRAY_A["tag_uid"], TRAY_B["tag_uid"])


# -- Two identical spools in inventory match two identical AMS trays --------


@pytest.mark.asyncio
async def test_two_inventory_spools_matched_to_two_identical_trays(test_engine, spool_match_lock):
    """Two identical inventory spools are correctly distributed across two AMS trays.

    Scenario: the user has two white PLA Basic spools in inventory (both untagged).
    Two printers each load a white PLA Basic spool simultaneously. With the lock,
    each printer claims a different inventory spool — no duplication, no overwrites.
    """
    from datetime import datetime, timedelta

    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    now = datetime.now()

    # Seed two identical untagged white PLA Basic spools
    async with session_maker() as seed_db:
        spool_1 = Spool(
            material="PLA",
            subtype="Basic",
            rgba="FFFFFFFF",
            brand="Bambu Lab",
            label_weight=1000,
            core_weight=250,
            weight_used=0,
            created_at=now - timedelta(days=1),
        )
        spool_1.k_profiles = []
        spool_1.assignments = []
        seed_db.add(spool_1)

        spool_2 = Spool(
            material="PLA",
            subtype="Basic",
            rgba="FFFFFFFF",
            brand="Bambu Lab",
            label_weight=1000,
            core_weight=250,
            weight_used=0,
            created_at=now,
        )
        spool_2.k_profiles = []
        spool_2.assignments = []
        seed_db.add(spool_2)
        await seed_db.commit()
        spool_1_id = spool_1.id
        spool_2_id = spool_2.id

    results: dict[str, int] = {}

    async def simulate_ams_match(label: str, tray_data: dict):
        async with session_maker() as db:
            async with spool_match_lock:
                spool = await find_matching_inventory_spool(db, tray_data)
                if spool:
                    link_tag_to_spool(spool, tray_data)
                else:
                    spool = await create_spool_from_tray(db, tray_data)
                await db.commit()
            results[label] = spool.id

    await asyncio.gather(
        simulate_ams_match("printer_a", TRAY_A),
        simulate_ams_match("printer_b", TRAY_B),
    )

    # Each printer must have claimed a DIFFERENT spool
    assert results["printer_a"] != results["printer_b"], (
        f"Both printers got spool id={results['printer_a']}"
    )

    # Both matched pre-existing inventory spools (no new spool was created)
    inventory_ids = {spool_1_id, spool_2_id}
    assert results["printer_a"] in inventory_ids
    assert results["printer_b"] in inventory_ids

    # Verify each spool has the correct tag from its tray
    async with session_maker() as db:
        for label, tray in [("printer_a", TRAY_A), ("printer_b", TRAY_B)]:
            spool = await db.get(Spool, results[label])
            assert spool.tag_uid == tray["tag_uid"]
            assert spool.tray_uuid == tray["tray_uuid"]
            assert spool.data_origin == "rfid_linked"

    # The FIFO order means the first printer gets the older spool
    assert results["printer_a"] == spool_1_id, "First printer should get the older spool (FIFO)"
    assert results["printer_b"] == spool_2_id, "Second printer should get the newer spool"


@pytest.mark.asyncio
async def test_two_trays_only_one_inventory_spool_second_creates(test_engine, spool_match_lock):
    """One inventory spool + two identical trays: first matches, second auto-creates.

    Ensures the lock causes the second caller to fall through to create_spool_from_tray
    when inventory is exhausted, rather than erroring or matching the now-tagged spool.
    """
    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as seed_db:
        existing = Spool(
            material="PLA",
            subtype="Basic",
            rgba="FFFFFFFF",
            brand="Bambu Lab",
            label_weight=1000,
            core_weight=250,
            weight_used=0,
        )
        existing.k_profiles = []
        existing.assignments = []
        seed_db.add(existing)
        await seed_db.commit()
        existing_id = existing.id

    results: dict[str, int] = {}

    async def simulate_ams_match(label: str, tray_data: dict):
        async with session_maker() as db:
            async with spool_match_lock:
                spool = await find_matching_inventory_spool(db, tray_data)
                if spool:
                    link_tag_to_spool(spool, tray_data)
                else:
                    spool = await create_spool_from_tray(db, tray_data)
                await db.commit()
            results[label] = spool.id

    await asyncio.gather(
        simulate_ams_match("printer_a", TRAY_A),
        simulate_ams_match("printer_b", TRAY_B),
    )

    assert results["printer_a"] != results["printer_b"]

    # One matched the existing spool, the other created a new one
    matched_existing = [l for l, sid in results.items() if sid == existing_id]
    created_new = [l for l, sid in results.items() if sid != existing_id]
    assert len(matched_existing) == 1, "Exactly one printer should match the existing spool"
    assert len(created_new) == 1, "Exactly one printer should create a new spool"

    # The auto-created spool should have rfid_auto origin and the correct tag
    async with session_maker() as db:
        new_spool = await db.get(Spool, results[created_new[0]])
        assert new_spool.data_origin == "rfid_auto"
        tray = TRAY_A if created_new[0] == "printer_a" else TRAY_B
        assert new_spool.tag_uid == tray["tag_uid"]
