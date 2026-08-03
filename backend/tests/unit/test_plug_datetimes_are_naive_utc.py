"""Smart-plug timestamps must be naive UTC (#2539 collateral).

Every ``DateTime`` column in the smart-plug tables is naive, and Bambuddy's
convention is that a naive column holds UTC. The smart-plug code wrote *aware*
datetimes into them anyway. SQLite tolerates that — its bind processor reads the
datetime's fields and drops the offset — so it went unnoticed for a long time.

**asyncpg does not.** It raises ``DataError: invalid input for query argument``,
which meant that on Postgres:

* every energy snapshot capture raised, so the snapshot table stayed empty and
  the Statistics page's date-filtered energy figure was permanently zero;
* every plug status poll raised on ``last_checked``.

Postgres is the setup Bambuddy recommends for multi-printer installs, so this
was not a corner. Both of these tests fail against the pre-#2539 code.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import smart_plug_manager as manager_module
from backend.app.utils.local_time import to_naive_utc, utcnow_naive


def test_utcnow_naive_carries_no_offset():
    now = utcnow_naive()
    assert now.tzinfo is None


def test_to_naive_utc_converts_rather_than_truncates():
    """An aware datetime in another zone must be *converted* to UTC before the
    offset is dropped, not merely stripped — stripping 02:00+02:00 would record
    it as 02:00 UTC, an hour of energy attributed to the wrong day.
    """
    from datetime import datetime, timedelta, timezone

    berlin_summer = timezone(timedelta(hours=2))
    aware = datetime(2026, 7, 11, 2, 30, tzinfo=berlin_summer)

    naive = to_naive_utc(aware)

    assert naive.tzinfo is None
    assert naive == datetime(2026, 7, 11, 0, 30)


def test_to_naive_utc_passes_through_naive_and_none():
    from datetime import datetime

    already = datetime(2026, 7, 11, 12, 0)
    assert to_naive_utc(already) is already
    assert to_naive_utc(None) is None


@pytest.mark.asyncio
async def test_energy_snapshot_is_stamped_with_a_naive_datetime():
    """The regression guard, and the one that actually reproduces the bug.

    Reproducing the real failure needs a live Postgres, which CI has no reason to
    run for this. So catch it one step earlier: intercept the row on its way into
    the session and assert the timestamp carries no offset. An aware value here is
    exactly what asyncpg rejects with DataError, and what SQLite quietly swallows —
    which is why nobody noticed for so long.

    A source scan was tried first and was worthless: the aware ``now`` is assigned
    on one line and used as ``recorded_at`` on another, so grepping for the two
    together sees nothing.
    """
    added: list[object] = []

    class FakeSession:
        async def execute(self, *_a, **_kw):
            result = MagicMock()
            result.scalars.return_value.all.return_value = [SimpleNamespace(id=1, plug_type="rest", enabled=True)]
            return result

        def add(self, obj):
            added.append(obj)

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    manager = manager_module.SmartPlugManager()

    with (
        patch("backend.app.core.database.async_session", FakeSession),
        patch.object(
            manager,
            "get_service_for_plug",
            new=AsyncMock(return_value=SimpleNamespace(get_energy=AsyncMock(return_value={"total": 2.62}))),
        ),
    ):
        await manager._capture_energy_snapshots()

    assert len(added) == 1, "expected one snapshot row to be written"
    recorded_at = added[0].recorded_at
    assert recorded_at.tzinfo is None, (
        "energy snapshot stamped with a timezone-aware datetime. The column is "
        "naive, and asyncpg raises DataError on Postgres — the whole capture "
        "fails and the Statistics energy figure stays at zero. Use utcnow_naive()."
    )
