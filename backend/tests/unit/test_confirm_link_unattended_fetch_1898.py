"""A machine must not be able to spend a one-tap verdict token (#1898).

Telegram and Slack fetch the URLs in a message to build a preview card, mail
gateways detonate links before delivery, and browsers prefetch. While the
verdict URLs went out as plain text in the notification body -- which is what
``DEFAULT_TEMPLATES['print_confirm_request']`` used to ship -- any one of those
GETs recorded a verdict nobody chose and retired the token, so the operator's
real tap landed on "already answered" and a scrap part counted as good for the
rest of time.

Four defences, in the order they matter. The capability URLs no longer travel
in body text at all. Recording is a POST, so a GET from anything that walks a
URL changes nothing (the route side of that is in the integration tests). The
confirmation page submits its own form only for a URL carrying the one-tap
marker, which rides on the Telegram inline keyboard and on nothing a machine
can read — so the scanners that render HTML and run JavaScript, which send an
ordinary Chrome string and which no User-Agent list can name, get a page with a
button on it. And the channels that build previews are asked not to, on the one
message whose links are capabilities.

The unattended-fetch heuristic is the fifth and the weakest, which is why it is
no longer the only thing in front of the write.
"""

import json

import httpx
import pytest

from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_template import DEFAULT_TEMPLATES
from backend.app.services.notification_service import NotificationService
from backend.app.services.print_confirmation import is_one_tap_request, is_unattended_fetch, one_tap_url

CONFIG = {"bot_token": "123456:AAbbCC", "chat_id": "-1002520100736"}

# Real link-preview and mail-security fetchers, plus the browsers that must
# keep working. The phone UAs are the ones a notification tap actually opens.
BROWSER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    # The ntfy app performs its action buttons itself, with an HTTP client UA.
    "okhttp/4.12.0",
    "python-httpx/0.27.0",
]

MACHINE_AGENTS = [
    "TelegramBot (like TwitterBot)",
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "facebookexternalhit/1.1",
    "Twitterbot/1.0",
    "WhatsApp/2.23.20.0 A",
    "Mozilla/5.0 (compatible; SkypeUriPreview Preview/0.5)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Barracuda Sentinel (EE)",
    "Mimecast-Link-Protect",
]


class _Client:
    """Stand-in for httpx.AsyncClient that records what would be sent."""

    def __init__(self):
        self.is_closed = False
        self.calls: list[dict] = []

    async def post(self, url, data=None, files=None, json=None, headers=None):
        self.calls.append(json if json is not None else (data or {}))
        return httpx.Response(200, json={"ok": True, "result": {}})


class TestUnattendedFetchDetection:
    @pytest.mark.parametrize("agent", BROWSER_AGENTS)
    def test_a_real_browser_is_left_alone(self, agent):
        assert is_unattended_fetch("GET", {"user-agent": agent}) is False

    @pytest.mark.parametrize("agent", MACHINE_AGENTS)
    def test_link_fetchers_are_recognised(self, agent):
        assert is_unattended_fetch("GET", {"user-agent": agent}) is True

    def test_prefetch_hints_count_even_from_a_browser_ua(self):
        """Chrome and Firefox preload links before they are clicked; Safari's
        preview sends the same hint. A finger on a button never does."""
        browser = BROWSER_AGENTS[1]
        assert is_unattended_fetch("GET", {"user-agent": browser, "purpose": "prefetch"}) is True
        assert is_unattended_fetch("GET", {"user-agent": browser, "x-purpose": "preview"}) is True
        assert is_unattended_fetch("GET", {"user-agent": browser, "x-moz": "prefetch"}) is True
        assert is_unattended_fetch("GET", {"user-agent": browser, "sec-purpose": "prefetch;prerender"}) is True

    def test_head_is_never_a_tap(self):
        """Only the GET route renders the page, so only a GET can be widened
        to HEAD by a future router change. A probe must not come back looking
        like a page load."""
        assert is_unattended_fetch("HEAD", {"user-agent": BROWSER_AGENTS[0]}) is True

    def test_a_missing_user_agent_is_not_held_against_the_caller(self):
        """Some notification clients send none; the token is still the
        credential, and guessing here would cost real verdicts."""
        assert is_unattended_fetch("GET", {}) is False


class TestPromptKeepsTheCapabilityOutOfBodyText:
    """This file used to pin the opposite: that both verdict URLs were in the
    body. They were, and that was the defect — the body is the one part of a
    notification that every unfurler, gateway and proxy reads. The capability
    links now travel only in affordances nothing prefetches."""

    def test_the_default_template_carries_only_the_deep_link(self):
        template = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "print_confirm_request")
        assert "{good_url}" not in template["body_template"]
        assert "{reject_url}" not in template["body_template"]
        assert "{confirm_url}" in template["body_template"]


class TestNtfyButtonsRecordOverPost:
    @pytest.mark.asyncio
    async def test_the_action_buttons_use_post(self):
        """The ntfy app issues the action itself, so it can use the method that
        records. A GET would only open the confirmation page — which is the
        point of the split, and is what every crawler gets."""
        service = NotificationService()
        captured: dict = {}

        async def _fake_ntfy(config, title, message, image_data=None, event_type=None, actions=None):
            captured["actions"] = actions
            return True, "ok"

        service._send_ntfy = _fake_ntfy
        provider = NotificationProvider(
            name="farm", provider_type="ntfy", config=json.dumps({"server": "https://ntfy.sh", "topic": "farm"})
        )

        ok, _ = await service._send_to_provider(
            provider,
            "How did your print come out?",
            "X1C: bracket.3mf",
            event_type="print_confirm_request",
            variables={
                "good_url": "https://farm.example.com/api/v1/archives/confirm/tok/good",
                "reject_url": "https://farm.example.com/api/v1/archives/confirm/tok/reject",
            },
        )

        assert ok
        assert "method=POST" in captured["actions"]
        assert "method=GET" not in captured["actions"]


class TestTheOneTapMarkerRidesOnlyOnButtons:
    """The marker is what decides whether the confirmation page submits itself,
    and it is on the Telegram inline keyboard and nowhere else.

    The User-Agent list above catches the fetchers that say what they are, and
    none of those run JavaScript. The ones that do — a mail-security sandbox
    detonating the link, a browser-isolation proxy — send an ordinary Chrome
    string, so no list can name them. What they cannot have is a URL that never
    appeared in any text they can read.
    """

    def test_a_marked_url_is_the_only_thing_that_opens_the_script(self):
        assert is_one_tap_request({"tap": "1"}) is True
        assert is_one_tap_request({}) is False, "a link out of a message body must not qualify"
        assert is_one_tap_request({"tap": "0"}) is False
        assert is_one_tap_request({"tap": "yes"}) is False

    def test_marking_a_url_that_already_carries_a_query(self):
        assert one_tap_url("https://host/api/v1/archives/confirm/tok/good") == (
            "https://host/api/v1/archives/confirm/tok/good?tap=1"
        )
        assert one_tap_url("https://host/confirm/tok/good?from=ntfy") == "https://host/confirm/tok/good?from=ntfy&tap=1"

    @pytest.mark.asyncio
    async def test_the_telegram_buttons_are_marked_and_the_body_link_is_not(self):
        """Telegram cannot POST, so its buttons are the one affordance that
        opens a browser — and the one that needs the page to submit itself.
        Telegram never fetches an inline-keyboard URL, so the marker does not
        leak into anything a machine reads."""
        service = NotificationService()
        captured: dict = {}

        async def _fake_telegram(config, message, image_data=None, buttons=None, link_preview=True):
            captured["buttons"] = buttons
            captured["message"] = message
            return True, "ok"

        service._send_telegram = _fake_telegram
        provider = NotificationProvider(name="farm", provider_type="telegram", config=json.dumps(CONFIG))

        ok, _ = await service._send_to_provider(
            provider,
            "How did your print come out?",
            "X1C: bracket.3mf\nGood: https://farm.example.com/api/v1/archives/confirm/tok/good",
            event_type="print_confirm_request",
            variables={
                "good_url": "https://farm.example.com/api/v1/archives/confirm/tok/good",
                "reject_url": "https://farm.example.com/api/v1/archives/confirm/tok/reject",
            },
        )

        assert ok
        assert [b["url"] for b in captured["buttons"]] == [
            "https://farm.example.com/api/v1/archives/confirm/tok/good?tap=1",
            "https://farm.example.com/api/v1/archives/confirm/tok/reject?tap=1",
        ]
        # An install that kept {good_url} in its edited body still sends the
        # plain URL in the text — and that is exactly the one that must not
        # press its own button when a scanner opens it.
        assert "?tap=1" not in captured["message"]

    @pytest.mark.asyncio
    async def test_the_ntfy_actions_stay_unmarked(self):
        """They POST, so no page is rendered and there is nothing to submit.
        Marking them would put the marker in an Authorization-free HTTP header
        for no gain."""
        service = NotificationService()
        captured: dict = {}

        async def _fake_ntfy(config, title, message, image_data=None, event_type=None, actions=None):
            captured["actions"] = actions
            return True, "ok"

        service._send_ntfy = _fake_ntfy
        provider = NotificationProvider(
            name="farm", provider_type="ntfy", config=json.dumps({"server": "https://ntfy.sh", "topic": "farm"})
        )

        ok, _ = await service._send_to_provider(
            provider,
            "How did your print come out?",
            "X1C: bracket.3mf",
            event_type="print_confirm_request",
            variables={
                "good_url": "https://farm.example.com/api/v1/archives/confirm/tok/good",
                "reject_url": "https://farm.example.com/api/v1/archives/confirm/tok/reject",
            },
        )

        assert ok
        assert "tap=1" not in captured["actions"]


class TestSlackDoesNotUnfurl:
    @pytest.mark.asyncio
    async def test_the_slack_payload_turns_previews_off(self):
        """Slack and Mattermost unfurl the URLs in `text` the same way
        Telegram builds a preview card."""
        service = NotificationService()
        client = _Client()
        service._http_client = client

        ok, _ = await service._send_webhook(
            {"webhook_url": "https://hooks.slack.example.com/services/T/B/x", "payload_format": "slack"},
            "How did your print come out?",
            "X1C: bracket.3mf",
            event_type="print_confirm_request",
        )

        assert ok
        assert client.calls[0]["unfurl_links"] is False
        assert client.calls[0]["unfurl_media"] is False

    @pytest.mark.asyncio
    async def test_every_other_event_keeps_its_previews(self):
        """Only the outcome prompt's links are capabilities. The slack payload
        never attaches image bytes — the base64 attach is generic-format only —
        so the unfurl is the only way a {finish_photo_url} in a print_complete
        body ever becomes a photo in the channel, and there is no setting that
        turns it back on."""
        service = NotificationService()
        client = _Client()
        service._http_client = client

        ok, _ = await service._send_webhook(
            {"webhook_url": "https://hooks.slack.example.com/services/T/B/x", "payload_format": "slack"},
            "Print complete",
            "X1C: bracket.3mf\nhttps://farm.example.com/api/v1/archives/7/photos/finish_a.jpg",
            event_type="print_complete",
        )

        assert ok
        assert "unfurl_links" not in client.calls[0]
        assert "unfurl_media" not in client.calls[0]


class TestTelegramDoesNotAskForAPreview:
    @pytest.mark.asyncio
    async def test_send_message_disables_the_link_preview(self):
        """Telegram's servers GET the first URL in the text to build the
        preview card. That fetch is the one that answered the prompt."""
        service = NotificationService()
        client = _Client()
        service._http_client = client

        ok, _ = await service._send_telegram(
            CONFIG,
            "*How did your print come out?*\nX1C: bracket.3mf\nGood: https://host/api/v1/archives/confirm/tok/good",
            link_preview=False,
        )

        assert ok
        assert client.calls[0]["disable_web_page_preview"] is True

    @pytest.mark.asyncio
    async def test_the_inline_buttons_variant_disables_it_too(self):
        """The shape the outcome prompt actually sends on a camera-less
        printer: no photo, so sendMessage rather than sendPhoto."""
        service = NotificationService()
        client = _Client()
        service._http_client = client

        buttons = [
            {"text": "Good", "url": "https://host/api/v1/archives/confirm/tok/good"},
            {"text": "Reject", "url": "https://host/api/v1/archives/confirm/tok/reject"},
        ]
        ok, _ = await service._send_telegram(CONFIG, "*T*\nbody", buttons=buttons, link_preview=False)

        assert ok
        assert client.calls[0]["disable_web_page_preview"] is True
        assert client.calls[0]["reply_markup"] == {"inline_keyboard": [buttons]}

    @pytest.mark.asyncio
    async def test_every_other_message_keeps_its_preview(self):
        """When the finish photo is too large to attach, the preview card is
        how a {finish_photo_url} in a print_complete body still shows up as a
        photo in the chat. Only the outcome prompt gives that up."""
        service = NotificationService()
        client = _Client()
        service._http_client = client

        ok, _ = await service._send_telegram(
            CONFIG, "*Print complete*\nX1C: bracket.3mf\nhttps://farm.example.com/api/v1/archives/7/photos/finish_a.jpg"
        )

        assert ok
        assert "disable_web_page_preview" not in client.calls[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("event_type", "expected"),
        [("print_confirm_request", False), ("print_complete", True), (None, True)],
    )
    async def test_only_the_outcome_prompt_is_sent_without_a_preview(self, event_type, expected):
        service = NotificationService()
        captured: dict = {}

        async def _fake_telegram(config, message, image_data=None, buttons=None, link_preview=True):
            captured["link_preview"] = link_preview
            return True, "ok"

        service._send_telegram = _fake_telegram
        provider = NotificationProvider(name="farm", provider_type="telegram", config=json.dumps(CONFIG))

        ok, _ = await service._send_to_provider(provider, "Title", "body", event_type=event_type)

        assert ok
        assert captured["link_preview"] is expected
