"""Git backups retain detection configuration without exporting credentials."""

from backend.app.models.settings import Settings
from backend.app.services.github_backup import GitHubBackupService


async def test_settings_backup_excludes_credentials(db_session):
    public_settings = {
        "octoeverywhere_enabled": "true",
        "octoeverywhere_confidence": "high",
        "octoeverywhere_poll_interval": "20",
    }
    private_settings = {
        "octoeverywhere_api_key": "private-gadget-key",
        "bambu_cloud_token": "private-cloud-token",
        "auth_secret_key": "private-auth-key",
        "manyfold_client_secret": "private-manyfold-secret",
    }
    db_session.add_all([Settings(key=key, value=value) for key, value in (public_settings | private_settings).items()])
    await db_session.commit()

    files = {}
    await GitHubBackupService()._collect_settings(db_session, files)

    assert files["settings/app_settings.json"]["settings"] == public_settings
    assert all(value not in str(files) for value in private_settings.values())
