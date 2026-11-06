"""Settings secrets never leave the instance in a Git backup.

A restore refuses every credential-shaped key, so the backup has no use for
their values. It still records whether each one was set (the placeholder) so
the restore can tell a switch whose credential stayed behind from one that
never had one. A URL setting with a login in it is left out: a masked URL
restored over a working one would break it.
"""

import json

import pytest
from sqlalchemy import delete

from backend.app.models.settings import Settings
from backend.app.services.github_backup import GitHubBackupService
from backend.app.services.github_restore import GitHubRestoreService, _is_blocked_setting_key
from backend.app.services.secret_fields import SECRET_PLACEHOLDER

SECRETS = {
    "mqtt_password": "value-one",
    "ha_token": "value-two",
    "prometheus_token": "value-three",
    "obico_ml_token": "value-four",
    "ldap_bind_password": "value-five",
    "smtp_password": "value-six",
    "virtual_printer_access_code": "value-seven",
    "bambu_cloud_token": "value-eight",
    "auth_secret_key": "value-nine",
    "manyfold_client_secret": "value-ten",
    "MQTT_SECRET": "value-eleven",
    "orca_cloud_pending_verifier": "value-twelve",
}


async def _collect(db_session, rows: dict[str, str]) -> dict:
    db_session.add_all(Settings(key=key, value=value) for key, value in rows.items())
    await db_session.commit()
    files: dict = {}
    await GitHubBackupService()._collect_settings(db_session, files)
    return files["settings/app_settings.json"]["settings"]


@pytest.mark.asyncio
async def test_a_set_secret_is_written_as_the_placeholder(db_session):
    collected = await _collect(db_session, {**SECRETS, "currency": "EUR"})

    assert {key: collected.get(key) for key in SECRETS} == dict.fromkeys(SECRETS, SECRET_PLACEHOLDER)
    assert collected["currency"] == "EUR"
    dumped = json.dumps(collected)
    assert not [value for value in SECRETS.values() if value in dumped]


@pytest.mark.asyncio
async def test_an_empty_secret_stays_empty(db_session):
    """Blank counts as empty, as it does for the restore."""
    collected = await _collect(db_session, {"prometheus_token": "", "mqtt_password": "  "})

    assert collected == {"prometheus_token": "", "mqtt_password": "  "}


@pytest.mark.asyncio
async def test_a_backed_up_secret_is_refused_on_restore(db_session):
    plan = await GitHubRestoreService()._plan_settings(db_session, dict(SECRETS))

    assert sorted(plan.blocked) == sorted(SECRETS)


@pytest.mark.asyncio
async def test_the_backup_hides_exactly_what_the_restore_refuses(db_session):
    rows = {**SECRETS, "currency": "EUR", "mqtt_enabled": "true", "ldap_server_url": "ldap://h"}
    collected = await _collect(db_session, rows)

    for key in rows:
        assert (collected[key] == SECRET_PLACEHOLDER) is _is_blocked_setting_key(key), key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://user:value-url@spoolman.local:7912/",
        "https://value-url@spoolman.local",
        "http://:value-url@spoolman.local",
        "http://user:value-url@spoolman.local?x=1",
    ],
)
async def test_a_url_setting_with_a_login_is_left_out(db_session, url):
    collected = await _collect(db_session, {"spoolman_url": url, "ha_url": "http://ha.local:8123"})

    assert "spoolman_url" not in collected
    assert collected["ha_url"] == "http://ha.local:8123"
    assert "value-url" not in json.dumps(collected)


@pytest.mark.asyncio
async def test_a_switch_whose_credential_stayed_behind_is_still_left_off_on_restore(db_session):
    """Restored onto an instance without the password, MQTT must not come on."""
    collected = await _collect(db_session, {"mqtt_enabled": "true", "mqtt_password": "value-one"})
    await db_session.execute(delete(Settings))
    await db_session.commit()

    plan = await GitHubRestoreService()._plan_settings(db_session, collected)

    assert plan.companion == ("mqtt_enabled",)
    assert "mqtt_password" in plan.blocked


LEGACY = {
    "virtual_printer_tailscale_auth_key": "value-legacy-one",
    "vapid_private_key": "value-legacy-two",
    "dark_theme": "true",
    "_backfill_1425_pipeline_permissions_done": "true",
    "debug_logging_enabled": "true",
}


@pytest.mark.asyncio
async def test_a_key_a_restore_does_not_write_is_not_backed_up(db_session):
    """Keys earlier versions left behind, runtime state and Bambuddy's own
    flags: a restore refuses them, so the backup leaves them out, secrets
    named in no recognisable way among them."""
    collected = await _collect(db_session, {**LEGACY, "currency": "EUR", "smtp_host": "mail.local"})

    assert not set(LEGACY) & set(collected)
    assert "value-legacy" not in json.dumps(collected)
    assert collected["currency"] == "EUR" and collected["smtp_host"] == "mail.local"


@pytest.mark.asyncio
async def test_everything_backed_up_is_something_a_restore_knows(db_session):
    rows = {**SECRETS, **LEGACY, "currency": "EUR", "ldap_server_url": "ldap://h", "manyfold_client_id": "id"}
    collected = await _collect(db_session, rows)

    plan = await GitHubRestoreService()._plan_settings(db_session, collected)

    assert plan.unknown == () and plan.internal == ()


@pytest.mark.asyncio
async def test_a_retired_setting_in_an_old_backup_is_dropped_quietly(db_session):
    plan = await GitHubRestoreService()._plan_settings(db_session, {"library_archive_mode": "ask", "currency": "EUR"})

    assert plan.unknown == ()
    assert "library_archive_mode" not in plan.stored


class TestANonAdminRestoreKeepsTheSignInSettings:
    """Through the settings page only an admin may change these, and only an
    admin may set the mail server: a GitHub restore by a non-admin holding
    github:restore and settings:update keeps them as they are here."""

    PAYLOAD = {
        "session_max_hours": 720,
        "smtp_username": "relay@example.com",
        "smtp_from_name": "Bambuddy",
        "prometheus_enabled": True,
        "external_url": "https://attacker.example",
        "currency": "EUR",
    }

    @pytest.mark.asyncio
    async def test_a_non_admin_restore_leaves_them(self, db_session):
        from backend.app.services import github_restore

        token = github_restore._restoring_as_admin.set(False)
        try:
            plan = await GitHubRestoreService()._plan_settings(db_session, dict(self.PAYLOAD))
        finally:
            github_restore._restoring_as_admin.reset(token)

        assert {"session_max_hours", "smtp_username", "smtp_from_name", "prometheus_enabled", "external_url"} <= set(
            plan.protected
        )
        assert plan.stored == {"currency": "EUR"}

    @pytest.mark.asyncio
    async def test_an_admin_restore_still_writes_them(self, db_session):
        plan = await GitHubRestoreService()._plan_settings(db_session, dict(self.PAYLOAD))

        assert "session_max_hours" not in plan.protected
        assert plan.stored["session_max_hours"] == "720"
        assert plan.stored["smtp_username"] == "relay@example.com"
