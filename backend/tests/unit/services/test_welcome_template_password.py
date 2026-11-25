"""A new user's initial password never passes through the editable Welcome template.

The Welcome Email template (user_created) rendered {password} as a variable.
Bambuddy now adds the login details itself, at the end of the text and in the
HTML, so the template's text cannot change them; a {password} in the template
renders as nothing.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.core import database as _db
from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate
from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA
from backend.app.services.email_service import create_welcome_email_from_template

PASSWORD = "S3cr3t&<pw>"
LOGIN_URL = "https://bambuddy.example.com/login"
DEFAULT = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "user_created")
DETAILS = f"Username: maker\nPassword: {PASSWORD}"


def _template(title: str, body: str) -> NotificationTemplate:
    return NotificationTemplate(
        event_type="user_created", name="Welcome Email", title_template=title, body_template=body, is_default=False
    )


async def _render(template: NotificationTemplate | None):
    with patch("backend.app.services.email_service.get_notification_template", return_value=template):
        return await create_welcome_email_from_template(AsyncMock(spec=AsyncSession), "maker", PASSWORD, LOGIN_URL)


async def test_a_template_cannot_carry_the_password_off():
    subject, text_body, html_body = await _render(
        _template("Hi {password}", "Activate: https://elsewhere.example/a?p={password}")
    )

    assert PASSWORD not in subject
    assert text_body == f"Activate: https://elsewhere.example/a?p=\n\n{DETAILS}"
    assert "p=S3cr3t" not in html_body


async def test_the_default_template_sends_the_details_once():
    subject, text_body, html_body = await _render(_template(DEFAULT["title_template"], DEFAULT["body_template"]))

    assert subject == "Welcome to BamBuddy"
    assert text_body.endswith(f"\n\n{DETAILS}")
    assert text_body.count(PASSWORD) == 1
    assert LOGIN_URL in text_body
    assert "{" not in text_body
    assert f'href="{LOGIN_URL}"' in html_body


async def test_the_details_are_escaped_in_the_html():
    _subject, _text, html_body = await _render(_template("Welcome", "Hello"))

    assert html_body.count("S3cr3t&amp;&lt;pw&gt;") == 1  # in the box only
    assert PASSWORD not in html_body


async def test_an_edited_template_keeps_its_words():
    _subject, text_body, _html = await _render(_template("Welcome", "Hallo {username}, viel Spass mit {app_name}!"))

    assert text_body == f"Hallo maker, viel Spass mit BamBuddy!\n\n{DETAILS}"


async def test_the_timestamp_the_editor_offers_is_filled():
    _subject, text_body, _html = await _render(_template("Welcome", "Created {timestamp}"))

    assert re.match(r"Created \d{4}-\d{2}-\d{2} \d{2}:\d{2}\n\n", text_body)


async def test_without_a_template_the_built_in_email_is_sent():
    subject, text_body, _html = await _render(None)

    assert subject == "Welcome to BamBuddy - Your Account Details"
    assert PASSWORD in text_body


def test_the_editor_does_not_offer_the_password():
    assert "password" not in EVENT_VARIABLES["user_created"]
    assert "password" not in SAMPLE_DATA["user_created"]


def test_the_default_template_has_no_password():
    assert "{password}" not in DEFAULT["body_template"]
    assert "{password}" not in DEFAULT["title_template"]


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
            "VALUES ('user_created', 'Welcome Email', 'Welcome to {app_name}', :b, 1)"
        ),
        {"b": body},
    )


async def _body(conn) -> str:
    return (
        await conn.execute(text("SELECT body_template FROM notification_templates WHERE event_type = 'user_created'"))
    ).scalar_one()


async def test_the_upgrade_rewrites_the_old_default(engine):
    old, new = _db._WELCOME_TEMPLATE_BODY
    async with engine.begin() as conn:
        await _insert(conn, old)

    for _ in range(2):  # runs on every start
        async with engine.begin() as conn:
            await _db._migrate_welcome_template_without_password(conn)

    async with engine.begin() as conn:
        assert await _body(conn) == new


async def test_the_upgrade_keeps_an_edited_template(engine):
    async with engine.begin() as conn:
        await _insert(conn, "My own words {password}")

    async with engine.begin() as conn:
        await _db._migrate_welcome_template_without_password(conn)
        assert await _body(conn) == "My own words {password}"


def test_the_upgrade_gives_what_a_fresh_install_gets():
    assert _db._WELCOME_TEMPLATE_BODY[1] == DEFAULT["body_template"]


@pytest.mark.sqlite_only  # upgrades its own SQLite database
async def test_start_up_runs_the_upgrade(tmp_path):
    from backend.app.core.database import Base, run_migrations
    from backend.app.main import app  # noqa: F401  registers every model, as init_db does

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _insert(conn, _db._WELCOME_TEMPLATE_BODY[0])

        async with engine.begin() as conn:
            await run_migrations(conn)

        async with engine.connect() as conn:
            assert await _body(conn) == _db._WELCOME_TEMPLATE_BODY[1]
    finally:
        await engine.dispose()
