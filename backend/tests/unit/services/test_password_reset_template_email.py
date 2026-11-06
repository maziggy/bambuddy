"""The password-reset link email is built from the "Password Reset" template.

The template was seeded and editable, but since the reset became a link email
no code read it: the email was always the built-in one, whatever the admin
wrote. Its default still described the old flow (a new password emailed in
plain text). The email now renders the template, and the code adds the link at
the end. The link is not a template variable, so the template's text cannot
change it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.core import database as _db
from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA
from backend.app.services.email_service import create_password_reset_link_email_from_template

RESET_URL = "https://bambuddy.example.com/login#reset_token=abc123"
DEFAULT = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "password_reset")


def _template(title: str, body: str) -> NotificationTemplate:
    return NotificationTemplate(
        event_type="password_reset", name="Password Reset", title_template=title, body_template=body, is_default=False
    )


async def _render(template: NotificationTemplate | None):
    with patch("backend.app.services.email_service.get_notification_template", return_value=template):
        return await create_password_reset_link_email_from_template(AsyncMock(spec=AsyncSession), "maker", RESET_URL)


async def test_the_default_template_sends_the_link():
    subject, text_body, html_body = await _render(_template(DEFAULT["title_template"], DEFAULT["body_template"]))

    assert subject == "BamBuddy - Password Reset"
    assert "maker" in text_body
    assert text_body.endswith(f"\n\n{RESET_URL}")
    assert text_body.count(RESET_URL) == 1
    assert f'href="{RESET_URL}"' in html_body
    assert "{" not in text_body


async def test_an_edited_template_is_what_the_user_gets_with_the_link_at_the_end():
    subject, text_body, html_body = await _render(
        _template("Reset for {username}", "Hi {username}\nThe {app_name} team")
    )

    assert subject == "Reset for maker"
    assert text_body == f"Hi maker\nThe BamBuddy team\n\n{RESET_URL}"
    assert "<br>" in html_body


async def test_a_template_cannot_place_the_link():
    """A {reset_url} in the template renders as nothing; the link is only the
    one the code adds."""
    subject, text_body, html_body = await _render(
        _template("Reset {reset_url}", "Reset here: https://elsewhere.example/r?next={reset_url}")
    )

    assert text_body == f"Reset here: https://elsewhere.example/r?next=\n\n{RESET_URL}"
    assert "reset_token" not in subject
    assert "next=https" not in text_body and "next=https" not in html_body
    assert html_body.count("reset_token") == 2  # the line at the end and the button


async def test_a_template_for_the_old_flow_still_carries_the_link():
    """Customised for the old flow: {password} is gone (renders empty)."""
    _subject, text_body, html_body = await _render(
        _template("Reset", "Hello {username},\nNew Password: {password}\nLogin at: {login_url}")
    )

    assert text_body.endswith(f"\n\n{RESET_URL}")
    assert f'href="{RESET_URL}"' in html_body


async def test_the_body_is_escaped_in_the_html():
    _subject, _text, html_body = await _render(_template("Reset", "<b>{username}</b>"))

    assert "<b>maker</b>" not in html_body
    assert "&lt;b&gt;maker&lt;/b&gt;" in html_body


async def test_without_a_template_the_built_in_email_is_sent():
    subject, text_body, html_body = await _render(None)

    assert subject == "BamBuddy - Password Reset Request"
    assert RESET_URL in text_body and RESET_URL in html_body


def test_the_editor_offers_neither_the_link_nor_a_password():
    for gone in ("reset_url", "password", "login_url"):
        assert gone not in EVENT_VARIABLES["password_reset"]
        assert gone not in SAMPLE_DATA["password_reset"]


def test_the_default_template_has_no_link_and_no_password():
    assert "{reset_url}" not in DEFAULT["body_template"]
    assert "{password}" not in DEFAULT["body_template"]


# --- upgrade: the shipped default is rewritten, an edited one is kept ---


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(NotificationTemplate.__table__.create)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert(conn, body: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO notification_templates (event_type, name, title_template, body_template, is_default) "
            "VALUES ('password_reset', 'Password Reset', '{app_name} - Password Reset', :b, 1)"
        ),
        {"b": body},
    )


async def _body(conn) -> str:
    return (
        await conn.execute(text("SELECT body_template FROM notification_templates WHERE event_type = 'password_reset'"))
    ).scalar_one()


async def test_the_upgrade_rewrites_the_old_default(engine):
    old, new = _db._PASSWORD_RESET_TEMPLATE_BODY
    async with engine.begin() as conn:
        await _insert(conn, old)

    for _ in range(2):  # runs on every start
        async with engine.begin() as conn:
            await _db._migrate_password_reset_template_link(conn)

    async with engine.begin() as conn:
        assert await _body(conn) == new


async def test_the_upgrade_keeps_an_edited_template(engine):
    async with engine.begin() as conn:
        await _insert(conn, "My own words {password}")

    async with engine.begin() as conn:
        await _db._migrate_password_reset_template_link(conn)
        assert await _body(conn) == "My own words {password}"


def test_the_upgrade_gives_what_a_fresh_install_gets():
    assert _db._PASSWORD_RESET_TEMPLATE_BODY[1] == DEFAULT["body_template"]


@pytest.mark.sqlite_only  # upgrades its own SQLite database
async def test_start_up_runs_the_upgrade(tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession as Session

    from backend.app.core.database import Base, run_migrations
    from backend.app.main import app  # noqa: F401  registers every model, as init_db does

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _insert(conn, _db._PASSWORD_RESET_TEMPLATE_BODY[0])

        async with engine.begin() as conn:
            await run_migrations(conn)

        async with Session(engine) as session:
            body = (await session.execute(text("SELECT body_template FROM notification_templates"))).scalar_one()
        assert body == _db._PASSWORD_RESET_TEMPLATE_BODY[1]
    finally:
        await engine.dispose()
