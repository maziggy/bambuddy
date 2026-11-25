"""Upgrades and resets use the provider-neutral AI template without losing custom text."""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.database import _migrate_ai_failure_detection_template, seed_notification_templates
from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate

LEGACY_BODY = "{printer}: {task_name}\nConfidence: {confidence}\nAction taken: {action}"
AI_DEFAULT = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "ai_failure_detection")


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(NotificationTemplate.__table__.create)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("event_type", "body", "expected_body"),
    [
        ("ai_failure_detection", LEGACY_BODY, AI_DEFAULT["body_template"]),
        ("ai_failure_detection", "Check {printer}: {confidence}", "Check {printer}: {confidence}"),
        ("printer_error", LEGACY_BODY, LEGACY_BODY),
    ],
)
async def test_migration_updates_only_legacy_ai_body_and_is_idempotent(engine, event_type, body, expected_body):
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add(
            NotificationTemplate(
                event_type=event_type,
                name="My template",
                title_template="Check {printer}",
                body_template=body,
            )
        )
        await session.commit()

    for _ in range(2):
        async with engine.begin() as conn:
            await _migrate_ai_failure_detection_template(conn)

    async with session_factory() as session:
        template = (await session.execute(select(NotificationTemplate))).scalar_one()
        assert template.body_template == expected_body
        assert template.title_template == "Check {printer}"
        assert template.name == "My template"


async def test_seed_and_reset_share_the_provider_neutral_default(engine):
    from backend.app.api.routes.notification_templates import reset_template

    session_factory = async_sessionmaker(engine)
    with patch("backend.app.core.database.async_session", session_factory):
        await seed_notification_templates()

    async with session_factory() as session:
        template = (
            await session.execute(
                select(NotificationTemplate).where(NotificationTemplate.event_type == "ai_failure_detection")
            )
        ).scalar_one()
        assert template.body_template == AI_DEFAULT["body_template"]
        assert {"{provider}", "{print_quality}", "{confidence}"} <= set(template.body_template.split())
        template_id = template.id
        template.title_template = "Check {printer}"
        template.body_template = "My custom body"
        await session.commit()

        with patch("backend.app.api.routes.notification_templates.notification_service.clear_template_cache") as clear:
            reset = await reset_template(template_id, db=session)

        assert reset.title_template == AI_DEFAULT["title_template"]
        assert reset.body_template == AI_DEFAULT["body_template"]
        clear.assert_called_once()
