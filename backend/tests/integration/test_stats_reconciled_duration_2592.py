"""Regression test for reconnect-reconciliation inflating Total Print Time (#2592).

On a farm, a connected-edge reconcile closes out every stale ``status="printing"``
archive as an aborted run whose duration was computed ``completed_at - started_at``
— i.e. the whole multi-day disconnect gap — and the Stats endpoint's fallback
recomputed the same value even when the stored duration was 0/NULL. A single
reconnect could add hundreds of fictitious print hours (reporter @Jostxxl saw
Total Print Time jump from ~1,500h to 3,215h).

The fix stores an explicit 0 for reconciled entries and makes the Stats total
trust that 0 instead of recomputing from the stale timestamps. This test drives
the ``/archives/stats`` endpoint with hand-crafted rows covering the reporter's
scenarios.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient

from backend.app.models.print_log import PrintLogEntry


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stats_total_time_ignores_reconciled_but_keeps_real_runtime(async_client: AsyncClient, db_session):
    base = datetime(2026, 7, 15, 10, 0, 0)
    reconnect = base + timedelta(days=2, hours=4)  # multi-day gap → ~52h each if recomputed

    rows = [
        # Two reconciled aborts for the same printer: duration logged as 0 on
        # purpose (unknown real end time). Their timestamps span days, so the
        # bug would have banked ~52h each (~104h total) via the fallback.
        PrintLogEntry(printer_id=1, status="aborted", started_at=base, completed_at=reconnect, duration_seconds=0),
        PrintLogEntry(printer_id=1, status="aborted", started_at=base, completed_at=reconnect, duration_seconds=0),
        # A genuine >24h print — must be retained in full (no cap, no zeroing).
        PrintLogEntry(
            printer_id=1,
            status="completed",
            started_at=base,
            completed_at=base + timedelta(hours=30),
            duration_seconds=30 * 3600,
        ),
        # A legacy row that never stored a duration — must still fall back to
        # its own (short, legitimate) 2h span.
        PrintLogEntry(
            printer_id=1,
            status="completed",
            started_at=base,
            completed_at=base + timedelta(hours=2),
            duration_seconds=None,
        ),
    ]
    for r in rows:
        db_session.add(r)
    await db_session.commit()

    resp = await async_client.get("/api/v1/archives/stats")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # 30h (genuine) + 2h (legacy fallback) + 0 + 0 (reconciled) = 32.0h.
    # Pre-fix the two reconciled rows would have added ~104h from their stamps.
    assert data["total_print_time_hours"] == 32.0
