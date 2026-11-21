"""Discord shows printer, job and filament as embed fields while the template is unchanged (#3641).

A template the user has edited is sent exactly as written: no fields, the green
side colour. Every other channel gets the text and the variables only.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.services.notification_content import (
    DEFAULT_EMBED_COLOR,
    NotificationPresentation,
    discord_fields,
    is_unmodified_template,
)
from backend.app.services.notification_service import NotificationService

DISCORD_CONFIG = {"webhook_url": "https://discord.com/api/webhooks/123/abc"}
EVENT = "queue_job_waiting"
PRESENTATION = NotificationPresentation(
    fields=(("Printer", "Sol P1s links"), ("Job", "Coaster_4x"), ("Waiting for", "\U0001f7e3 PLA Lavender (#B39DDB)")),
    color=0xB39DDB,
)


def _default(event_type):
    return next(t for t in DEFAULT_TEMPLATES if t["event_type"] == event_type)


@pytest.fixture
def service():
    # A fresh service: the global one caches templates across tests.
    return NotificationService()


@pytest.fixture
def seed_template(db_session):
    async def _seed(event_type=EVENT, **edits):
        data = {**_default(event_type), **edits}
        db_session.add(
            NotificationTemplate(
                event_type=event_type,
                name=data["name"],
                title_template=data["title_template"],
                body_template=data["body_template"],
            )
        )
        await db_session.commit()

    return _seed


def _client():
    response = MagicMock()
    response.status_code = 204
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    return client


async def _send(service, db, provider, presentation, *, event_type=EVENT, variables=None, image_data=None):
    client = _client()
    with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=client):
        await service._send_to_providers(
            [provider],
            "Title",
            "Body",
            db,
            event_type,
            image_data=image_data,
            variables=variables,
            presentation=presentation,
        )
    return client


class TestDiscordEmbed:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unchanged_template_gets_fields_and_colour(
        self, service, db_session, seed_template, notification_provider_factory
    ):
        await seed_template()
        provider = await notification_provider_factory(provider_type="discord", config=DISCORD_CONFIG)

        client = await _send(service, db_session, provider, PRESENTATION)

        embed = client.post.await_args.kwargs["json"]["embeds"][0]
        assert embed["title"] == "Title"
        assert embed["description"] == "Body"
        assert embed["color"] == 0xB39DDB
        assert embed["fields"] == [
            {"name": "Printer", "value": "Sol P1s links", "inline": False},
            {"name": "Job", "value": "Coaster_4x", "inline": False},
            {"name": "Waiting for", "value": "\U0001f7e3 PLA Lavender (#B39DDB)", "inline": False},
        ]

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("edit", [{"body_template": "{job_name} is waiting"}, {"title_template": "Waiting!"}])
    async def test_edited_template_sends_the_old_payload(
        self, service, db_session, seed_template, notification_provider_factory, edit
    ):
        await seed_template(**edit)
        provider = await notification_provider_factory(provider_type="discord", config=DISCORD_CONFIG)

        client = await _send(service, db_session, provider, PRESENTATION)

        assert client.post.await_args.kwargs["json"] == {
            "embeds": [{"title": "Title", "description": "Body", "color": DEFAULT_EMBED_COLOR}]
        }

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_a_stored_template_nothing_is_added(self, service, db_session, notification_provider_factory):
        provider = await notification_provider_factory(provider_type="discord", config=DISCORD_CONFIG)

        client = await _send(service, db_session, provider, PRESENTATION)

        embed = client.post.await_args.kwargs["json"]["embeds"][0]
        assert "fields" not in embed
        assert embed["color"] == DEFAULT_EMBED_COLOR

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_presentation_is_the_old_payload(
        self, service, db_session, seed_template, notification_provider_factory
    ):
        await seed_template()
        provider = await notification_provider_factory(provider_type="discord", config=DISCORD_CONFIG)

        client = await _send(service, db_session, provider, None)

        assert client.post.await_args.kwargs["json"] == {
            "embeds": [{"title": "Title", "description": "Body", "color": DEFAULT_EMBED_COLOR}]
        }

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_photo_upload_carries_the_fields(
        self, service, db_session, seed_template, notification_provider_factory
    ):
        await seed_template()
        provider = await notification_provider_factory(
            provider_type="discord", config=DISCORD_CONFIG, attach_photo=True
        )

        client = await _send(service, db_session, provider, PRESENTATION, image_data=b"jpeg")

        embed = json.loads(client.post.await_args.kwargs["data"]["payload_json"])["embeds"][0]
        assert [f["name"] for f in embed["fields"]] == ["Printer", "Job", "Waiting for"]
        assert embed["image"] == {"url": "attachment://photo.jpg"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_test_notification_is_unchanged(self, service):
        client = _client()
        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=client):
            await service.send_test_notification("discord", DISCORD_CONFIG, attach_photo=False)

        embed = client.post.await_args.kwargs["json"]["embeds"][0]
        assert set(embed) == {"title", "description", "color"}


class TestOtherChannels:
    """Webhook, Telegram and ntfy get the same call with or without a presentation."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_generic_webhook_payload_does_not_change(
        self, service, db_session, seed_template, notification_provider_factory
    ):
        await seed_template()
        provider = await notification_provider_factory(
            provider_type="webhook", config={"webhook_url": "https://hooks.example.com/x"}
        )
        variables = {"job_name": "Coaster_4x"}

        with patch("backend.app.services.notification_service._assert_safe_provider_url", return_value=None):
            with_presentation = await _send(service, db_session, provider, PRESENTATION, variables=variables)
            without = await _send(service, db_session, provider, None, variables=variables)

        sent = with_presentation.post.await_args.kwargs["json"]
        expected = without.post.await_args.kwargs["json"]
        assert {k: v for k, v in sent.items() if k != "timestamp"} == {
            k: v for k, v in expected.items() if k != "timestamp"
        }
        assert "fields" not in sent
        assert "presentation" not in sent

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("provider_type", "config", "sender"),
        [
            ("telegram", {"bot_token": "1:abc", "chat_id": "1"}, "_send_telegram"),
            ("ntfy", {"server": "https://ntfy.sh", "topic": "t"}, "_send_ntfy"),
        ],
    )
    async def test_text_channels_get_the_same_call(
        self, service, db_session, seed_template, notification_provider_factory, provider_type, config, sender
    ):
        await seed_template()
        provider = await notification_provider_factory(provider_type=provider_type, config=config)

        calls = []
        for presentation in (PRESENTATION, None):
            send = AsyncMock(return_value=(True, "ok"))
            with patch.object(service, sender, send):
                await service._send_to_providers(
                    [provider], "Title", "Body", db_session, EVENT, presentation=presentation
                )
            calls.append(send.await_args)

        assert calls[0] == calls[1]


class TestTemplateDetection:
    @pytest.mark.parametrize("template", DEFAULT_TEMPLATES, ids=lambda t: t["event_type"])
    def test_shipped_text_is_unmodified(self, template):
        assert is_unmodified_template(template["event_type"], template["title_template"], template["body_template"])

    def test_either_part_edited_is_modified(self):
        default = _default(EVENT)
        assert not is_unmodified_template(EVENT, default["title_template"] + "!", default["body_template"])
        assert not is_unmodified_template(EVENT, default["title_template"], default["body_template"] + " more")
        assert not is_unmodified_template(EVENT, default["title_template"], default["body_template"].replace("\n", " "))

    def test_line_endings_and_trailing_space_are_not_an_edit(self):
        default = _default(EVENT)
        body = default["body_template"].replace("\n", "\r\n") + " "
        assert is_unmodified_template(EVENT, default["title_template"] + "  ", body)

    def test_unknown_event_is_modified(self):
        assert not is_unmodified_template("no_such_event", "a", "b")


class TestDiscordFieldLimits:
    def test_at_most_25_fields(self):
        fields = tuple((f"n{i}", "v") for i in range(40))
        assert len(discord_fields(fields, 6000)) == 25

    def test_long_value_is_cut_to_1024(self):
        (field,) = discord_fields((("Waiting for", "x" * 5000),), 6000)
        assert len(field["value"]) == 1024
        assert field["value"].endswith("…")

    def test_empty_name_or_value_is_left_out(self):
        assert discord_fields((("Printer", ""), ("", "x"), ("Job", " "), ("Ok", "y")), 6000) == [
            {"name": "Ok", "value": "y", "inline": False}
        ]

    def test_budget_of_the_embed_is_kept(self):
        fields = tuple((f"n{i}", "v" * 1000) for i in range(10))
        shown = discord_fields(fields, 3000)
        assert sum(len(f["name"]) + len(f["value"]) for f in shown) <= 3000
        assert 0 < len(shown) < 10
