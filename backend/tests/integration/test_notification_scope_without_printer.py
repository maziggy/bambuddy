"""An event about a job that has no printer yet reaches "All printers" channels only.

A channel limited to one printer received every "job added" for an "Any
<model>" or unassigned job, and every "job waiting": with no printer on the
event, no printer filter applied. A wait for a job pinned to a printer names
that printer and reaches its channels. It hears of such a
job once it is assigned to or started on its printer. Events about the whole
fleet -- the queue finishing, stock, app messages -- still reach every channel.
"""

import pytest

from backend.app.models.notification import NotificationProvider
from backend.app.services.notification_service import NotificationService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _providers(db_session, printer_factory):
    printer = await printer_factory()
    rows = [
        NotificationProvider(
            name="everyone", provider_type="ntfy", config="{}", on_queue_job_added=True, on_queue_completed=True
        ),
        NotificationProvider(
            name="one printer",
            provider_type="ntfy",
            config="{}",
            printer_id=printer.id,
            on_queue_job_added=True,
            on_queue_completed=True,
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    return printer.id


async def _names(service, db_session, event, printer_id=None, **kwargs):
    providers = await service._get_providers_for_event(db_session, event, printer_id, **kwargs)
    return sorted(p.name for p in providers)


async def test_a_job_without_a_printer_reaches_all_printer_channels_only(db_session, printer_factory):
    await _providers(db_session, printer_factory)

    assert await _names(NotificationService(), db_session, "on_queue_job_added") == ["everyone"]


async def test_a_job_on_the_printer_reaches_its_channel(db_session, printer_factory):
    printer_id = await _providers(db_session, printer_factory)

    assert await _names(NotificationService(), db_session, "on_queue_job_added", printer_id) == [
        "everyone",
        "one printer",
    ]


async def test_a_fleet_event_reaches_every_channel(db_session, printer_factory):
    await _providers(db_session, printer_factory)

    assert await _names(NotificationService(), db_session, "on_queue_completed", fleet_wide=True) == [
        "everyone",
        "one printer",
    ]


async def test_the_fleet_events_are_marked_so():
    """Their callers pass fleet_wide, or they would silently lose scoped channels."""
    import inspect

    from backend.app.services import notification_service, print_scheduler

    source = inspect.getsource(notification_service.NotificationService)
    for event in (
        "on_app_message",
        "on_location_ha_sensor_alert",
        "on_queue_completed",
        "on_stock_reorder_alert",
        "on_stock_break_alert",
    ):
        line = next(text for text in source.splitlines() if f'_get_providers_for_event(db, "{event}"' in text)
        assert "fleet_wide=True" in line, event
    scheduler = inspect.getsource(print_scheduler)
    for event in ("on_filament_low", "on_stock_reorder_alert", "on_stock_break_alert"):
        line = next(text for text in scheduler.splitlines() if f'_get_providers_for_event(db, "{event}"' in text)
        assert "fleet_wide=True" in line, event


@pytest.mark.parametrize(("printer_bound", "reached"), [(True, ["everyone", "one printer"]), (False, ["everyone"])])
async def test_a_job_waiting_on_its_printer_reaches_that_printers_channel(
    db_session, printer_factory, printer_bound, reached
):
    from unittest.mock import AsyncMock, patch

    printer = await printer_factory()
    db_session.add_all(
        [
            NotificationProvider(name="everyone", provider_type="ntfy", config="{}", on_queue_job_waiting=True),
            NotificationProvider(
                name="one printer",
                provider_type="ntfy",
                config="{}",
                printer_id=printer.id,
                on_queue_job_waiting=True,
            ),
        ]
    )
    await db_session.commit()
    service = NotificationService()
    send = AsyncMock()

    with patch.object(service, "_send_to_providers", send):
        await service.on_queue_job_waiting(
            job_name="Bracket",
            target_model="X1C",
            waiting_reason="filament_short",
            db=db_session,
            printer_id=printer.id if printer_bound else None,
        )

    assert sorted(p.name for p in send.await_args.args[0]) == reached
