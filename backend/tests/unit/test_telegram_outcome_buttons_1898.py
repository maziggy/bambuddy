"""The outcome prompt must arrive even when Telegram refuses its buttons (#1898).

The Good / Reject affordance on Telegram is an inline URL keyboard. Telegram
validates every button URL and rejects the whole ``sendMessage`` when one of
them is not a URL it accepts -- which is what an install without a public
``external_url`` produces. Losing the buttons is survivable; losing the message
the user is waiting for is not, and "I never got a message" is precisely the
report this feature keeps drawing.
"""

import httpx
import pytest

from backend.app.services.notification_service import NotificationService

CONFIG = {"bot_token": "123456:AAbbCC", "chat_id": "-1002520100736"}
BUTTONS = [
    {"text": "\U0001f44d Good", "url": "http://bambuddy.local:8000/api/v1/archives/confirm/tok/good"},
    {"text": "\U0001f44e Reject", "url": "http://bambuddy.local:8000/api/v1/archives/confirm/tok/reject"},
]
PNG = b"\x89PNG\r\n\x1a\n"


class _Client:
    """Stand-in for httpx.AsyncClient that rejects any send carrying buttons."""

    def __init__(self, reject_buttons: bool):
        self.is_closed = False
        self.reject_buttons = reject_buttons
        self.calls: list[dict] = []

    async def post(self, url, data=None, files=None, json=None):
        body = json if json is not None else (data or {})
        self.calls.append(body)
        if self.reject_buttons and "reply_markup" in body:
            return httpx.Response(
                400, json={"ok": False, "description": "Bad Request: inline keyboard button URL is invalid"}
            )
        return httpx.Response(200, json={"ok": True, "result": {}})


def _service(reject_buttons: bool) -> tuple[NotificationService, _Client]:
    service = NotificationService()
    client = _Client(reject_buttons)
    service._http_client = client  # bypass real HTTP
    return service, client


@pytest.mark.asyncio
async def test_a_rejected_keyboard_falls_back_to_a_plain_message():
    service, client = _service(reject_buttons=True)

    ok, detail = await service._send_telegram(CONFIG, "*How did your print come out?*\nbody", buttons=BUTTONS)

    assert ok, detail
    assert len(client.calls) == 2, "expected one attempt with buttons and one without"
    assert "reply_markup" in client.calls[0]
    assert "reply_markup" not in client.calls[1]
    assert client.calls[1]["text"].endswith("body")


@pytest.mark.asyncio
async def test_the_photo_variant_falls_back_too():
    """The finish photo rides along with the prompt, so this is the shape most
    completed prints actually send."""
    service, client = _service(reject_buttons=True)

    ok, _ = await service._send_telegram(CONFIG, "*T*\nbody", image_data=PNG, buttons=BUTTONS)

    assert ok
    assert len(client.calls) == 2
    assert "reply_markup" not in client.calls[1]


@pytest.mark.asyncio
async def test_buttons_are_kept_when_telegram_accepts_them():
    service, client = _service(reject_buttons=False)

    ok, _ = await service._send_telegram(CONFIG, "*T*\nbody", buttons=BUTTONS)

    assert ok
    assert len(client.calls) == 1
    assert client.calls[0]["reply_markup"] == {"inline_keyboard": [BUTTONS]}


@pytest.mark.asyncio
async def test_a_buttonless_failure_is_still_reported():
    """The retry exists for the keyboard, not to paper over a bad token."""

    class _AlwaysFails(_Client):
        async def post(self, url, data=None, files=None, json=None):
            self.calls.append(json if json is not None else (data or {}))
            return httpx.Response(401, text="Unauthorized")

    service = NotificationService()
    client = _AlwaysFails(reject_buttons=False)
    service._http_client = client

    ok, detail = await service._send_telegram(CONFIG, "*T*\nbody")

    assert ok is False
    assert "401" in detail
    assert len(client.calls) == 1
