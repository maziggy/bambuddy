"""invalidate_all_sessions signs every user out and drops pending auth tokens.

It runs once after the auth datetime columns are converted on PostgreSQL and
after every backup restore, where the rows may come from older code.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.core.auth import _is_token_fresh
from backend.app.core.config import settings as app_settings
from backend.app.core.database import invalidate_all_sessions
from backend.app.models.auth_ephemeral import AuthEphemeralToken, AuthRateLimitEvent, EventType, TokenType
from backend.app.models.user import User


@pytest.mark.asyncio
async def test_signs_every_user_out_and_drops_short_lived_auth_records(test_engine, db_session):
    db_session.add_all(
        [
            User(username="changed", password_hash="x", password_changed_at=datetime(2026, 1, 1)),
            User(username="never_changed", password_hash="x", password_changed_at=None),
        ]
    )
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    for token_type in ("revoked_jti", "media", "camera_stream", "websocket", *[t.value for t in TokenType]):
        db_session.add(AuthEphemeralToken(token=f"t-{token_type}", token_type=token_type, expires_at=expires))
    for event_type in EventType:
        db_session.add(AuthRateLimitEvent(username="changed", event_type=event_type.value))
    await db_session.commit()

    before = datetime.now(timezone.utc)
    async with test_engine.begin() as conn:
        await invalidate_all_sessions(conn)

    db_session.expire_all()
    users = (await db_session.execute(select(User))).scalars().all()
    assert {u.username for u in users} == {"changed", "never_changed"}
    for user in users:
        # A token issued before the call is rejected, one issued after it is not.
        assert _is_token_fresh(int(before.timestamp()) - 1, user) is False
        assert _is_token_fresh(int(before.timestamp()) + 2, user) is True

    remaining = (await db_session.execute(select(AuthEphemeralToken.token_type))).scalars().all()
    assert remaining == ["revoked_jti"]
    assert (await db_session.execute(select(AuthRateLimitEvent))).scalars().all() == []


@pytest.mark.asyncio
async def test_restore_ends_by_invalidating_all_sessions(async_client, monkeypatch, tmp_path):
    from backend.app.api.routes.settings import create_backup_zip

    # Keep the restored database separate from the test engine.
    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    zip_path, _ = await create_backup_zip(output_path=tmp_path)

    invalidate = AsyncMock()
    with (
        patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
        patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
        patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        patch("backend.app.core.database.invalidate_all_sessions", invalidate),
        patch("backend.app.services.print_scheduler.scheduler.stop"),
        patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
        patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
    ):
        response = await async_client.post(
            "/api/v1/settings/restore",
            files={"file": ("backup.zip", zip_path.read_bytes(), "application/zip")},
        )

    assert response.status_code == 200, response.text
    invalidate.assert_awaited_once()
