"""Numbers restored from a GitHub backup are checked like numbers typed in.

Archive and spool-usage restore copied grams, costs, energy and print time
straight from the backup: a hand-edited or damaged backup could put negative or
absurd values (or text) into the filament and cost totals, which the archive
editor itself refuses. Each such field now has to be a finite number of at
least 0 (grams at most 100000, as the editor allows); anything else is left
empty, said once per category.
"""

import math

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.spool_usage_history import SpoolUsageHistory
from backend.app.services.github_restore import _CategoryTally
from backend.tests.unit.test_github_restore import TestRestoreArchives, _codes, _service

pytestmark = pytest.mark.asyncio


def _entry(**numbers):
    return TestRestoreArchives()._archive_entry(**numbers)


async def _restore_archive(db_session, entry):
    tally = _CategoryTally()
    await _service()._restore_archives(db_session, {"archives": [entry]}, False, tally, {})
    await db_session.commit()
    return (await db_session.execute(select(PrintArchive))).scalar_one(), tally


async def test_good_numbers_come_through(db_session):
    row, tally = await _restore_archive(
        db_session,
        _entry(
            filament_used_grams=45.2,
            cost=1.13,
            energy_kwh=0.4,
            energy_cost=0.12,
            wear_cost=0.05,
            print_time_seconds=3600,
        ),
    )

    assert (
        row.filament_used_grams,
        row.cost,
        row.energy_kwh,
        row.energy_cost,
        row.wear_cost,
        row.print_time_seconds,
    ) == (
        45.2,
        1.13,
        0.4,
        0.12,
        0.05,
        3600,
    )
    assert "archivesNumbersDropped" not in _codes(tally)


@pytest.mark.parametrize(
    "bad",
    [
        {"filament_used_grams": -5},
        {"filament_used_grams": 1e9},
        {"cost": "12"},
        {"energy_kwh": math.inf},
        {"energy_cost": float("nan")},
        {"wear_cost": True},
        {"print_time_seconds": -1},
    ],
    ids=["negative-grams", "absurd-grams", "text-cost", "infinite-energy", "nan-cost", "bool-wear", "negative-time"],
)
async def test_a_bad_number_is_left_empty_and_said_once(db_session, bad):
    row, tally = await _restore_archive(db_session, _entry(**bad))

    (field,) = bad
    assert getattr(row, field) is None
    assert _codes(tally).count("archivesNumbersDropped") == 1


async def test_a_bad_quantity_becomes_one(db_session):
    row, _ = await _restore_archive(db_session, _entry(quantity="many"))

    assert row.quantity == 1


async def test_usage_numbers_are_checked_too(db_session):
    tally = _CategoryTally()
    usage = {
        "usage_history": [
            {
                "spool_id": None,
                "weight_used": -3,
                "percent_used": 500,
                "cost": "abc",
                "created_at": "2026-02-01 09:00:00",
            }
        ]
    }

    await _service()._restore_spools(db_session, {"spools": []}, usage, False, tally, {})
    await db_session.commit()

    row = (await db_session.execute(select(SpoolUsageHistory))).scalar_one()
    assert (row.weight_used, row.cost) == (0, None)
    assert "usageNumbersDropped" in _codes(tally)


async def test_a_quantity_of_zero_is_kept(db_session):
    """A ruined plate (#3051): counting it as a print again would be wrong."""
    row, _ = await _restore_archive(db_session, _entry(quantity=0))

    assert row.quantity == 0


async def test_a_huge_integer_is_left_empty_not_a_crash(db_session):
    row, tally = await _restore_archive(db_session, _entry(print_time_seconds=10**400, cost=10**400))

    assert (row.print_time_seconds, row.cost) == (None, None)
    assert "archivesNumbersDropped" in _codes(tally)


async def test_an_integer_too_large_for_the_column_is_left_empty(db_session):
    row, _ = await _restore_archive(db_session, _entry(print_time_seconds=2**31))

    assert row.print_time_seconds is None
