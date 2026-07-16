"""Today / Yesterday derived from a plug's lifetime energy counter (#2539).

The reporter's Shelly Plug S Gen3 reports one number, ``aenergy.total``, and it
only ever climbs. Bambuddy filed that under "today", so Today never reset at
midnight and Yesterday and Total stayed at zero forever. These tests pin the
arithmetic that replaces it, and the two ways it can legitimately have no answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models.smart_plug import SmartPlug
from backend.app.models.smart_plug_energy_snapshot import SmartPlugEnergySnapshot
from backend.app.services.plug_energy_history import derive_today_yesterday, fill_derived_energy
from backend.app.utils.local_time import local_day_start, to_naive_utc

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def berlin(monkeypatch):
    """The reporter's timezone. A UTC day boundary would roll his Today over at
    02:00 wall-clock, which is the whole reason local_day_start exists.
    """
    monkeypatch.setenv("TZ", "Europe/Berlin")


async def _plug(db) -> SmartPlug:
    plug = SmartPlug(
        name="Shelly",
        plug_type="rest",
        rest_energy_total_path="aenergy.total",
        rest_energy_total_multiplier=0.001,
    )
    db.add(plug)
    await db.commit()
    await db.refresh(plug)
    return plug


async def _snapshot(db, plug_id: int, when: datetime, kwh: float) -> None:
    db.add(
        SmartPlugEnergySnapshot(
            plug_id=plug_id,
            recorded_at=to_naive_utc(when),
            lifetime_kwh=kwh,
        )
    )
    await db.commit()


async def test_derives_today_and_yesterday_from_the_counter(db_session):
    plug = await _plug(db_session)
    now = datetime.now(timezone.utc)

    await _snapshot(db_session, plug.id, local_day_start(now, days_ago=1), 100.0)
    await _snapshot(db_session, plug.id, local_day_start(now, days_ago=0), 102.0)

    today, yesterday = await derive_today_yesterday(db_session, plug.id, live_total_kwh=103.5)

    assert today == pytest.approx(1.5)  # counter now, minus this midnight
    assert yesterday == pytest.approx(2.0)  # this midnight, minus the one before


async def test_yesterday_is_none_until_two_midnights_have_passed(db_session):
    """A day-old install can say what today used, but has nothing to compare
    yesterday against. Better an empty field than a fabricated one.
    """
    plug = await _plug(db_session)
    now = datetime.now(timezone.utc)
    await _snapshot(db_session, plug.id, local_day_start(now, days_ago=0), 102.0)

    today, yesterday = await derive_today_yesterday(db_session, plug.id, live_total_kwh=103.5)

    assert today == pytest.approx(1.5)
    assert yesterday is None


async def test_nothing_derivable_before_the_first_midnight(db_session):
    plug = await _plug(db_session)
    now = datetime.now(timezone.utc)
    # Snapshot taken this morning, after midnight — no baseline for the day.
    await _snapshot(db_session, plug.id, now - timedelta(minutes=30), 102.0)

    today, yesterday = await derive_today_yesterday(db_session, plug.id, live_total_kwh=103.5)

    assert today is None
    assert yesterday is None


async def test_counter_reset_reports_nothing_rather_than_a_negative(db_session):
    """A factory reset zeroes a Shelly's aenergy.total. The delta goes negative,
    and "-101.6 kWh used today" is worse than a blank.
    """
    plug = await _plug(db_session)
    now = datetime.now(timezone.utc)
    await _snapshot(db_session, plug.id, local_day_start(now, days_ago=1), 100.0)
    await _snapshot(db_session, plug.id, local_day_start(now, days_ago=0), 102.0)

    today, _ = await derive_today_yesterday(db_session, plug.id, live_total_kwh=0.4)

    assert today is None


async def test_snapshots_from_other_plugs_are_not_borrowed(db_session):
    plug = await _plug(db_session)
    other = SmartPlug(name="Other", plug_type="rest")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    now = datetime.now(timezone.utc)
    await _snapshot(db_session, other.id, local_day_start(now, days_ago=0), 50.0)

    today, yesterday = await derive_today_yesterday(db_session, plug.id, live_total_kwh=103.5)

    assert today is None
    assert yesterday is None


class TestFillDerivedEnergy:
    async def test_fills_today_and_yesterday_for_a_lifetime_only_plug(self, db_session):
        plug = await _plug(db_session)
        now = datetime.now(timezone.utc)
        await _snapshot(db_session, plug.id, local_day_start(now, days_ago=1), 100.0)
        await _snapshot(db_session, plug.id, local_day_start(now, days_ago=0), 102.0)

        energy = await fill_derived_energy(db_session, plug.id, {"power": 84.0, "total": 103.5})

        assert energy["today"] == pytest.approx(1.5)
        assert energy["yesterday"] == pytest.approx(2.0)
        assert energy["total"] == 103.5

    async def test_never_overwrites_what_the_device_reported(self, db_session):
        """Tasmota knows its own daily figures. A device that measured the day
        itself beats our hourly-snapshot arithmetic, so it wins.
        """
        plug = await _plug(db_session)
        now = datetime.now(timezone.utc)
        await _snapshot(db_session, plug.id, local_day_start(now, days_ago=1), 100.0)
        await _snapshot(db_session, plug.id, local_day_start(now, days_ago=0), 102.0)

        energy = await fill_derived_energy(db_session, plug.id, {"today": 9.9, "yesterday": 8.8, "total": 103.5})

        assert energy["today"] == 9.9
        assert energy["yesterday"] == 8.8

    async def test_no_lifetime_counter_is_left_alone(self, db_session):
        """A REST plug configured with only a today-path, or an MQTT plug: there
        is nothing to derive from, and its Today already came from the device.
        """
        plug = await _plug(db_session)

        energy = await fill_derived_energy(db_session, plug.id, {"power": 84.0, "today": 1.2})

        assert energy == {"power": 84.0, "today": 1.2}
