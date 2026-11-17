"""The "Filament Out of Stock" notification event end to end (#3590)."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA
from backend.app.services.notification_service import notification_service


class TestService:
    @pytest.mark.asyncio
    async def test_sends_to_opted_in_provider(self, db_session, notification_provider_factory):
        await notification_provider_factory(name="Opted In", on_filament_out_of_stock=True)
        # Templates are seeded at startup; seed the one under test the same way.
        db_session.add(
            NotificationTemplate(**next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "filament_out_of_stock"))
        )
        await db_session.commit()
        with patch.object(notification_service, "_send_to_provider", AsyncMock(return_value=(True, None))) as send:
            await notification_service.on_filament_out_of_stock(
                "Benchy", "X1C", "PLA (Earth Brown)", db_session, printer_id=None
            )
        send.assert_awaited_once()
        title, message = send.await_args.args[1], send.await_args.args[2]
        assert title == "Filament Out of Stock"
        assert message == "Benchy waits for PLA (Earth Brown), and none is left in the inventory."

    @pytest.mark.asyncio
    async def test_muted_provider_gets_nothing(self, db_session, notification_provider_factory):
        await notification_provider_factory(name="Muted", on_filament_out_of_stock=False)
        with patch.object(notification_service, "_send_to_provider", AsyncMock(return_value=(True, None))) as send:
            await notification_service.on_filament_out_of_stock("Benchy", "X1C", "PLA (Earth Brown)", db_session)
        send.assert_not_awaited()


class TestApi:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_defaults_off_and_can_be_toggled(self, async_client):
        payload = {
            "name": "Out of stock",
            "provider_type": "ntfy",
            "enabled": True,
            "config": {"server": "https://ntfy.sh", "topic": "t"},
        }
        create = await async_client.post("/api/v1/notifications/", json=payload)
        assert create.status_code in (200, 201), create.text
        body = create.json()
        assert body["on_filament_out_of_stock"] is False

        update = await async_client.patch(
            f"/api/v1/notifications/{body['id']}", json={"on_filament_out_of_stock": True}
        )
        assert update.status_code == 200
        assert update.json()["on_filament_out_of_stock"] is True


class TestTemplate:
    def test_default_template_uses_only_known_variables(self):
        tpl = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "filament_out_of_stock")
        assert tpl["title_template"] == "Filament Out of Stock"
        for var in ("job_name", "target_model", "filament", "timestamp", "app_name"):
            assert var in EVENT_VARIABLES["filament_out_of_stock"]
            assert var in SAMPLE_DATA["filament_out_of_stock"]
        assert "{job_name}" in tpl["body_template"]
        assert "{filament}" in tpl["body_template"]
