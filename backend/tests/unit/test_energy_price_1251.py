"""Tests for #1251 — electricity price from Home Assistant, costed hour by hour.

Covers:
- reading a price sensor: units, unusable states, negative prices, fallback,
  and remembering the reading in ``energy_cost_per_kwh``
- the cost arithmetic: each step at the price it starts from, resets skipped
- Statistics: date-range and all-time cost follow the snapshot prices instead
  of multiplying everything by today's price
- per-print cost across a price change, and the fallbacks for prints that
  started before the upgrade or ended on another plug
- the start of a print recording when, from which plug and at what price
- the one-shot backfill of prices onto existing snapshots
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from backend.app.api.routes.archives import _sum_snapshot_deltas
from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.models.smart_plug_energy_snapshot import SmartPlugEnergySnapshot
from backend.app.services import energy_price
from backend.app.services.energy_price import (
    all_time_cost,
    cost_at_average_price,
    cost_of_readings,
    current_price,
    new_snapshot,
    price_from_state,
    print_energy_cost,
    snapshot_cost,
)

T0 = datetime(2026, 9, 1, 0, 0)  # naive UTC, as the column stores it


def _snap(plug_id: int, hours: float, kwh: float, price: float | None) -> SmartPlugEnergySnapshot:
    return SmartPlugEnergySnapshot(
        plug_id=plug_id, recorded_at=T0 + timedelta(hours=hours), lifetime_kwh=kwh, price_per_kwh=price
    )


def _state(value, unit: str | None = "AUD/kWh") -> dict:
    return {"state": value, "attributes": {"unit_of_measurement": unit} if unit else {}}


class TestPriceFromState:
    def test_per_kwh_is_taken_as_is(self):
        assert price_from_state(_state("0.31")) == pytest.approx(0.31)

    def test_per_mwh_is_scaled(self):
        # Nord Pool / ENTSO-E day-ahead sensors are often per MWh.
        assert price_from_state(_state("120.5", "EUR/MWh")) == pytest.approx(0.1205)

    def test_per_wh_is_scaled(self):
        assert price_from_state(_state("0.0003", "€/Wh")) == pytest.approx(0.3)

    def test_no_unit_is_taken_as_per_kwh(self):
        assert price_from_state(_state("0.2", None)) == pytest.approx(0.2)

    @pytest.mark.parametrize("value", ["unknown", "unavailable", "", None, "nan", "inf"])
    def test_unusable_states_give_none(self, value):
        assert price_from_state(_state(value)) is None

    def test_no_state_gives_none(self):
        assert price_from_state(None) is None

    def test_negative_price_is_costed_as_free(self):
        assert price_from_state(_state("-0.05")) == 0.0


class TestCostArithmetic:
    def test_each_step_at_the_price_it_starts_from(self):
        # 2 kWh at 0.10, then 3 kWh at 0.50; the end reading's price is unused.
        kwh, cost = cost_of_readings([(10.0, 0.10), (12.0, 0.50), (15.0, 9.99)], fallback_price=1.0)
        assert kwh == pytest.approx(5.0)
        assert cost == pytest.approx(0.2 + 1.5)

    def test_missing_price_uses_fallback(self):
        kwh, cost = cost_of_readings([(0.0, None), (2.0, None)], fallback_price=0.25)
        assert (kwh, cost) == (pytest.approx(2.0), pytest.approx(0.5))

    def test_counter_reset_step_counts_nothing(self):
        kwh, cost = cost_of_readings([(100.0, 0.1), (2.0, 0.1), (5.0, 0.1)], fallback_price=1.0)
        assert kwh == pytest.approx(3.0)
        assert cost == pytest.approx(0.3)

    def test_average_price_scales_to_the_energy_shown(self):
        assert cost_at_average_price(10.0, 5.0, 2.0, fallback_price=9.0) == pytest.approx(4.0)

    def test_average_price_without_costed_energy_uses_fallback(self):
        assert cost_at_average_price(10.0, 0.0, 0.0, fallback_price=0.3) == pytest.approx(3.0)


class TestCurrentPrice:
    async def _configure_ha(self, db, entity="sensor.amber_price"):
        await set_setting(db, "ha_enabled", "true")
        await set_setting(db, "ha_url", "http://ha.local:8123")
        await set_setting(db, "ha_token", "token")  # pragma: allowlist secret
        await set_setting(db, "energy_price_source", "homeassistant")
        await set_setting(db, "energy_price_ha_entity", entity)
        await set_setting(db, "energy_cost_per_kwh", "0.2")
        await db.commit()

    @pytest.mark.asyncio
    async def test_fixed_source_uses_the_setting(self, db_session):
        await set_setting(db_session, "energy_cost_per_kwh", "0.27")
        await db_session.commit()
        with patch.object(energy_price, "_read_state", AsyncMock()) as read:
            assert await current_price(db_session) == pytest.approx(0.27)
        read.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_when_nothing_is_set(self, db_session):
        assert await current_price(db_session) == pytest.approx(0.15)

    @pytest.mark.asyncio
    async def test_reads_the_sensor_and_remembers_it(self, db_session, monkeypatch):
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        await self._configure_ha(db_session)
        with patch.object(energy_price, "_read_state", AsyncMock(return_value=_state("0.42"))) as read:
            assert await current_price(db_session, remember=True) == pytest.approx(0.42)
        read.assert_awaited_once_with("http://ha.local:8123", "token", "sensor.amber_price")
        assert await get_setting(db_session, "energy_cost_per_kwh") == "0.42"

    @pytest.mark.asyncio
    async def test_print_paths_read_without_writing_a_setting(self, db_session, monkeypatch):
        # A setting written in the print-start transaction could cost the
        # print its energy reading if that write failed.
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        await self._configure_ha(db_session)
        with patch.object(energy_price, "_read_state", AsyncMock(return_value=_state("0.42"))):
            assert await current_price(db_session) == pytest.approx(0.42)
        assert await get_setting(db_session, "energy_cost_per_kwh") == "0.2"

    @pytest.mark.asyncio
    async def test_unreadable_sensor_keeps_the_last_price(self, db_session, monkeypatch):
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        await self._configure_ha(db_session)
        with patch.object(energy_price, "_read_state", AsyncMock(return_value=None)):
            assert await current_price(db_session) == pytest.approx(0.2)
        assert await get_setting(db_session, "energy_cost_per_kwh") == "0.2"

    @pytest.mark.asyncio
    async def test_no_entity_does_not_read(self, db_session, monkeypatch):
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        await self._configure_ha(db_session, entity="")
        with patch.object(energy_price, "_read_state", AsyncMock()) as read:
            assert await current_price(db_session) == pytest.approx(0.2)
        read.assert_not_called()

    @pytest.mark.asyncio
    async def test_home_assistant_switched_off_does_not_read(self, db_session, monkeypatch):
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        await self._configure_ha(db_session)
        await set_setting(db_session, "ha_enabled", "false")
        await db_session.commit()
        with patch.object(energy_price, "_read_state", AsyncMock()) as read:
            assert await current_price(db_session) == pytest.approx(0.2)
        read.assert_not_called()


async def _record(db, plug_id: int, hours: float, kwh: float, price: float) -> SmartPlugEnergySnapshot:
    """Add a snapshot the way the hourly loop does, running totals and all."""
    row = await new_snapshot(
        db, plug_id=plug_id, recorded_at=T0 + timedelta(hours=hours), lifetime_kwh=kwh, price=price
    )
    db.add(row)
    await db.flush()
    return row


class TestNewSnapshot:
    @pytest.mark.asyncio
    async def test_first_snapshot_counts_the_counter_at_its_price(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        row = await _record(db_session, plug.id, 0, 100.0, 0.10)
        assert (row.kwh_to_date, row.cost_to_date) == (pytest.approx(100.0), pytest.approx(10.0))

    @pytest.mark.asyncio
    async def test_each_step_at_the_previous_snapshots_price(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        await _record(db_session, plug.id, 0, 100.0, 0.10)
        await _record(db_session, plug.id, 1, 102.0, 0.50)
        row = await _record(db_session, plug.id, 2, 105.0, 0.20)
        assert row.kwh_to_date == pytest.approx(105.0)
        assert row.cost_to_date == pytest.approx(10.0 + 2 * 0.10 + 3 * 0.50)
        assert row.price_per_kwh == pytest.approx(0.20)

    @pytest.mark.asyncio
    async def test_counter_reset_adds_nothing_and_the_total_carries_on(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        await _record(db_session, plug.id, 0, 100.0, 0.10)
        await _record(db_session, plug.id, 1, 2.0, 0.10)  # device reset
        row = await _record(db_session, plug.id, 2, 5.0, 0.10)
        assert row.kwh_to_date == pytest.approx(103.0)
        assert row.cost_to_date == pytest.approx(10.3)

    @pytest.mark.asyncio
    async def test_continues_from_a_backfilled_row(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        # What the upgrade backfill leaves: totals are the counter at one price.
        db_session.add(
            SmartPlugEnergySnapshot(
                plug_id=plug.id,
                recorded_at=T0,
                lifetime_kwh=50.0,
                price_per_kwh=0.2,
                kwh_to_date=50.0,
                cost_to_date=10.0,
            )
        )
        await db_session.flush()
        row = await _record(db_session, plug.id, 1, 53.0, 0.9)
        assert row.kwh_to_date == pytest.approx(53.0)
        assert row.cost_to_date == pytest.approx(10.0 + 3 * 0.2)

    @pytest.mark.asyncio
    async def test_plugs_keep_their_own_totals(self, db_session, smart_plug_factory):
        a = await smart_plug_factory(name="A")
        b = await smart_plug_factory(name="B")
        await _record(db_session, a.id, 0, 10.0, 0.10)
        await _record(db_session, b.id, 0, 500.0, 1.00)
        row = await _record(db_session, a.id, 1, 11.0, 0.10)
        assert row.kwh_to_date == pytest.approx(11.0)
        assert row.cost_to_date == pytest.approx(1.1)


class TestSnapshotCost:
    @pytest.mark.asyncio
    async def test_empty(self, db_session):
        result = await snapshot_cost(db_session, fallback_price=0.3)
        assert result.kwh == 0.0
        assert result.cost == 0.0

    @pytest.mark.asyncio
    async def test_whole_history(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        await _record(db_session, plug.id, 0, 100.0, 0.10)
        await _record(db_session, plug.id, 1, 102.0, 0.50)
        await _record(db_session, plug.id, 2, 105.0, 0.20)
        await db_session.commit()

        result = await snapshot_cost(db_session, fallback_price=9.0)
        assert result.kwh == pytest.approx(105.0)
        assert result.cost == pytest.approx(100 * 0.10 + 2 * 0.10 + 3 * 0.50)
        assert result.last_lifetime_kwh == pytest.approx(105.0)

    @pytest.mark.asyncio
    async def test_range_covers_the_span_the_energy_figure_counts(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        await _record(db_session, plug.id, 0, 100.0, 0.10)  # baseline: last at or before dt_from
        await _record(db_session, plug.id, 2, 104.0, 0.50)
        await _record(db_session, plug.id, 4, 110.0, 0.20)  # endpoint: last at or before dt_to
        await _record(db_session, plug.id, 6, 150.0, 0.90)
        await db_session.commit()

        dt_from = (T0 + timedelta(hours=1)).replace(tzinfo=timezone.utc)
        dt_to = (T0 + timedelta(hours=5)).replace(tzinfo=timezone.utc)
        result = await snapshot_cost(db_session, fallback_price=9.0, dt_from=dt_from, dt_to=dt_to)
        energy, _ = await _sum_snapshot_deltas(db_session, dt_from=dt_from, dt_to=dt_to)
        assert result.kwh == pytest.approx(energy) == pytest.approx(10.0)
        assert result.cost == pytest.approx(4 * 0.10 + 6 * 0.50)

    @pytest.mark.asyncio
    async def test_range_without_a_baseline_starts_at_the_first_snapshot(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        await _record(db_session, plug.id, 2, 100.0, 0.10)
        await _record(db_session, plug.id, 3, 104.0, 0.50)
        await db_session.commit()

        result = await snapshot_cost(
            db_session, fallback_price=9.0, dt_from=T0.replace(tzinfo=timezone.utc), dt_to=None
        )
        assert result.kwh == pytest.approx(4.0)
        assert result.cost == pytest.approx(0.4)

    @pytest.mark.asyncio
    async def test_plugs_are_summed(self, db_session, smart_plug_factory):
        a = await smart_plug_factory(name="A")
        b = await smart_plug_factory(name="B")
        for plug_id, start, price in ((a.id, 10.0, 0.10), (b.id, 500.0, 1.00)):
            await _record(db_session, plug_id, 0, start, price)
            await _record(db_session, plug_id, 1, start + 1 + (plug_id == b.id), price)
        await db_session.commit()

        result = await snapshot_cost(db_session, fallback_price=9.0, dt_from=T0.replace(tzinfo=timezone.utc))
        assert result.kwh == pytest.approx(3.0)
        assert result.cost == pytest.approx(1 * 0.10 + 2 * 1.00)

    @pytest.mark.asyncio
    async def test_reset_in_pre_upgrade_history_counts_nothing(self, db_session, smart_plug_factory):
        """Backfilled totals are the raw counter, so a reset shows as a drop."""
        plug = await smart_plug_factory(name="A")
        for hours, kwh in ((0, 1000.0), (2, 5.0)):
            db_session.add(
                SmartPlugEnergySnapshot(
                    plug_id=plug.id,
                    recorded_at=T0 + timedelta(hours=hours),
                    lifetime_kwh=kwh,
                    price_per_kwh=0.2,
                    kwh_to_date=kwh,
                    cost_to_date=kwh * 0.2,
                )
            )
        await db_session.commit()

        result = await snapshot_cost(db_session, fallback_price=9.0, dt_from=(T0 + timedelta(hours=1)))
        assert (result.kwh, result.cost) == (0.0, 0.0)

    @pytest.mark.asyncio
    async def test_row_without_totals_falls_back_to_its_counter(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        db_session.add(SmartPlugEnergySnapshot(plug_id=plug.id, recorded_at=T0, lifetime_kwh=4.0))
        await db_session.commit()

        result = await snapshot_cost(db_session, fallback_price=0.25)
        assert (result.kwh, result.cost) == (pytest.approx(4.0), pytest.approx(1.0))


class TestAllTimeCost:
    @pytest.mark.asyncio
    async def test_history_keeps_its_price_when_the_price_changes(self, db_session, smart_plug_factory):
        """The reported problem: today's price must not re-cost past energy."""
        plug = await smart_plug_factory(name="A")
        await _record(db_session, plug.id, 0, 100.0, 0.10)
        await _record(db_session, plug.id, 1, 110.0, 0.10)
        await db_session.commit()

        # Live counter 112: 110 recorded at 0.10, 2 since the last snapshot at
        # today's 0.50.
        cost = await all_time_cost(db_session, live_total_kwh=112.0, price_now=0.50)
        assert cost == pytest.approx(110 * 0.10 + 2 * 0.50)

    @pytest.mark.asyncio
    async def test_no_snapshots_uses_the_price_now(self, db_session):
        assert await all_time_cost(db_session, live_total_kwh=20.0, price_now=0.3) == pytest.approx(6.0)

    @pytest.mark.asyncio
    async def test_live_counter_below_the_history_is_costed_at_its_average_price(self, db_session, smart_plug_factory):
        # The counter was reset since the last snapshot: the energy shown is
        # the live counter, at the average price of the history.
        plug = await smart_plug_factory(name="A")
        await _record(db_session, plug.id, 0, 100.0, 0.10)
        await _record(db_session, plug.id, 1, 200.0, 0.30)
        await db_session.commit()

        cost = await all_time_cost(db_session, live_total_kwh=50.0, price_now=9.0)
        assert cost == pytest.approx(50 * (10.0 + 10.0) / 200.0)


class TestPrintEnergyCost:
    @pytest.mark.asyncio
    async def test_price_change_during_the_print(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        # Started at 50 kWh / 0.10 half an hour before the 1 h snapshot.
        db_session.add_all([_snap(plug.id, 1, 51.0, 0.40), _snap(plug.id, 2, 53.0, 0.05)])
        # Snapshots from before the print must not count.
        db_session.add(_snap(plug.id, 0, 49.0, 9.0))
        await db_session.commit()

        cost = await print_energy_cost(
            db_session,
            plug_id=plug.id,
            start_at=T0 + timedelta(minutes=30),
            start_kwh=50.0,
            start_price=0.10,
            end_kwh=54.0,
            end_plug_id=plug.id,
            price_now=0.05,
        )
        # 1 kWh at 0.10, 2 at 0.40, 1 at 0.05 — not 4 kWh at the end price 0.05.
        assert cost == pytest.approx(0.10 + 0.80 + 0.05)

    @pytest.mark.asyncio
    async def test_short_print_without_snapshots_uses_the_start_price(self, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        cost = await print_energy_cost(
            db_session,
            plug_id=plug.id,
            start_at=T0,
            start_kwh=10.0,
            start_price=0.30,
            end_kwh=10.5,
            end_plug_id=plug.id,
            price_now=0.90,
        )
        assert cost == pytest.approx(0.15)

    @pytest.mark.asyncio
    async def test_print_started_before_the_upgrade_uses_the_price_now(self, db_session):
        cost = await print_energy_cost(
            db_session,
            plug_id=None,
            start_at=None,
            start_kwh=10.0,
            start_price=None,
            end_kwh=12.0,
            end_plug_id=1,
            price_now=0.25,
        )
        assert cost == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_end_reading_from_another_plug_uses_the_price_now(self, db_session):
        cost = await print_energy_cost(
            db_session,
            plug_id=1,
            start_at=T0,
            start_kwh=10.0,
            start_price=0.10,
            end_kwh=12.0,
            end_plug_id=2,
            price_now=0.25,
        )
        assert cost == pytest.approx(0.5)


class TestStatsEndpoint:
    @pytest.mark.asyncio
    async def test_date_range_cost_uses_snapshot_prices(self, async_client, db_session, smart_plug_factory):
        plug = await smart_plug_factory(name="A")
        # T0 is 2026-09-01 00:00 UTC.
        await _record(db_session, plug.id, 23, 100.0, 0.10)  # 2026-09-01 23:00, baseline
        await _record(db_session, plug.id, 30, 104.0, 0.50)
        await _record(db_session, plug.id, 36, 106.0, 0.50)
        await set_setting(db_session, "energy_tracking_mode", "total")
        await set_setting(db_session, "energy_cost_per_kwh", "3.0")  # today's price, must not apply
        await db_session.commit()

        response = await async_client.get("/api/v1/archives/stats?date_from=2026-09-02&date_to=2026-09-02")
        assert response.status_code == 200
        data = response.json()
        assert data["total_energy_kwh"] == pytest.approx(6.0)
        assert data["total_energy_cost"] == pytest.approx(4 * 0.10 + 2 * 0.50)


class TestRecordEnergyStart:
    @pytest.mark.asyncio
    async def test_records_when_where_and_at_what_price(
        self, db_session, printer_factory, smart_plug_factory, archive_factory
    ):
        printer = await printer_factory()
        plug = await smart_plug_factory(name="Power", printer_id=printer.id)
        archive = await archive_factory(printer.id)
        await set_setting(db_session, "energy_cost_per_kwh", "0.33")
        await db_session.commit()

        from backend.app.main import _record_energy_start

        with patch("backend.app.main._get_plug_energy", AsyncMock(return_value={"total": 7.5})):
            assert await _record_energy_start(archive, printer.id, db_session) is True

        assert archive.energy_start_kwh == 7.5
        assert archive.energy_start_plug_id == plug.id
        assert archive.energy_start_price == pytest.approx(0.33)
        assert archive.energy_start_at is not None
        assert archive.energy_start_at.tzinfo is None


class TestSnapshotPriceBackfill:
    @pytest.mark.asyncio
    async def test_fills_existing_rows_once(self, test_engine, db_session, smart_plug_factory):
        from backend.app.core.database import _backfill_snapshot_prices

        plug_id = (await smart_plug_factory(name="A")).id
        db_session.add_all([_snap(plug_id, 0, 1.0, None), _snap(plug_id, 1, 2.0, None)])
        await set_setting(db_session, "energy_cost_per_kwh", "0.28")
        await db_session.commit()

        async with test_engine.begin() as conn:
            await _backfill_snapshot_prices(conn)

        db_session.expire_all()
        rows = (
            await db_session.execute(
                select(
                    SmartPlugEnergySnapshot.price_per_kwh,
                    SmartPlugEnergySnapshot.kwh_to_date,
                    SmartPlugEnergySnapshot.cost_to_date,
                ).order_by(SmartPlugEnergySnapshot.recorded_at)
            )
        ).all()
        assert [tuple(r) for r in rows] == [
            (pytest.approx(0.28), pytest.approx(1.0), pytest.approx(0.28)),
            (pytest.approx(0.28), pytest.approx(2.0), pytest.approx(0.56)),
        ]

        # A second run (next boot) leaves a NULL alone: the flag is set.
        db_session.add(_snap(plug_id, 2, 3.0, None))
        await db_session.commit()
        async with test_engine.begin() as conn:
            await _backfill_snapshot_prices(conn)
            nulls = (
                await conn.execute(text("SELECT COUNT(*) FROM smart_plug_energy_snapshots WHERE price_per_kwh IS NULL"))
            ).scalar_one()
        assert nulls == 1


class TestSettingsRoute:
    @pytest.mark.asyncio
    async def test_saves_and_returns_the_price_source(self, async_client):
        response = await async_client.put(
            "/api/v1/settings/",
            json={"energy_price_source": "homeassistant", "energy_price_ha_entity": "sensor.amber_price"},
        )
        assert response.status_code == 200
        assert response.json()["energy_price_source"] == "homeassistant"
        assert response.json()["energy_price_ha_entity"] == "sensor.amber_price"

    @pytest.mark.asyncio
    async def test_defaults_to_fixed(self, async_client):
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["energy_price_source"] == "fixed"
        assert response.json()["energy_price_ha_entity"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["tibber", None])
    async def test_refuses_an_unknown_or_null_source(self, async_client, value):
        response = await async_client.put("/api/v1/settings/", json={"energy_price_source": value})
        assert response.status_code == 422
        # A stored "None" would have broken every later settings read.
        assert (await async_client.get("/api/v1/settings/")).status_code == 200

    @pytest.mark.asyncio
    async def test_reads_the_price_only_when_its_source_changes(self, async_client):
        body = {"energy_price_source": "homeassistant", "energy_price_ha_entity": "sensor.p", "currency": "AUD"}
        with patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.3)) as read:
            await async_client.put("/api/v1/settings/", json=body)
            assert read.await_count == 1
            # The page saves every field on each change; same source, no read.
            await async_client.put("/api/v1/settings/", json={**body, "currency": "EUR"})
            assert read.await_count == 1
            await async_client.put("/api/v1/settings/", json={**body, "energy_price_ha_entity": "sensor.q"})
            assert read.await_count == 2


class TestSnapshotCapture:
    @pytest.mark.asyncio
    async def test_every_row_of_a_tick_carries_the_price(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from backend.app.services import smart_plug_manager as manager_module

        added: list[SmartPlugEnergySnapshot] = []

        class FakeSession:
            async def execute(self, *_a, **_kw):
                result = MagicMock()
                result.scalars.return_value.all.return_value = [
                    SimpleNamespace(id=1, plug_type="rest", enabled=True),
                    SimpleNamespace(id=2, plug_type="tasmota", enabled=True),
                ]
                # No earlier snapshot for either plug.
                result.scalar_one_or_none.return_value = None
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
            patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.37)) as read,
            patch.object(
                manager,
                "get_service_for_plug",
                new=AsyncMock(return_value=SimpleNamespace(get_energy=AsyncMock(return_value={"total": 5.0}))),
            ),
        ):
            await manager._capture_energy_snapshots()

        read.assert_awaited_once()
        assert read.await_args.kwargs == {"remember": True}
        assert [row.price_per_kwh for row in added] == [0.37, 0.37]
        assert [row.cost_to_date for row in added] == [pytest.approx(5.0 * 0.37)] * 2


class TestEnergyMultiplierRange:
    """myStrom reports watt-seconds: 1 Ws = 1/3,600,000 kWh (#1251 thread)."""

    WS_TO_KWH = 1 / 3_600_000

    @pytest.mark.parametrize(
        "field",
        [
            "mqtt_power_multiplier",
            "mqtt_energy_multiplier",
            "rest_power_multiplier",
            "rest_energy_multiplier",
            "rest_energy_total_multiplier",
        ],
    )
    def test_watt_seconds_conversion_is_accepted(self, field):
        from backend.app.schemas.smart_plug import SmartPlugCreate, SmartPlugUpdate

        plug = SmartPlugCreate(
            name="myStrom", plug_type="rest", rest_on_url="http://plug/relay?state=1", **{field: self.WS_TO_KWH}
        )
        assert getattr(plug, field) == pytest.approx(self.WS_TO_KWH)
        assert getattr(SmartPlugUpdate(**{field: self.WS_TO_KWH}), field) == pytest.approx(self.WS_TO_KWH)

    def test_zero_is_still_refused(self):
        from pydantic import ValidationError

        from backend.app.schemas.smart_plug import SmartPlugCreate

        with pytest.raises(ValidationError):
            SmartPlugCreate(
                name="myStrom",
                plug_type="rest",
                rest_on_url="http://plug/relay?state=1",
                rest_energy_total_multiplier=0,
            )


class TestStrayStoredSource:
    @pytest.mark.asyncio
    async def test_settings_still_load_and_the_price_is_fixed(self, async_client, db_session):
        # Settings restored from a backup are written as stored, unchecked.
        await set_setting(db_session, "energy_price_source", "tibber")
        await set_setting(db_session, "energy_cost_per_kwh", "0.2")
        await db_session.commit()

        assert (await async_client.get("/api/v1/settings/")).status_code == 200
        with patch.object(energy_price, "_read_state", AsyncMock()) as read:
            assert await current_price(db_session) == pytest.approx(0.2)
        read.assert_not_called()
