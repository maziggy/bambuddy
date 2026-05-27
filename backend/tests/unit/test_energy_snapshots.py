"""Tests for #941 — date-range energy in total consumption mode + restart-resilient per-print tracking.

Covers:
- `_sum_snapshot_deltas()`: correct (endpoint - baseline) arithmetic
- Counter-reset clamp, warming-up flag, missing-endpoint handling
- Restart resilience: per-print `energy_start_kwh` persists across a
  "simulated restart" (new session/process), so the print-end handler can
  still compute the delta.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.api.routes.archives import _sum_snapshot_deltas
from backend.app.models.archive import PrintArchive
from backend.app.models.smart_plug_energy_snapshot import SmartPlugEnergySnapshot


def _snap(plug_id: int, recorded_at: datetime, kwh: float) -> SmartPlugEnergySnapshot:
    return SmartPlugEnergySnapshot(plug_id=plug_id, recorded_at=recorded_at, lifetime_kwh=kwh)


class TestSumSnapshotDeltas:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_plugs(self, db_session):
        total, warming = await _sum_snapshot_deltas(db_session, dt_from=None, dt_to=None)
        assert total == 0.0
        assert warming is False

    @pytest.mark.asyncio
    async def test_simple_delta_with_baseline_and_endpoint(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        # Baseline sits before the range, endpoint inside the range.
        t0 = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        db_session.add(_snap(plug.id, t0, 100.0))  # baseline
        db_session.add(_snap(plug.id, t0 + timedelta(days=2), 115.0))  # endpoint
        await db_session.commit()

        range_start = t0 + timedelta(days=1)
        range_end = t0 + timedelta(days=3)
        total, warming = await _sum_snapshot_deltas(db_session, dt_from=range_start, dt_to=range_end)

        assert total == pytest.approx(15.0)
        assert warming is False

    @pytest.mark.asyncio
    async def test_warming_up_when_no_baseline_before_range(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        # All snapshots happen AFTER range_start — simulates fresh upgrade.
        t0 = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        db_session.add(_snap(plug.id, t0, 500.0))  # first snapshot ever (fallback baseline)
        db_session.add(_snap(plug.id, t0 + timedelta(hours=6), 502.0))  # endpoint
        await db_session.commit()

        range_start = datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc)  # before any snapshot
        range_end = datetime(2026, 4, 10, 23, 59, tzinfo=timezone.utc)

        total, warming = await _sum_snapshot_deltas(db_session, dt_from=range_start, dt_to=range_end)

        assert total == pytest.approx(2.0)  # 502 - 500
        assert warming is True

    @pytest.mark.asyncio
    async def test_counter_reset_is_clamped_to_zero(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        t0 = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        db_session.add(_snap(plug.id, t0, 1000.0))  # baseline
        # Counter reset — endpoint is lower than baseline (plug replaced, firmware reset, ...)
        db_session.add(_snap(plug.id, t0 + timedelta(days=2), 5.0))
        await db_session.commit()

        total, warming = await _sum_snapshot_deltas(
            db_session,
            dt_from=t0 + timedelta(days=1),
            dt_to=t0 + timedelta(days=3),
        )

        assert total == 0.0
        assert warming is False

    @pytest.mark.asyncio
    async def test_multiple_plugs_are_summed(self, db_session, smart_plug_factory):
        plug1 = await smart_plug_factory(name="A", ip_address="10.0.0.1")
        plug2 = await smart_plug_factory(name="B", ip_address="10.0.0.2")
        t0 = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        # plug1: 100 -> 110  (delta 10)
        db_session.add(_snap(plug1.id, t0, 100.0))
        db_session.add(_snap(plug1.id, t0 + timedelta(days=2), 110.0))
        # plug2:  50 ->  55  (delta 5)
        db_session.add(_snap(plug2.id, t0, 50.0))
        db_session.add(_snap(plug2.id, t0 + timedelta(days=2), 55.0))
        await db_session.commit()

        total, warming = await _sum_snapshot_deltas(
            db_session,
            dt_from=t0 + timedelta(days=1),
            dt_to=t0 + timedelta(days=3),
        )

        assert total == pytest.approx(15.0)
        assert warming is False

    @pytest.mark.asyncio
    async def test_plug_with_no_snapshots_signals_warming(self, db_session, smart_plug_factory):
        # Plug exists but never snapshotted (yet).
        await smart_plug_factory(name="A")
        t0 = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)

        total, warming = await _sum_snapshot_deltas(
            db_session,
            dt_from=t0,
            dt_to=t0 + timedelta(days=1),
        )

        assert total == 0.0
        assert warming is True

    @pytest.mark.asyncio
    async def test_endpoint_picks_last_snapshot_at_or_before_range_end(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        t0 = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        db_session.add(_snap(plug.id, t0, 100.0))  # baseline
        db_session.add(_snap(plug.id, t0 + timedelta(days=1), 105.0))  # inside range
        db_session.add(_snap(plug.id, t0 + timedelta(days=5), 130.0))  # AFTER range_end — must be ignored
        await db_session.commit()

        total, _warming = await _sum_snapshot_deltas(
            db_session,
            dt_from=t0 + timedelta(hours=12),
            dt_to=t0 + timedelta(days=2),
        )

        # Baseline is last snapshot <= range_start → the t0 one at 100
        # Endpoint is last snapshot <= range_end → the day-1 one at 105
        assert total == pytest.approx(5.0)


class TestPerPrintRestartResilience:
    """#941: per-print energy tracking survives a mid-print backend restart.

    The critical change: `energy_start_kwh` is stored on the archive row, not
    in an in-memory dict. A new DB session should still be able to read it.
    """

    @pytest.mark.asyncio
    async def test_energy_start_kwh_persists_to_db(self, db_session, printer_factory):
        printer = await printer_factory()
        archive = PrintArchive(
            printer_id=printer.id,
            filename="resilience.gcode.3mf",
            print_name="Resilience",
            file_path="archives/test/resilience.gcode.3mf",
            file_size=1000,
            status="printing",
            energy_start_kwh=123.456,
        )
        db_session.add(archive)
        await db_session.commit()
        archive_id = archive.id

        # Drop the ORM reference and re-fetch, simulating a fresh session
        # (the situation we'd be in after a backend restart).
        db_session.expunge_all()
        result = await db_session.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
        reloaded = result.scalar_one()

        assert reloaded.energy_start_kwh == pytest.approx(123.456)

    @pytest.mark.asyncio
    async def test_energy_kwh_delta_computes_from_persisted_start(self, db_session, printer_factory):
        """Simulates the background energy calc reading from DB instead of a dict."""
        printer = await printer_factory()
        archive = PrintArchive(
            printer_id=printer.id,
            filename="delta.gcode.3mf",
            print_name="Delta",
            file_path="archives/test/delta.gcode.3mf",
            file_size=1000,
            status="completed",
            energy_start_kwh=200.0,
        )
        db_session.add(archive)
        await db_session.commit()

        # Emulate the end-of-print calculation: plug currently reads 203.4 kWh
        ending_kwh = 203.4
        assert archive.energy_start_kwh is not None
        archive.energy_kwh = round(ending_kwh - archive.energy_start_kwh, 4)
        archive.energy_cost = round(archive.energy_kwh * 0.30, 3)
        await db_session.commit()

        # Re-read and verify
        db_session.expunge_all()
        result = await db_session.execute(select(PrintArchive).where(PrintArchive.id == archive.id))
        reloaded = result.scalar_one()
        assert reloaded.energy_kwh == pytest.approx(3.4)
        assert reloaded.energy_cost == pytest.approx(1.02)
