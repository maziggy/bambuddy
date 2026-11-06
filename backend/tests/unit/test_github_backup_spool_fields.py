"""The spool collector's field whitelist for Git backup (#2870).

``_collect_spools`` builds an explicit dict per spool, so anything missing
from it is silently absent from the backup and therefore lost on restore.
The owner's own bookkeeping — purchasing number (#2870), category and
low-stock override (#729), free-text storage — belongs in the file.
"""

import pytest

from backend.app.models.spool import Spool
from backend.app.services.github_backup import GitHubBackupService


@pytest.mark.asyncio
async def test_collects_the_owners_own_bookkeeping_fields(db_session):
    db_session.add(
        Spool(
            material="PLA",
            material_number="15",
            category="Production",
            low_stock_threshold_pct=40,
            storage_location="Shelf B",
        )
    )
    await db_session.commit()

    files: dict = {}
    await GitHubBackupService()._collect_spools(db_session, files)

    entry = files["spools/inventory.json"]["spools"][0]
    assert entry["material_number"] == "15"
    assert entry["category"] == "Production"
    assert entry["low_stock_threshold_pct"] == 40
    assert entry["storage_location"] == "Shelf B"


@pytest.mark.asyncio
async def test_location_id_is_left_out(db_session):
    """The locations table is not backed up, so the ID has nothing to mean."""
    db_session.add(Spool(material="PLA"))
    await db_session.commit()

    files: dict = {}
    await GitHubBackupService()._collect_spools(db_session, files)

    assert "location_id" not in files["spools/inventory.json"]["spools"][0]


@pytest.mark.asyncio
async def test_collects_the_drying_record(db_session):
    """#2863 — written with str(), the form _parse_dt reads back."""
    from datetime import datetime

    db_session.add(
        Spool(material="PLA", last_dried_at=datetime(2026, 10, 6, 12, 30), last_dried_temp=55, last_dried_hours=7.5)
    )
    await db_session.commit()

    files: dict = {}
    await GitHubBackupService()._collect_spools(db_session, files)

    entry = files["spools/inventory.json"]["spools"][0]
    assert entry["last_dried_at"] == "2026-10-06 12:30:00"
    assert entry["last_dried_temp"] == 55
    assert entry["last_dried_hours"] == 7.5


@pytest.mark.asyncio
async def test_collects_the_total_consumed_reset_point(db_session):
    """WU-7: without it, a restore lost every "Reset Total Consumed"."""
    db_session.add(Spool(material="PLA", weight_used=500, weight_used_baseline=300))
    await db_session.commit()

    files: dict = {}
    await GitHubBackupService()._collect_spools(db_session, files)

    assert files["spools/inventory.json"]["spools"][0]["weight_used_baseline"] == 300


@pytest.mark.asyncio
async def test_collects_when_the_spool_was_last_used(db_session):
    """LU-4: without it, every spool read as never used after a restore."""
    from datetime import datetime

    db_session.add(Spool(material="PLA", last_used=datetime(2026, 10, 18, 9, 15)))
    await db_session.commit()

    files: dict = {}
    await GitHubBackupService()._collect_spools(db_session, files)

    assert files["spools/inventory.json"]["spools"][0]["last_used"] == "2026-10-18 09:15:00"
