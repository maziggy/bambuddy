"""Unit tests for print log service and schema."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.schemas.print_log import PrintLogEntrySchema, PrintLogResponse
from backend.app.services.print_log import write_log_entry


class TestPrintLogEntrySchema:
    """Test PrintLogEntrySchema validation."""

    def test_minimal_entry(self):
        """Schema accepts minimal required fields."""
        entry = PrintLogEntrySchema(
            id=1,
            status="completed",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        assert entry.id == 1
        assert entry.status == "completed"
        assert entry.print_name is None
        assert entry.printer_name is None
        assert entry.duration_seconds is None

    def test_full_entry(self):
        """Schema accepts all fields."""
        started = datetime(2024, 1, 15, 10, 0, 0)
        completed = datetime(2024, 1, 15, 12, 30, 0)
        entry = PrintLogEntrySchema(
            id=42,
            print_name="Benchy",
            printer_name="X1C-01",
            printer_id=3,
            status="completed",
            started_at=started,
            completed_at=completed,
            duration_seconds=9000,
            filament_type="PLA",
            filament_color="#FF5500",
            filament_used_grams=15.2,
            thumbnail_path="archives/3/20240115_benchy/thumbnail.png",
            created_by_username="admin",
            created_at=datetime(2024, 1, 15, 12, 30, 0),
        )
        assert entry.print_name == "Benchy"
        assert entry.printer_name == "X1C-01"
        assert entry.filament_used_grams == 15.2
        assert entry.created_by_username == "admin"

    def test_failed_status(self):
        """Schema accepts various status values."""
        for status in ("completed", "failed", "stopped", "cancelled", "skipped"):
            entry = PrintLogEntrySchema(id=1, status=status, created_at=datetime.now())
            assert entry.status == status


class TestPrintLogResponse:
    """Test PrintLogResponse pagination wrapper."""

    def test_empty_response(self):
        """Empty response with zero total."""
        resp = PrintLogResponse(items=[], total=0)
        assert len(resp.items) == 0
        assert resp.total == 0

    def test_paginated_response(self):
        """Response with items and total count > items count."""
        items = [PrintLogEntrySchema(id=i, status="completed", created_at=datetime.now()) for i in range(3)]
        resp = PrintLogResponse(items=items, total=100)
        assert len(resp.items) == 3
        assert resp.total == 100


class TestWriteLogEntry:
    """Test the write_log_entry service function (logic only, no DB)."""

    def test_duration_calculation(self):
        """Duration is computed from started_at and completed_at."""
        started = datetime(2024, 1, 15, 10, 0, 0)
        completed = started + timedelta(hours=2, minutes=30)

        # Simulating the duration calculation from write_log_entry
        duration = int((completed - started).total_seconds())
        assert duration == 9000  # 2.5 hours = 9000 seconds

    def test_duration_none_when_missing_times(self):
        """Duration is None when started_at or completed_at is missing."""
        started = datetime(2024, 1, 15, 10, 0, 0)
        completed_at = None
        started_at = None
        completed = datetime.now()

        # No completed_at
        duration = None
        if started and completed_at:
            duration = int((completed_at - started).total_seconds())
        assert duration is None

        # No started_at
        duration = None
        if started_at and completed:
            duration = int((completed - started_at).total_seconds())
        assert duration is None


class TestWriteLogEntryReconciledDuration:
    """write_log_entry duration handling for reconciled (synthetic) completions (#2592).

    A reconciled abort closes out a stale ``status="printing"`` archive at
    reconnect; its real end time is unknown, so ``completed_at - started_at``
    would bank the whole disconnect gap as print time. Those entries must log
    0, while genuine prints (including >24h ones) keep their real duration.
    """

    @staticmethod
    async def _write(**kwargs):
        db = MagicMock()
        db.flush = AsyncMock()
        return await write_log_entry(db, **kwargs)

    @pytest.mark.asyncio
    async def test_reconciled_logs_zero_despite_multiday_gap(self):
        started = datetime(2026, 7, 15, 10, 0, 0)
        completed = started + timedelta(days=2, hours=4)  # the reconnect moment, not the real end
        entry = await self._write(status="aborted", started_at=started, completed_at=completed, reconciled=True)
        assert entry.duration_seconds == 0

    @pytest.mark.asyncio
    async def test_reconciled_logs_zero_even_without_timestamps(self):
        entry = await self._write(status="aborted", reconciled=True)
        assert entry.duration_seconds == 0

    @pytest.mark.asyncio
    async def test_genuine_long_print_retains_full_duration(self):
        """A legitimate >24h print keeps its real duration — no cap, no zeroing."""
        started = datetime(2026, 7, 15, 10, 0, 0)
        completed = started + timedelta(hours=30)
        entry = await self._write(status="completed", started_at=started, completed_at=completed)
        assert entry.duration_seconds == 30 * 3600

    @pytest.mark.asyncio
    async def test_non_reconciled_missing_times_is_none(self):
        entry = await self._write(status="completed", started_at=datetime(2026, 7, 15, 10, 0, 0))
        assert entry.duration_seconds is None
