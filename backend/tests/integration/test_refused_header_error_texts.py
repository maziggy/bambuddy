"""A stored credential must not come back in an error's text.

An HTTP client refusing a header (a stored token with a stray space or line
break) quotes the header's value. The Git backup's results and logs and the
Home Assistant connection test returned that text, so a saved credential, which
the API otherwise never hands back, could be read in an error message.
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.services.homeassistant import HomeAssistantService

# Made up. Never paste a value from a real log here.
SECRET = "git-refused-value-3"
REFUSED = f"Illegal header value b'token {SECRET} '"

_CONNECTED = {
    "success": True,
    "message": "Connection successful",
    "repo_name": "test/repo",
    "permissions": {"push": True},
    "is_private": True,
}


async def _configure_backup(async_client: AsyncClient) -> None:
    with patch(
        "backend.app.services.github_backup.github_backup_service.test_connection",
        new=AsyncMock(return_value=_CONNECTED),
    ):
        response = await async_client.post(
            "/api/v1/github-backup/config",
            json={
                "repository_url": "https://github.com/test/repo",
                "access_token": "ghp_token",
                "branch": "main",
                "schedule_enabled": False,
                "schedule_type": "daily",
                "backup_kprofiles": True,
                "backup_cloud_profiles": False,
                "backup_settings": False,
                "enabled": True,
            },
        )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "push",
    [
        AsyncMock(side_effect=RuntimeError(REFUSED)),
        AsyncMock(return_value={"status": "failed", "message": REFUSED, "error": REFUSED}),
    ],
    ids=["raised", "returned"],
)
async def test_a_failed_backup_reports_no_header_value(async_client: AsyncClient, db_session, push):
    await _configure_backup(async_client)
    # The backup opens its own sessions.
    sessions = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)

    with (
        patch("backend.app.services.github_backup.github_backup_service._push_to_provider", new=push),
        patch("backend.app.services.github_backup.async_session", sessions),
        patch(
            "backend.app.services.github_backup.github_backup_service.test_connection",
            new=AsyncMock(return_value=_CONNECTED),
        ),
    ):
        run = await async_client.post("/api/v1/github-backup/run")
    config = await async_client.get("/api/v1/github-backup/config")
    logs = await async_client.get("/api/v1/github-backup/logs")

    for response in (run, config, logs):
        assert response.status_code == 200, response.text
        assert SECRET not in response.text
    assert "Illegal header value [REDACTED]" in run.text
    assert "Illegal header value [REDACTED]" in logs.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_stored_token_test_reports_no_header_value(async_client: AsyncClient):
    await _configure_backup(async_client)
    backend = MagicMock()
    backend.test_connection = AsyncMock(return_value={"success": False, "message": REFUSED})

    with patch("backend.app.services.github_backup.get_provider_backend", return_value=backend):
        response = await async_client.post("/api/v1/github-backup/test-stored")

    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert "Illegal header value [REDACTED]" in response.text


@pytest.mark.asyncio
async def test_the_home_assistant_test_reports_no_header_value():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        url = f"http://127.0.0.1:{server.getsockname()[1]}"
        with patch.object(HomeAssistantService, "_validate_url", side_effect=lambda u: u):
            result = await HomeAssistantService(timeout=2).test_connection(url, f"{SECRET} ")

    assert result["success"] is False
    assert SECRET not in result["error"]
    assert "Illegal header value [REDACTED]" in result["error"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_errors_stored_before_masking_are_served_masked(async_client: AsyncClient, db_session):
    """Rows written by an earlier version still hold the quoted value."""
    from backend.app.models.github_backup import GitHubBackupLog
    from backend.app.models.notification import NotificationLog, NotificationProvider

    provider = NotificationProvider(
        name="HA", provider_type="homeassistant", enabled=True, config="{}", last_error=REFUSED
    )
    db_session.add(provider)
    await db_session.flush()
    db_session.add(
        NotificationLog(
            provider_id=provider.id, event_type="test", title="t", message="m", success=False, error_message=REFUSED
        )
    )
    await db_session.commit()
    await _configure_backup(async_client)
    config = (await async_client.get("/api/v1/github-backup/config")).json()
    db_session.add(GitHubBackupLog(config_id=config["id"], status="failed", trigger="manual", error_message=REFUSED))
    await db_session.commit()

    for path in ("/api/v1/notifications/", "/api/v1/notifications/logs", "/api/v1/github-backup/logs"):
        response = await async_client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert SECRET not in response.text, path
        assert "Illegal header value [REDACTED]" in response.text, path


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_restore_commit_list_reports_no_header_value(async_client: AsyncClient):
    await _configure_backup(async_client)
    backend = MagicMock()
    backend.list_commits = AsyncMock(
        return_value={"success": False, "message": f"LocalProtocolError: {REFUSED}", "commits": []}
    )

    with patch("backend.app.services.github_restore.get_provider_backend", return_value=backend):
        response = await async_client.get("/api/v1/github-backup/commits")

    assert SECRET not in response.text
    assert "Illegal header value [REDACTED]" in response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_obico_status_reports_no_header_value(async_client: AsyncClient):
    from backend.app.services.obico_detection import obico_detection_service

    with patch.object(obico_detection_service, "_last_error", f"ML API call failed for printer 1: {REFUSED}"):
        response = await async_client.get("/api/v1/obico/status")

    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert "Illegal header value [REDACTED]" in response.text
