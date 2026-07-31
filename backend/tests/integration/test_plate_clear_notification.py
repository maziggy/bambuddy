"""Integration tests for the plate-clear-required notification (#2525).

The event is opt-in: it fires after every print, at the same moment as the
print-complete alert, so a provider only receives it when the toggle is
explicitly enabled.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.notification_service import notification_service


class TestPlateClearNotificationDispatch:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sends_to_a_provider_that_opted_in(self, notification_provider_factory, db_session):
        await notification_provider_factory(name="Opted In", on_plate_clear_required=True)

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_clear_required(1, "Workshop", db_session)

        assert send.await_count == 1
        providers = send.await_args.args[0]
        assert [p.name for p in providers] == ["Opted In"]
        assert send.await_args.args[4] == "plate_clear_required"
        # _build_message_from_template folds in app_name/timestamp; the caller's
        # own variable is what matters here.
        assert send.await_args.kwargs["variables"]["printer"] == "Workshop"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_silent_for_a_provider_that_did_not_opt_in(self, notification_provider_factory, db_session):
        await notification_provider_factory(name="Default Off", on_plate_clear_required=False)

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_clear_required(1, "Workshop", db_session)

        send.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skips_a_provider_scoped_to_a_different_printer(self, notification_provider_factory, db_session):
        await notification_provider_factory(name="Other Printer", on_plate_clear_required=True, printer_id=99)

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_clear_required(1, "Workshop", db_session)

        send.assert_not_awaited()


class TestPlateClearProviderField:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_defaults_to_off_on_create_and_round_trips_on_update(self, async_client: AsyncClient):
        create = await async_client.post(
            "/api/v1/notifications/",
            json={
                "name": "Plate Clear Test",
                "provider_type": "ntfy",
                "enabled": True,
                "config": {"server": "https://ntfy.sh", "topic": "test-topic"},
            },
        )
        assert create.status_code in (200, 201), create.text
        provider_id = create.json()["id"]
        assert create.json()["on_plate_clear_required"] is False

        update = await async_client.patch(
            f"/api/v1/notifications/{provider_id}",
            json={"on_plate_clear_required": True},
        )
        assert update.status_code == 200, update.text
        assert update.json()["on_plate_clear_required"] is True
