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

    async def _recipients(self, db_session, printer_id):
        with patch.object(notification_service, "_send_to_providers", AsyncMock()) as fan_out:
            await notification_service.on_filament_out_of_stock(
                "Benchy", "X1C", "PLA (Earth Brown)", db_session, printer_id=printer_id
            )
            await notification_service.on_queue_job_waiting(
                "Benchy", "X1C", "needs PLA (Earth Brown)", db_session, printer_id=printer_id
            )
        out_of_stock, waiting = ([p.name for p in call.args[0]] for call in fan_out.await_args_list)
        # The two events about one job go to the same providers.
        assert sorted(out_of_stock) == sorted(waiting)
        return sorted(out_of_stock)

    @pytest.mark.asyncio
    async def test_any_model_job_reaches_only_all_printers_providers(
        self, db_session, notification_provider_factory, printer_factory
    ):
        """No printer holds an "Any <model>" job, so a provider limited to one printer is left out."""
        first, second = await printer_factory(), await printer_factory()
        common = {"on_filament_out_of_stock": True, "on_queue_job_waiting": True}
        await notification_provider_factory(name="All", **common)
        await notification_provider_factory(name="First", printer_id=first.id, **common)
        await notification_provider_factory(name="Second", printer_id=second.id, **common)

        assert await self._recipients(db_session, None) == ["All"]
        # A job held on a printer also reaches that printer's own provider.
        assert await self._recipients(db_session, first.id) == ["All", "First"]


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
