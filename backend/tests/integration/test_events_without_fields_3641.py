"""Events that pass no fields send the payload they always sent (#3641).

Plate Changer Empty (#3575) and Filament Out of Stock (#3590) have no fields of
their own. On Discord they are the title, the body and the green side colour,
with the shipped template and with an edited one.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.services.notification_content import DEFAULT_EMBED_COLOR
from backend.app.services.notification_service import NotificationService

pytestmark = pytest.mark.integration

PLATE_CHANGER_BODY = (
    "Swap A1: all 5 plates used. Refill the plate changer and empty its bin, then reset the count "
    "on the printer card. The queue sends this printer nothing until then."
)
OUT_OF_STOCK_BODY = "Benchy waits for PLA (Earth Brown), and none is left in the inventory."

# event, the provider's toggle, the call, the shipped title and body, an edited body and what it renders
EVENTS = [
    pytest.param(
        "plate_changer_empty",
        {},
        lambda service, db: service.on_plate_changer_empty(1, "Swap A1", 5, db),
        ("Plate Changer Empty", PLATE_CHANGER_BODY),
        ("{printer} is out of plates ({capacity})", "Swap A1 is out of plates (5)"),
        id="plate_changer_empty",
    ),
    pytest.param(
        "filament_out_of_stock",
        {"on_filament_out_of_stock": True},
        lambda service, db: service.on_filament_out_of_stock("Benchy", "X1C", "PLA (Earth Brown)", db),
        ("Filament Out of Stock", OUT_OF_STOCK_BODY),
        ("Order {filament} for {job_name}", "Order PLA (Earth Brown) for Benchy"),
        id="filament_out_of_stock",
    ),
]


@pytest.fixture
def service():
    # A fresh service: the global one caches templates across tests.
    return NotificationService()


async def _seed(db, event_type, **edits):
    data = {**next(t for t in DEFAULT_TEMPLATES if t["event_type"] == event_type), **edits}
    db.add(
        NotificationTemplate(
            event_type=event_type,
            name=data["name"],
            title_template=data["title_template"],
            body_template=data["body_template"],
        )
    )
    await db.commit()


async def _payload(service, call):
    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=204))
    with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=client):
        await call
    assert client.post.await_count == 1
    return client.post.await_args.kwargs["json"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("event_type", "toggle", "call", "shipped", "edited"), EVENTS)
class TestDiscordPayload:
    async def _discord(self, factory, toggle):
        return await factory(
            provider_type="discord", config={"webhook_url": "https://discord.com/api/webhooks/1/a"}, **toggle
        )

    async def test_shipped_template_has_no_fields(
        self, service, db_session, notification_provider_factory, event_type, toggle, call, shipped, edited
    ):
        await _seed(db_session, event_type)
        await self._discord(notification_provider_factory, toggle)

        payload = await _payload(service, call(service, db_session))

        title, body = shipped
        assert payload == {"embeds": [{"title": title, "description": body, "color": DEFAULT_EMBED_COLOR}]}

    async def test_edited_template_is_sent_as_written(
        self, service, db_session, notification_provider_factory, event_type, toggle, call, shipped, edited
    ):
        template, rendered = edited
        await _seed(db_session, event_type, body_template=template)
        await self._discord(notification_provider_factory, toggle)

        payload = await _payload(service, call(service, db_session))

        assert payload == {"embeds": [{"title": shipped[0], "description": rendered, "color": DEFAULT_EMBED_COLOR}]}

    async def test_no_presentation_is_handed_on(
        self, service, db_session, notification_provider_factory, event_type, toggle, call, shipped, edited
    ):
        await _seed(db_session, event_type)
        await self._discord(notification_provider_factory, toggle)

        with patch.object(service, "_send_to_provider", AsyncMock(return_value=(True, None))) as send:
            await call(service, db_session)

        send.assert_awaited_once()
        assert send.await_args.kwargs["presentation"] is None
