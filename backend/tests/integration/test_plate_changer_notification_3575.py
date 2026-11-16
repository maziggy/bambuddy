"""The plate-changer-empty notification (#3575).

Sent once when a plate changer uses its last plate. Unlike plate-clear-required
(#2525) it is on by default: the queue stops feeding that printer, so someone
has to know.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.models.notification_template import DEFAULT_TEMPLATES
from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA
from backend.app.services.notification_service import notification_service

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
class TestDispatch:
    async def test_sends_to_a_provider_with_the_default(self, notification_provider_factory, db_session):
        await notification_provider_factory(name="Default")

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_changer_empty(1, "Swap A1", 5, db_session)

        assert send.await_count == 1
        assert [p.name for p in send.await_args.args[0]] == ["Default"]
        assert send.await_args.args[4] == "plate_changer_empty"
        variables = send.await_args.kwargs["variables"]
        assert variables["printer"] == "Swap A1"
        assert variables["capacity"] == "5"

    async def test_silent_for_a_provider_that_opted_out(self, notification_provider_factory, db_session):
        await notification_provider_factory(name="Opted Out", on_plate_changer_empty=False)

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_changer_empty(1, "Swap A1", 5, db_session)

        send.assert_not_awaited()

    async def test_skips_a_provider_scoped_to_a_different_printer(
        self, notification_provider_factory, printer_factory, db_session
    ):
        await printer_factory(name="Swap A1")
        other = await printer_factory(name="Other")
        await notification_provider_factory(name="Other Printer", printer_id=other.id)

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_changer_empty(1, "Swap A1", 5, db_session)

        send.assert_not_awaited()

    async def test_reaches_a_provider_scoped_to_this_printer(
        self, notification_provider_factory, printer_factory, db_session
    ):
        printer = await printer_factory(name="Swap A1")
        await notification_provider_factory(name="Mine", printer_id=printer.id)
        printer_id = printer.id

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_changer_empty(printer_id, "Swap A1", 5, db_session)

        assert [p.name for p in send.await_args.args[0]] == ["Mine"]

    async def test_a_disabled_provider_gets_nothing(self, notification_provider_factory, db_session):
        await notification_provider_factory(name="Off", enabled=False)

        send = AsyncMock()
        with patch.object(notification_service, "_send_to_providers", send):
            await notification_service.on_plate_changer_empty(1, "Swap A1", 5, db_session)

        send.assert_not_awaited()


class TestTemplate:
    def _default(self):
        return next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "plate_changer_empty")

    def test_default_template_exists_and_only_uses_declared_variables(self):
        import re

        template = self._default()
        assert template["name"] == "Plate Changer Empty"
        declared = set(EVENT_VARIABLES["plate_changer_empty"])
        used = set(re.findall(r"\{([a-z_]+)\}", template["title_template"] + template["body_template"]))
        assert used <= declared
        assert {"printer", "capacity"} <= used

    def test_variables_and_sample_data(self):
        assert EVENT_VARIABLES["plate_changer_empty"] == ["printer", "capacity", "timestamp", "app_name"]
        assert set(SAMPLE_DATA["plate_changer_empty"]) == set(EVENT_VARIABLES["plate_changer_empty"])

    def test_renders_the_printer_and_the_capacity(self):
        body = notification_service._render_template(
            self._default()["body_template"], {"printer": "Swap A1", "capacity": "5"}
        )
        assert body.startswith("Swap A1: all 5 plates used")
        assert "{" not in body

    @pytest.mark.asyncio
    async def test_the_event_type_is_in_the_variables_api(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/notification-templates/variables")
        assert response.status_code == 200, response.text
        entry = next(e for e in response.json() if e["event_type"] == "plate_changer_empty")
        assert entry["event_name"] == "Plate Changer Empty"
        assert entry["variables"] == ["printer", "capacity", "timestamp", "app_name"]


@pytest.mark.asyncio
class TestProviderField:
    async def test_defaults_to_on_on_create_and_round_trips_on_update(self, async_client: AsyncClient):
        create = await async_client.post(
            "/api/v1/notifications/",
            json={
                "name": "Plate Changer Test",
                "provider_type": "ntfy",
                "enabled": True,
                "config": {"server": "https://ntfy.sh", "topic": "test-topic"},
            },
        )
        assert create.status_code in (200, 201), create.text
        provider_id = create.json()["id"]
        assert create.json()["on_plate_changer_empty"] is True

        off = await async_client.patch(f"/api/v1/notifications/{provider_id}", json={"on_plate_changer_empty": False})
        assert off.status_code == 200, off.text
        assert off.json()["on_plate_changer_empty"] is False
        assert (await async_client.get(f"/api/v1/notifications/{provider_id}")).json()[
            "on_plate_changer_empty"
        ] is False

        on = await async_client.patch(f"/api/v1/notifications/{provider_id}", json={"on_plate_changer_empty": True})
        assert on.json()["on_plate_changer_empty"] is True

    async def test_create_can_switch_it_off(self, async_client: AsyncClient):
        create = await async_client.post(
            "/api/v1/notifications/",
            json={
                "name": "Quiet",
                "provider_type": "ntfy",
                "enabled": True,
                "config": {"server": "https://ntfy.sh", "topic": "test-topic"},
                "on_plate_changer_empty": False,
            },
        )
        assert create.status_code in (200, 201), create.text
        assert create.json()["on_plate_changer_empty"] is False
