"""Regression tests for startup seeding of the enclosure temp/humidity cache.

These guard against the mismatch where the seeding code read
``EnclosureReading.temperature`` (the model column is ``temp``) and wrote the
cache under the ``"temperature"`` key (consumers read ``"temp"``). Either bug
left the enclosure temp/humidity cards blank for ~60s after every restart.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest


class _FakeSessionCtx:
    """Async context manager that yields a pre-existing session.

    Lets ``seed_enclosure_cache_from_db`` reuse the test's ``db_session`` (and
    therefore the same in-memory SQLite connection) instead of opening a new one.
    """

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
async def reading_factory(db_session, printer_factory):
    """Factory to create EnclosureReading rows tied to a printer."""

    async def _create(printer_id=None, **kwargs):
        from backend.app.models.enclosure_reading import EnclosureReading

        if printer_id is None:
            printer = await printer_factory()
            printer_id = printer.id

        defaults = {
            "printer_id": printer_id,
            "temp": 24.0,
            "humidity": 55.0,
            "recorded_at": datetime.now(timezone.utc),
        }
        defaults.update(kwargs)
        reading = EnclosureReading(**defaults)
        db_session.add(reading)
        await db_session.commit()
        await db_session.refresh(reading)
        return reading

    return _create


def test_model_uses_temp_not_temperature():
    """The column is ``temp``; reading ``.temperature`` is what crashed seeding."""
    from backend.app.models.enclosure_reading import EnclosureReading

    reading = EnclosureReading(printer_id=1, temp=25.5, humidity=60.0)
    assert reading.temp == 25.5
    assert not hasattr(reading, "temperature")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_seed_populates_cache_with_temp_key(db_session, printer_factory, reading_factory):
    """Startup seeding maps the latest DB reading into the cache consumers read."""
    from backend.app.services.homeassistant import homeassistant_service

    printer = await printer_factory(ha_temp_entity="sensor.enclosure_temp")
    await reading_factory(printer_id=printer.id, temp=25.5, humidity=60.0)

    saved_cache = homeassistant_service._enclosure_cache
    homeassistant_service._enclosure_cache = {}
    try:
        with patch(
            "backend.app.main.async_session",
            lambda: _FakeSessionCtx(db_session),
        ):
            from backend.app.main import seed_enclosure_cache_from_db

            await seed_enclosure_cache_from_db()

        cached = homeassistant_service.get_cached_enclosure(printer.id)
        assert cached is not None, "cache was not seeded (model attr / key mismatch?)"
        # Consumers (enclosure.py, storage.py, printers.py) read the "temp" key.
        assert cached["temp"] == 25.5
        assert cached["humidity"] == 60.0
    finally:
        homeassistant_service._enclosure_cache = saved_cache


@pytest.mark.asyncio
@pytest.mark.integration
async def test_seed_uses_latest_reading(db_session, printer_factory, reading_factory):
    """Seeding picks the most recent reading per printer."""
    from backend.app.services.homeassistant import homeassistant_service

    printer = await printer_factory(ha_temp_entity="sensor.enclosure_temp")
    await reading_factory(
        printer_id=printer.id,
        temp=20.0,
        humidity=40.0,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    await reading_factory(
        printer_id=printer.id,
        temp=30.0,
        humidity=70.0,
        recorded_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    saved_cache = homeassistant_service._enclosure_cache
    homeassistant_service._enclosure_cache = {}
    try:
        with patch(
            "backend.app.main.async_session",
            lambda: _FakeSessionCtx(db_session),
        ):
            from backend.app.main import seed_enclosure_cache_from_db

            await seed_enclosure_cache_from_db()

        cached = homeassistant_service.get_cached_enclosure(printer.id)
        assert cached is not None
        assert cached["temp"] == 30.0
        assert cached["humidity"] == 70.0
    finally:
        homeassistant_service._enclosure_cache = saved_cache
