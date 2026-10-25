"""A settings restore writes only what the settings editors would accept.

Each value is checked as the editor that owns it checks it. A key no editor
writes (runtime state, a login's account state, a migration flag, anything
unknown) is not restored, and neither is a user's email code sign-in flag.
"""

import pytest
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.schemas.github_backup import RestoreCategory
from backend.app.services.github_restore import _ADDRESS_KEYS, SETTINGS_PATH, GitHubRestoreService, _CategoryTally


async def _restore(db_session, values: dict, local: dict | None = None):
    if local:
        db_session.add_all(Settings(key=key, value=value) for key, value in local.items())
        await db_session.commit()
    tally, written = _CategoryTally(), set()
    await GitHubRestoreService()._restore_settings(
        db_session, {"settings": values}, overwrite=True, tally=tally, keys_written=written
    )
    await db_session.commit()
    rows = (await db_session.execute(select(Settings))).scalars().all()
    return tally, written, {row.key: row.value for row in rows}


def _note(tally: _CategoryTally, code: str) -> dict | None:
    return next((note for note in tally.notes if note["code"] == code), None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "refused", "accepted"),
    [
        ("ha_url", "http://169.254.169.254/latest", "http://ha.local:8123"),
        ("bambuddy_internal_url", "ftp://bambuddy.local", "http://bambuddy.local:8000"),
        ("docker_compose_dir", "/opt/bambuddy; echo x", "/opt/bambuddy"),
        ("gcode_snippets", "not json", '{"X1C": {"start": ""}}'),
        ("obico_action", "explode", "notify"),
        ("energy_price_source", "mars", "fixed"),
        ("low_stock_threshold", "lots", "20"),
        ("manyfold_url", "http://169.254.169.254", "http://manyfold.local"),
        ("spoolman_url", "http://169.254.169.254:7912", "http://spoolman.local:7912"),
        ("smtp_port", "five", "587"),
    ],
)
async def test_a_value_the_editor_refuses_is_not_restored(db_session, key, refused, accepted):
    tally, written, stored = await _restore(db_session, {key: refused}, local={key: accepted})

    assert stored[key] == accepted
    assert written == set()
    note = _note(tally, "settingsInvalidSkipped")
    assert note is not None and note["params"]["keys"] == key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ha_url", "http://ha.local:8123"),
        ("docker_compose_dir", "/opt/bambuddy"),
        ("auto_archive", "true"),
        ("ams_temp_alarm", "None"),
        ("energy_cost_per_kwh", "0.31"),
        ("manyfold_url", "http://manyfold.local"),
        ("manyfold_client_id", "client"),
        ("smtp_host", "mail.local"),
        ("smtp_port", "587"),
        ("smtp_security", "ssl"),
        ("smtp_auth_enabled", "false"),
        ("smtp_from_email", "a@mail.local"),
        ("smtp_from_name", "Bambuddy"),
        ("smtp_username", "mailer"),
        ("smtp_use_tls", "true"),
    ],
)
async def test_a_value_the_editor_accepts_is_restored(db_session, key, value):
    # An address is restored only as this instance already has it.
    local = {key: value} if key in _ADDRESS_KEYS else None
    tally, written, stored = await _restore(db_session, {key: value}, local=local)

    assert stored[key] == value
    assert written == {key}
    assert _note(tally, "settingsInvalidSkipped") is None
    assert _note(tally, "settingsUnknownSkipped") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "something_from_elsewhere",
        "debug_logging_enabled",
        "ams_drying_alarm_latch",
        "announcements_feed_serial",
        "stock_alerts_notified",
        "archive_auto_purge_last_run",
        "archive_auto_purge_enabled",
        "archive_auto_purge_days",
        "archive_auto_purge_stats",
        "library_trash_retention_days",
        "library_auto_purge_enabled",
        "library_auto_purge_days",
        "library_auto_purge_include_never_printed",
        "bambu_cloud_email",
        "orca_cloud_email",
        "orca_cloud_pending_at",
        "virtual_printer_model",
        "ha_env_managed",
    ],
)
async def test_a_key_no_editor_writes_is_not_restored(db_session, key):
    tally, written, stored = await _restore(db_session, {key: "true", "currency": "EUR"})

    assert key not in stored
    assert written == {"currency"}
    note = _note(tally, "settingsUnknownSkipped")
    assert note is not None and note["params"]["keys"] == key


@pytest.mark.asyncio
async def test_a_users_email_code_sign_in_is_never_switched_by_a_restore(db_session):
    tally, written, stored = await _restore(
        db_session, {"user_1_email_2fa_enabled": "false"}, local={"user_1_email_2fa_enabled": "true"}
    )

    assert stored["user_1_email_2fa_enabled"] == "true"
    assert written == set()
    assert _note(tally, "settingsTwoFactorSkipped") is not None
    assert _note(tally, "settingsUnknownSkipped") is None


@pytest.mark.asyncio
async def test_the_preview_counts_only_what_will_be_written(db_session):
    parsed = {
        SETTINGS_PATH: {
            "settings": {
                "currency": "EUR",
                "ha_url": "http://169.254.169.254/",
                "something_from_elsewhere": "true",
                "user_1_email_2fa_enabled": "false",
            }
        }
    }

    count, detail = await GitHubRestoreService()._count_items(db_session, RestoreCategory.SETTINGS, parsed)

    assert count == 1
    assert detail is not None and detail.code == "settingsNotRestorableWillSkip"
    assert detail.params == {"count": 3}


@pytest.mark.asyncio
async def test_bambuddys_own_one_shot_flags_are_skipped_without_a_word(db_session):
    """Every backup carries them; naming them on every restore would be noise."""
    tally, written, stored = await _restore(
        db_session, {"_backfill_1425_pipeline_permissions_done": "true", "currency": "EUR"}
    )
    parsed = {SETTINGS_PATH: {"settings": {"_backfill_1425_pipeline_permissions_done": "true", "currency": "EUR"}}}
    count, detail = await GitHubRestoreService()._count_items(db_session, RestoreCategory.SETTINGS, parsed)

    assert "_backfill_1425_pipeline_permissions_done" not in stored
    assert written == {"currency"}
    assert tally.notes == []
    assert (count, detail) == (1, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["mqtt_port", "use_slicer_api", "energy_price_source", "auto_archive", "ftp_timeout"])
async def test_a_saved_null_is_refused_where_the_settings_cannot_read_one(db_session, key):
    tally, written, stored = await _restore(db_session, {key: "None"})

    assert key not in stored
    assert _note(tally, "settingsInvalidSkipped") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ams_temp_alarm", "default_printer_id", "open_in_slicer"])
async def test_a_saved_null_is_restored_where_the_settings_read_one(db_session, key):
    _tally, written, stored = await _restore(db_session, {key: "None"})

    assert stored[key] == "None"
    assert written == {key}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value", "stored_as"),
    [
        ("smtp_port", "587.0", "587"),
        ("smtp_port", " 587", "587"),
        ("smtp_auth_enabled", "1", "true"),
        ("mqtt_enabled", "yes", "true"),
        ("mqtt_port", "1883.0", "1883"),
        ("auto_archive", "True", "true"),
        ("smtp_use_tls", "0", "false"),
    ],
)
async def test_a_value_is_written_in_the_form_its_editor_stores(db_session, key, value, stored_as):
    local = {key: stored_as} if key in _ADDRESS_KEYS else None
    _tally, written, stored = await _restore(db_session, {key: value}, local=local)

    assert stored[key] == stored_as
    assert written == {key}


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [{"external_url": "https://bambuddy.here.example"}, None])
async def test_the_address_sign_in_links_point_to_is_never_changed_by_a_restore(db_session, local):
    """Password reset and welcome links are built from it: a backup naming
    another address would send their tokens there."""
    tally, written, stored = await _restore(
        db_session, {"external_url": "https://elsewhere.example", "currency": "EUR"}, local=local
    )
    parsed = {SETTINGS_PATH: {"settings": {"external_url": "https://elsewhere.example", "currency": "EUR"}}}
    count, detail = await GitHubRestoreService()._count_items(db_session, RestoreCategory.SETTINGS, parsed)

    assert stored.get("external_url") == (local or {}).get("external_url")
    assert written == {"currency"}
    note = _note(tally, "settingsAddressKept")
    assert note is not None and note["params"]["keys"] == "external_url"
    assert (count, detail.code if detail else None) == (1, "settingsNotRestorableWillSkip")


@pytest.mark.asyncio
async def test_the_same_sign_in_link_address_restores_quietly(db_session):
    tally, _written, stored = await _restore(
        db_session,
        {"external_url": "https://bambuddy.here.example"},
        local={"external_url": "https://bambuddy.here.example"},
    )

    assert stored["external_url"] == "https://bambuddy.here.example"
    assert _note(tally, "settingsAddressKept") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "here", "elsewhere"),
    [
        ("external_url", "https://bambuddy.here.example", "https://elsewhere.example"),
        ("smtp_host", "mail.here.example", "mail.elsewhere.example"),
        ("smtp_port", "587", "25"),
        ("smtp_security", "starttls", "none"),
        ("smtp_use_tls", "true", "false"),
        ("local_backup_path", "/data/backups", "/srv/share"),
        ("mqtt_broker", "broker.here.example", "broker.elsewhere.example"),
        ("mqtt_port", "1883", "1884"),
        ("mqtt_use_tls", "false", "true"),
        ("ha_url", "http://ha.here.example:8123", "http://ha.elsewhere.example:8123"),
        ("obico_ml_url", "http://obico.here.example:3333", "http://obico.elsewhere.example:3333"),
        ("bambuddy_internal_url", "http://bambuddy.here.example:8000", "http://bambuddy.elsewhere.example:8000"),
        ("orcaslicer_api_url", "http://orca.here.example:3000", "http://orca.elsewhere.example:3000"),
        ("bambu_studio_api_url", "http://studio.here.example:3000", "http://studio.elsewhere.example:3000"),
        ("spoolman_url", "http://spoolman.here.example:7912", "http://spoolman.elsewhere.example:7912"),
        ("manyfold_url", "http://manyfold.here.example", "http://manyfold.elsewhere.example"),
    ],
)
@pytest.mark.parametrize("has_row", [True, False])
async def test_a_backup_never_changes_where_bambuddy_sends_things(db_session, key, here, elsewhere, has_row):
    """Mail, backups, printer status, snapshots, model files: an address from a
    backup is kept as this instance has it, set or not."""
    tally, written, stored = await _restore(db_session, {key: elsewhere}, local={key: here} if has_row else None)

    assert stored.get(key) == (here if has_row else None)
    assert written == set()
    note = _note(tally, "settingsAddressKept")
    assert note is not None and note["params"]["keys"] == key


def test_every_address_key_is_one_a_restore_would_otherwise_write():
    """An address key nothing checks would be dead weight in the list."""
    from backend.app.services.github_restore import _checked_setting

    assert set(_ADDRESS_KEYS) == {key for key in _ADDRESS_KEYS if _checked_setting(key, "")[0] is not None}


def test_the_backup_and_the_restore_agree_on_which_keys_are_settings():
    """The backup keeps exactly the keys a restore writes; the two must not drift."""
    from backend.app.schemas.settings import AppSettingsUpdate
    from backend.app.services.github_restore import _EXTRA_RESTORABLE_KEYS, _checked_setting, is_restorable_setting_key

    keys = {*AppSettingsUpdate.model_fields, *_EXTRA_RESTORABLE_KEYS, "manyfold_url", "manyfold_client_id"}
    keys |= {"vapid_private_key", "dark_theme", "_backfill_x_done", "user_1_email_2fa_enabled", "auth_enabled"}
    for key in keys:
        assert is_restorable_setting_key(key) is (_checked_setting(key, "")[0] is not None), key
