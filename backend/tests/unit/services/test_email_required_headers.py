"""Outgoing mail carries Date and Message-ID, and a readable From (#3376).

Neither smtplib nor the MIME classes add Date or Message-ID, so strict
receivers (amavis's bad_header policy) quarantined every Bambuddy mail after
the SMTP server had accepted it. Both senders are covered: send_email (SMTP
test, welcome, password reset, MFA) and the notification provider.
"""

from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from unittest.mock import patch

import pytest

from backend.app.schemas.auth import SMTPSettings
from backend.app.services.email_service import add_required_headers, send_email
from backend.app.services.notification_service import NotificationService

JPEG = b"\xff\xd8\xff\xe0jpeg"
PHOTO_URL = "https://printer.local/api/v1/archives/42/photos/finish.jpg"


def _assert_required_headers(msg, domain: str) -> None:
    assert parsedate_to_datetime(msg["Date"]).tzinfo is not None
    message_id = msg["Message-ID"]
    assert message_id.startswith("<")
    assert message_id.endswith(f"@{domain}>")


def _fake_smtp(captured: list):
    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            # As sent: serialise and re-parse, so header encoding counts.
            captured.append(message_from_string(msg.as_string()))

        def sendmail(self, frm, to, body):
            captured.append(message_from_string(body))

        def quit(self):
            pass

    return FakeSMTP


def _smtp_settings(**kw) -> SMTPSettings:
    base = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_auth_enabled": False,
        "smtp_from_email": "farm@mail.example.org",
        "smtp_from_name": "Bambuddy",
    }
    base.update(kw)
    return SMTPSettings(**base)


@pytest.mark.parametrize("image", [None, JPEG], ids=["alternative", "related"])
def test_send_email_sets_date_and_message_id(image):
    captured: list = []
    with patch("backend.app.services.email_service.smtplib.SMTP", _fake_smtp(captured)):
        send_email(_smtp_settings(), "to@example.com", "Test", "body", "<p>body</p>", image_data=image)

    (msg,) = captured
    _assert_required_headers(msg, "mail.example.org")


@pytest.mark.parametrize("image", [None, JPEG], ids=["alternative", "related"])
def test_send_email_from_keeps_the_address_readable(image):
    """A non-ASCII or comma'd sender name is encoded on its own; the address
    stays plain, so receivers still find a valid From."""
    captured: list = []
    settings = _smtp_settings(smtp_from_name="Bämbuddy Farm, Inc")
    with patch("backend.app.services.email_service.smtplib.SMTP", _fake_smtp(captured)):
        send_email(settings, "to@example.com", "Test", "body", "<p>body</p>", image_data=image)

    (msg,) = captured
    assert "<farm@mail.example.org>" in msg["From"]
    # Decoded the way a mail client does.
    (sender,) = message_from_string(msg.as_string(), policy=policy.default)["From"].addresses
    assert (sender.display_name, sender.addr_spec) == ("Bämbuddy Farm, Inc", "farm@mail.example.org")


@pytest.mark.asyncio
@pytest.mark.parametrize("inline_photo", [False, True], ids=["text", "related"])
async def test_notification_email_sets_date_and_message_id(inline_photo):
    config = {
        "smtp_server": "smtp.example.com",
        "smtp_port": "587",
        "from_email": "printer@farm.example.net",
        "to_email": "alice@example.com",
        "security": "starttls",
        "auth_enabled": "false",
    }
    body = f"Done.\n\nSnapshot: {PHOTO_URL}" if inline_photo else "Done."
    captured: list = []
    with patch("backend.app.services.notification_service.smtplib.SMTP", _fake_smtp(captured)):
        ok, _ = await NotificationService()._send_email(
            config, "Print Finished", body, image_data=JPEG, finish_photo_url=PHOTO_URL
        )

    assert ok is True
    (msg,) = captured
    assert msg.get_content_type() == ("multipart/related" if inline_photo else "multipart/mixed")
    _assert_required_headers(msg, "farm.example.net")


@pytest.mark.parametrize("from_email", ["", "no-at-sign", "user@"])
def test_message_id_without_a_sender_domain_uses_a_fixed_one(from_email):
    """Never make_msgid()'s hostname lookup: it can block on DNS and puts
    the container's name in the header."""
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart()
    with patch("socket.getfqdn", side_effect=AssertionError("hostname lookup")):
        add_required_headers(msg, from_email)
    _assert_required_headers(msg, "bambuddy.local")


def test_existing_headers_are_kept():
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart()
    msg["Date"] = "Sat, 24 Oct 2026 19:40:19 +0000"
    msg["Message-ID"] = "<fixed@example.com>"
    add_required_headers(msg, "a@b.example")
    assert msg.get_all("Date") == ["Sat, 24 Oct 2026 19:40:19 +0000"]
    assert msg.get_all("Message-ID") == ["<fixed@example.com>"]
