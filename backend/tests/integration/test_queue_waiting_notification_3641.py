"""Queue Job Waiting names the printer and the filaments it waits for (#3641).

The default template's words stay as they were. Discord adds fields while that
template is unchanged; every channel gets the new variables for its own templates.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.services.notification_content import NotificationFilament
from backend.app.services.notification_service import NotificationService, telegram_markdown_escape

LAVENDER = NotificationFilament(type="PLA", color_hex="#B39DDB", color_name="Lavender Purple", slot="AMS A1")
REASON = "No matching material/color. Waiting on PLA (Lavender Purple)"
DEFAULT = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "queue_job_waiting")


@pytest.fixture
def service():
    return NotificationService()


@pytest.fixture
def seed_template(db_session):
    async def _seed(**edits):
        data = {**DEFAULT, **edits}
        db_session.add(
            NotificationTemplate(
                event_type="queue_job_waiting",
                name=data["name"],
                title_template=data["title_template"],
                body_template=data["body_template"],
            )
        )
        await db_session.commit()

    return _seed


async def _waiting(service, db, *, printer_id=None, target_model="P1S", filaments=(), provider_type="discord"):
    """Run the event against a mocked HTTP client and return what Discord was sent."""
    response = MagicMock(status_code=204)
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=client):
        await service.on_queue_job_waiting(
            job_name="Coaster_4x",
            target_model=target_model,
            waiting_reason=REASON,
            db=db,
            printer_id=printer_id,
            filaments=filaments,
        )
    return client.post.await_args.kwargs["json"]["embeds"][0]


@pytest.fixture
def discord(notification_provider_factory):
    return lambda **kw: notification_provider_factory(
        provider_type="discord", config={"webhook_url": "https://discord.com/api/webhooks/1/a"}, **kw
    )


class TestDiscord:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pinned_job_shows_printer_job_and_filament(
        self, service, db_session, seed_template, discord, printer_factory
    ):
        await seed_template()
        await discord()
        printer = await printer_factory(name="Sol P1s links", model="P1S")

        embed = await _waiting(service, db_session, printer_id=printer.id, filaments=[LAVENDER])

        # The default words are what they were.
        assert embed["title"] == "Queue Job Waiting"
        assert embed["description"] == f"Coaster_4x waiting for P1S\n{REASON}"
        assert embed["fields"] == [
            {"name": "Printer", "value": "Sol P1s links", "inline": False},
            {"name": "Job", "value": "Coaster_4x", "inline": False},
            {"name": "Waiting for", "value": "\U0001f7e3 PLA Lavender Purple (#B39DDB)", "inline": False},
        ]
        assert embed["color"] == 0xB39DDB

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_model_job_is_any_model_and_several_filaments_keep_the_green(
        self, service, db_session, seed_template, discord
    ):
        await seed_template()
        await discord()
        white = NotificationFilament(type="PETG", color_hex="#FFFFFF", color_name="White")

        embed = await _waiting(service, db_session, filaments=[LAVENDER, white])

        assert embed["fields"][0] == {"name": "Printer", "value": "Any P1S", "inline": False}
        assert embed["fields"][2]["value"] == "\U0001f7e3 PLA Lavender Purple (#B39DDB)\n⚪ PETG White (#FFFFFF)"
        assert embed["color"] == 0x00AE42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_filaments_there_is_no_waiting_for_field(self, service, db_session, seed_template, discord):
        await seed_template()
        await discord()

        embed = await _waiting(service, db_session)

        assert [f["name"] for f in embed["fields"]] == ["Printer", "Job"]
        assert embed["color"] == 0x00AE42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_near_white_filament_keeps_the_green(self, service, db_session, seed_template, discord):
        await seed_template()
        await discord()

        embed = await _waiting(service, db_session, filaments=[NotificationFilament(type="PLA", color_hex="#F5F5F5")])

        assert embed["color"] == 0x00AE42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_edited_template_is_sent_as_before(self, service, db_session, seed_template, discord):
        await seed_template(body_template="{job_name} waits: {waiting_reason}")
        await discord()

        embed = await _waiting(service, db_session, filaments=[LAVENDER])

        assert embed == {"title": "Queue Job Waiting", "description": f"Coaster_4x waits: {REASON}", "color": 0x00AE42}


class TestVariables:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_text_channels_can_use_the_new_variables(
        self, service, db_session, seed_template, notification_provider_factory, printer_factory
    ):
        await seed_template(
            body_template="{printer}: {job_name}\nneeds {filament} [{filament_type}|{color_name}|{color_hex}|{color_emoji}|{slot}]"
        )
        await notification_provider_factory(provider_type="ntfy", config={"server": "https://ntfy.sh", "topic": "t"})
        printer = await printer_factory(name="Sol P1s links")
        send = AsyncMock(return_value=(True, "ok"))

        with patch.object(service, "_send_ntfy", send):
            await service.on_queue_job_waiting(
                "Coaster_4x", "P1S", REASON, db_session, printer_id=printer.id, filaments=[LAVENDER]
            )

        assert send.await_args.args[2] == (
            "Sol P1s links: Coaster_4x\n"
            "needs \U0001f7e3 PLA Lavender Purple (#B39DDB) [PLA|Lavender Purple|#B39DDB|\U0001f7e3|AMS A1]"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_webhook_gets_the_new_variables_and_the_reason_as_before(
        self, service, db_session, seed_template, notification_provider_factory
    ):
        await seed_template()
        await notification_provider_factory(
            provider_type="webhook", config={"webhook_url": "https://hooks.example.com/x"}
        )
        response = MagicMock(status_code=200)
        client = AsyncMock()
        client.post = AsyncMock(return_value=response)

        with (
            patch.object(service, "_get_client", new_callable=AsyncMock, return_value=client),
            patch("backend.app.services.notification_service._assert_safe_provider_url", return_value=None),
        ):
            await service.on_queue_job_waiting("Coaster_4x", "P1S", REASON, db_session, filaments=[LAVENDER])

        payload = client.post.await_args.kwargs["json"]
        assert payload["waiting_reason"] == REASON
        assert payload["printer"] == "Any P1S"
        assert payload["filament"] == "\U0001f7e3 PLA Lavender Purple (#B39DDB)"
        assert payload["color_hex"] == "#B39DDB"
        assert "fields" not in payload
        assert "presentation" not in payload

    def test_telegram_escapes_the_new_values_like_the_rest_of_the_body(self):
        body = "Sol_P1s: Coaster_4x\nneeds \U0001f7e3 PLA Lavender_Purple (#B39DDB)"
        escaped = telegram_markdown_escape(f"*Queue Job Waiting*\n{body}")
        assert escaped == (
            "*Queue Job Waiting*\nSol\\_P1s: Coaster\\_4x\nneeds \U0001f7e3 PLA Lavender\\_Purple (#B39DDB)"
        )
