"""The stock alert templates name the colour and subtype (#2955).

The forecast groups by colour, so two colours of one product would otherwise send
the same message. The migration rewrites a template body IF AND ONLY IF it is
still the shipped default -- an admin who edited the wording keeps it, the same
guard as the ha_sensor_alert rename (#2824).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _STOCK_ALERT_TEMPLATE_BODIES, _migrate_stock_alert_template_sku_variables
from backend.app.models.notification_template import DEFAULT_TEMPLATES


@pytest.fixture
async def engine():
    """In-memory SQLite with just the notification_templates table."""
    from backend.app.models.notification_template import NotificationTemplate

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(NotificationTemplate.__table__.create)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert(conn, event_type: str, body: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO notification_templates (event_type, name, title_template, body_template, is_default) "
            "VALUES (:et, 'n', 't', :b, 1)"
        ),
        {"et": event_type, "b": body},
    )


async def _body(conn, event_type: str) -> str:
    return (
        await conn.execute(
            text("SELECT body_template FROM notification_templates WHERE event_type = :et"), {"et": event_type}
        )
    ).scalar_one()


@pytest.mark.parametrize("event_type", sorted(_STOCK_ALERT_TEMPLATE_BODIES))
async def test_rewrites_the_shipped_default(engine, event_type):
    old, _new = _STOCK_ALERT_TEMPLATE_BODIES[event_type]
    async with engine.begin() as conn:
        await _insert(conn, event_type, old)

    async with engine.begin() as conn:
        await _migrate_stock_alert_template_sku_variables(conn)
        body = await _body(conn, event_type)

    assert "{subtype}" in body
    assert "{color}" in body


@pytest.mark.parametrize("event_type", sorted(_STOCK_ALERT_TEMPLATE_BODIES))
async def test_leaves_an_edited_template_alone(engine, event_type):
    async with engine.begin() as conn:
        await _insert(conn, event_type, "Running low on {material}!")

    async with engine.begin() as conn:
        await _migrate_stock_alert_template_sku_variables(conn)
        assert await _body(conn, event_type) == "Running low on {material}!"


@pytest.mark.parametrize("event_type", sorted(_STOCK_ALERT_TEMPLATE_BODIES))
async def test_the_rewrite_is_exactly_what_a_fresh_install_gets(engine, event_type):
    """The migrated body and the DEFAULT_TEMPLATES body must not drift apart."""
    default = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == event_type)
    assert _STOCK_ALERT_TEMPLATE_BODIES[event_type][1] == default["body_template"]


async def test_running_it_twice_changes_nothing(engine):
    old, new = _STOCK_ALERT_TEMPLATE_BODIES["stock_reorder_alert"]
    async with engine.begin() as conn:
        await _insert(conn, "stock_reorder_alert", old)

    for _ in range(2):
        async with engine.begin() as conn:
            await _migrate_stock_alert_template_sku_variables(conn)

    async with engine.begin() as conn:
        assert await _body(conn, "stock_reorder_alert") == new
