"""Location sensor alerts must not ride on the printer sensor toggle (#2824).

on_location_ha_sensor_alert is its own column, not a reuse of on_ha_sensor_alert.
That one can be narrowed to a single printer via NotificationProvider.printer_id,
and a location alert has no printer to narrow by — sharing the column meant a
provider scoped to one printer's sensors silently also received every drybox
alert, with no way to have one without the other.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.notification import NotificationProvider
from backend.app.services.notification_service import NotificationService


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'location-sensor-scope.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_printer_scoped_provider_does_not_get_location_alerts_by_default(session):
    """The regression itself: a provider that only wants one printer's sensor
    alerts must not also receive storage-location alerts just because it has
    on_ha_sensor_alert on."""
    printer_only = NotificationProvider(
        name="Printer 5 sensors",
        provider_type="discord",
        config="{}",
        enabled=True,
        printer_id=5,
        on_ha_sensor_alert=True,
        on_location_ha_sensor_alert=False,
    )
    session.add(printer_only)
    await session.commit()

    service = NotificationService()
    providers = await service._get_providers_for_event(session, "on_location_ha_sensor_alert", None)

    assert providers == []


@pytest.mark.asyncio
async def test_provider_opted_into_location_alerts_receives_them(session):
    global_provider = NotificationProvider(
        name="Global",
        provider_type="discord",
        config="{}",
        enabled=True,
        printer_id=None,
        on_location_ha_sensor_alert=True,
    )
    printer_scoped_but_opted_in = NotificationProvider(
        name="Printer 5, opted into location alerts too",
        provider_type="discord",
        config="{}",
        enabled=True,
        printer_id=5,
        on_location_ha_sensor_alert=True,
    )
    session.add_all([global_provider, printer_scoped_but_opted_in])
    await session.commit()

    service = NotificationService()
    providers = await service._get_providers_for_event(session, "on_location_ha_sensor_alert", None)

    assert {p.name for p in providers} == {"Global", "Printer 5, opted into location alerts too"}


@pytest.mark.asyncio
async def test_printer_sensor_alerts_are_unaffected_by_the_new_column(session):
    """Printer-scoped sensor alerts still work on their own toggle, independent
    of whether the provider has ever touched on_location_ha_sensor_alert."""
    printer_only = NotificationProvider(
        name="Printer 5 sensors",
        provider_type="discord",
        config="{}",
        enabled=True,
        printer_id=5,
        on_ha_sensor_alert=True,
        on_location_ha_sensor_alert=False,
    )
    session.add(printer_only)
    await session.commit()

    service = NotificationService()
    providers = await service._get_providers_for_event(session, "on_ha_sensor_alert", 5)

    assert [p.name for p in providers] == ["Printer 5 sensors"]
