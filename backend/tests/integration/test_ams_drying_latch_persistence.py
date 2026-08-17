"""The AMS drying latch has to survive a backend restart (#1802).

Suppression of the high-temperature alarm spans a drying cycle plus the
cool-down after it, which together can run well over twelve hours. Holding that
purely in memory — as the sibling ``_ams_alarm_cooldown`` dict does — meant any
restart partway through resumed alarming about heat the user asked for, so the
latch is stored in the settings table instead.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.main import (
    AMS_DRYING_GRACE_MINUTES,
    AMS_DRYING_LATCH_KEY,
    _load_ams_drying_latch,
    _save_ams_drying_latch,
)
from backend.app.models.settings import Settings


async def _stored_value(db_session) -> str | None:
    result = await db_session.execute(select(Settings).where(Settings.key == AMS_DRYING_LATCH_KEY))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


@pytest.mark.asyncio
class TestAmsDryingLatchPersistence:
    async def test_round_trip_survives_a_reload(self, db_session):
        stamp = datetime.now(timezone.utc) - timedelta(minutes=10)
        await _save_ams_drying_latch(db_session, {"1:0": stamp})
        await db_session.commit()

        # A fresh load is what a restarted backend does on its first pass.
        assert await _load_ams_drying_latch(db_session) == {"1:0": stamp}

    async def test_no_row_created_when_nothing_ever_dries(self, db_session):
        await _save_ams_drying_latch(db_session, {})
        await db_session.commit()
        assert await _stored_value(db_session) is None
        assert await _load_ams_drying_latch(db_session) == {}

    async def test_existing_row_is_updated_not_duplicated(self, db_session):
        first = datetime.now(timezone.utc) - timedelta(minutes=30)
        second = datetime.now(timezone.utc)
        await _save_ams_drying_latch(db_session, {"1:0": first})
        await db_session.commit()
        await _save_ams_drying_latch(db_session, {"1:0": second})
        await db_session.commit()

        result = await db_session.execute(select(Settings).where(Settings.key == AMS_DRYING_LATCH_KEY))
        assert len(result.scalars().all()) == 1
        assert await _load_ams_drying_latch(db_session) == {"1:0": second}

    async def test_clearing_the_latch_empties_the_row(self, db_session):
        await _save_ams_drying_latch(db_session, {"1:0": datetime.now(timezone.utc)})
        await db_session.commit()
        await _save_ams_drying_latch(db_session, {})
        await db_session.commit()

        assert await _stored_value(db_session) == "{}"
        assert await _load_ams_drying_latch(db_session) == {}

    async def test_multiple_units_are_tracked_independently(self, db_session):
        now = datetime.now(timezone.utc)
        latch = {"1:0": now - timedelta(minutes=5), "1:1": now, "2:128": now - timedelta(minutes=15)}
        await _save_ams_drying_latch(db_session, latch)
        await db_session.commit()
        assert await _load_ams_drying_latch(db_session) == latch

    async def test_entries_past_the_grace_cap_are_dropped_on_load(self, db_session):
        now = datetime.now(timezone.utc)
        fresh = now - timedelta(minutes=5)
        stale = now - timedelta(minutes=AMS_DRYING_GRACE_MINUTES + 30)
        await _save_ams_drying_latch(db_session, {"1:0": fresh, "9:3": stale})
        await db_session.commit()

        # The stale one would expire on its next visit anyway; dropping it here
        # keeps rows for deleted printers from accumulating forever.
        assert await _load_ams_drying_latch(db_session) == {"1:0": fresh}

    async def test_wildly_future_stamps_are_dropped(self, db_session):
        # A box whose clock jumps backwards (a Pi coming up before NTP) would
        # otherwise hold the alarm suppressed until real time caught up.
        future = datetime.now(timezone.utc) + timedelta(hours=6)
        await _save_ams_drying_latch(db_session, {"1:0": future})
        await db_session.commit()
        assert await _load_ams_drying_latch(db_session) == {}

    async def test_near_future_stamps_are_clamped_to_now(self, db_session):
        # Small backwards skew survives as a latch, but must not sit ahead of
        # now: suppression is measured as now minus the stamp, so a future one
        # would run for the skew on top of the cap instead of the cap alone.
        before = datetime.now(timezone.utc)
        await _save_ams_drying_latch(db_session, {"1:0": before + timedelta(minutes=30)})
        await db_session.commit()

        loaded = await _load_ams_drying_latch(db_session)
        assert set(loaded) == {"1:0"}
        assert before <= loaded["1:0"] <= datetime.now(timezone.utc)

    async def test_corrupt_row_reads_as_no_latch(self, db_session):
        db_session.add(Settings(key=AMS_DRYING_LATCH_KEY, value="{not json"))
        await db_session.commit()
        # Degrades to the pre-#1802 behaviour rather than crashing the recorder.
        assert await _load_ams_drying_latch(db_session) == {}

    async def test_non_object_json_reads_as_no_latch(self, db_session):
        db_session.add(Settings(key=AMS_DRYING_LATCH_KEY, value="[1, 2, 3]"))
        await db_session.commit()
        assert await _load_ams_drying_latch(db_session) == {}

    async def test_unparseable_stamps_are_skipped_individually(self, db_session):
        good = datetime.now(timezone.utc) - timedelta(minutes=3)
        db_session.add(
            Settings(
                key=AMS_DRYING_LATCH_KEY,
                value=json.dumps({"1:0": good.isoformat(), "1:1": "yesterday"}),
            )
        )
        await db_session.commit()
        assert await _load_ams_drying_latch(db_session) == {"1:0": good}

    async def test_naive_stamps_are_read_as_utc(self, db_session):
        # SQLite hands back naive datetimes elsewhere in the app, so a hand-edited
        # or migrated value without an offset must not raise on comparison.
        naive = (datetime.now(timezone.utc) - timedelta(minutes=7)).replace(tzinfo=None)
        db_session.add(Settings(key=AMS_DRYING_LATCH_KEY, value=json.dumps({"1:0": naive.isoformat()})))
        await db_session.commit()

        loaded = await _load_ams_drying_latch(db_session)
        assert loaded == {"1:0": naive.replace(tzinfo=timezone.utc)}
