"""Unit tests for the ``backend.app.cli`` kiosk-bootstrap subcommand."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.cli import DEFAULT_KIOSK_KEY_NAME, KioskBootstrapError, kiosk_bootstrap
from backend.app.core.auth import _validate_api_key
from backend.app.core.database import Base
from backend.app.models.api_key import APIKey
from backend.app.models.settings import Settings


@pytest_asyncio.fixture
async def session_maker() -> AsyncGenerator[async_sessionmaker, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_creates_key_when_none_exists(session_maker):
    key = await kiosk_bootstrap(
        DEFAULT_KIOSK_KEY_NAME,
        force=False,
        session_maker=session_maker,
        ensure_schema=False,
    )

    assert key.startswith("bb_")
    assert len(key) > 20

    async with session_maker() as db:
        rows = (await db.execute(select(APIKey))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.name == DEFAULT_KIOSK_KEY_NAME
        assert row.enabled is True
        assert row.can_queue is False
        assert row.can_control_printer is False
        assert row.can_read_status is True
        assert row.printer_ids is None
        assert row.expires_at is None
        assert row.key_prefix.startswith("bb_")
        assert row.key_hash != key  # stored value is a hash, not the plaintext


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_refuses_to_overwrite_without_force(session_maker):
    first = await kiosk_bootstrap(
        DEFAULT_KIOSK_KEY_NAME,
        force=False,
        session_maker=session_maker,
        ensure_schema=False,
    )

    with pytest.raises(KioskBootstrapError) as exc_info:
        await kiosk_bootstrap(
            DEFAULT_KIOSK_KEY_NAME,
            force=False,
            session_maker=session_maker,
            ensure_schema=False,
        )

    assert "already exists" in str(exc_info.value)
    assert "--force" in str(exc_info.value)

    # First key survives unchanged and still validates
    async with session_maker() as db:
        row = (await db.execute(select(APIKey))).scalar_one()
        validated = await _validate_api_key(db, first)
        assert validated is not None
        assert validated.id == row.id


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_force_rotates_existing_key(session_maker):
    first = await kiosk_bootstrap(
        DEFAULT_KIOSK_KEY_NAME,
        force=False,
        session_maker=session_maker,
        ensure_schema=False,
    )
    second = await kiosk_bootstrap(
        DEFAULT_KIOSK_KEY_NAME,
        force=True,
        session_maker=session_maker,
        ensure_schema=False,
    )

    assert first != second

    async with session_maker() as db:
        rows = (await db.execute(select(APIKey))).scalars().all()
        assert len(rows) == 1  # old row was deleted, not duplicated

        # Old key no longer validates, new key does
        assert await _validate_api_key(db, first) is None
        validated = await _validate_api_key(db, second)
        assert validated is not None
        assert validated.name == DEFAULT_KIOSK_KEY_NAME


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_marks_setup_completed(session_maker):
    """Bootstrap must set setup_completed=true so AuthContext doesn't redirect the kiosk to /setup."""
    await kiosk_bootstrap(
        DEFAULT_KIOSK_KEY_NAME,
        force=False,
        session_maker=session_maker,
        ensure_schema=False,
    )

    async with session_maker() as db:
        setting = (await db.execute(select(Settings).where(Settings.key == "setup_completed"))).scalar_one()
        assert setting.value == "true"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_setup_idempotent_on_rotate(session_maker):
    """Re-running with --force must not duplicate the setup_completed row."""
    await kiosk_bootstrap(
        DEFAULT_KIOSK_KEY_NAME,
        force=False,
        session_maker=session_maker,
        ensure_schema=False,
    )
    await kiosk_bootstrap(
        DEFAULT_KIOSK_KEY_NAME,
        force=True,
        session_maker=session_maker,
        ensure_schema=False,
    )

    async with session_maker() as db:
        rows = (await db.execute(select(Settings).where(Settings.key == "setup_completed"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].value == "true"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_custom_name(session_maker):
    key = await kiosk_bootstrap(
        "custom-kiosk-name",
        force=False,
        session_maker=session_maker,
        ensure_schema=False,
    )

    async with session_maker() as db:
        row = (await db.execute(select(APIKey))).scalar_one()
        assert row.name == "custom-kiosk-name"
        validated = await _validate_api_key(db, key)
        assert validated is not None
        assert validated.name == "custom-kiosk-name"
