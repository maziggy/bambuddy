"""A restore never sends a credential kept here to a place the backup names.

Credentials are never restored, so the local one stays. The settings that say
where it is sent (broker, port, TLS, URL) are left as they are when the backup
would change them, as the settings editor refuses the same change without the
credential being entered again.
"""

import pytest
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.schemas.github_backup import RestoreCategory
from backend.app.services.github_restore import SETTINGS_PATH, GitHubRestoreService, _CategoryTally


async def _local(db_session, rows: dict[str, str]) -> None:
    db_session.add_all(Settings(key=key, value=value) for key, value in rows.items())
    await db_session.commit()


async def _restore(db_session, values: dict, overwrite: bool = True) -> tuple[_CategoryTally, set[str]]:
    tally, written = _CategoryTally(), set()
    await GitHubRestoreService()._restore_settings(
        db_session, {"settings": values}, overwrite=overwrite, tally=tally, keys_written=written
    )
    await db_session.commit()
    return tally, written


async def _value(db_session, key: str) -> str | None:
    row = (await db_session.execute(select(Settings).where(Settings.key == key))).scalar_one_or_none()
    return row.value if row else None


@pytest.mark.asyncio
async def test_a_changed_broker_is_left_as_it_is_while_the_password_is_kept(db_session):
    await _local(db_session, {"mqtt_password": "local-value", "mqtt_broker": "here.local"})

    tally, written = await _restore(
        db_session, {"mqtt_broker": "elsewhere.example", "mqtt_password": "********", "currency": "EUR"}
    )

    assert await _value(db_session, "mqtt_broker") == "here.local"
    assert await _value(db_session, "mqtt_password") == "local-value"
    assert written == {"currency"}
    kept = [note for note in tally.notes if note["code"] == "settingsDestinationKept"]
    assert kept and kept[0]["params"]["keys"] == "mqtt_broker"


@pytest.mark.asyncio
async def test_without_a_stored_credential_the_destination_is_still_kept(db_session):
    """No credential to protect, but the broker still decides where printer
    status goes: kept as an address."""
    await _local(db_session, {"mqtt_broker": "here.local", "mqtt_password": "  "})

    tally, written = await _restore(db_session, {"mqtt_broker": "elsewhere.example"})

    assert await _value(db_session, "mqtt_broker") == "here.local"
    assert written == set()
    assert "settingsAddressKept" in [note["code"] for note in tally.notes]


@pytest.mark.asyncio
async def test_an_unchanged_destination_is_restored_as_usual(db_session):
    await _local(db_session, {"mqtt_password": "local-value", "mqtt_broker": "here.local", "mqtt_use_tls": "false"})

    tally, written = await _restore(
        db_session, {"mqtt_broker": "here.local", "mqtt_port": "1883", "mqtt_use_tls": False}
    )

    assert written == {"mqtt_broker", "mqtt_port", "mqtt_use_tls"}
    assert "settingsDestinationKept" not in [note["code"] for note in tally.notes]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [("mqtt_port", "8883"), ("mqtt_use_tls", "true"), ("mqtt_use_tls", True)],
)
async def test_a_destination_never_saved_here_counts_from_its_default(db_session, key, value):
    await _local(db_session, {"mqtt_password": "local-value"})

    _, written = await _restore(db_session, {key: value})

    assert await _value(db_session, key) is None
    assert written == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("secret", "destination"),
    [("ha_token", "ha_url"), ("obico_ml_token", "obico_ml_url")],
)
async def test_a_token_does_not_follow_a_changed_url(db_session, monkeypatch, secret, destination):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    await _local(db_session, {secret: "local-value", destination: "http://here.local"})

    _, written = await _restore(db_session, {destination: "http://elsewhere.example"})

    assert await _value(db_session, destination) == "http://here.local"
    assert written == set()


@pytest.mark.asyncio
async def test_with_a_deployment_token_a_changed_url_is_still_kept_as_an_address(db_session, monkeypatch):
    """The environment's token may follow a URL change in the editor, but a
    restore never changes where Bambuddy sends things."""
    monkeypatch.setenv("HA_TOKEN", "env-value")
    await _local(db_session, {"ha_token": "local-value", "ha_url": "http://here.local"})

    tally, _written = await _restore(db_session, {"ha_url": "http://elsewhere.example"})

    assert await _value(db_session, "ha_url") == "http://here.local"
    assert "settingsAddressKept" in [note["code"] for note in tally.notes]


@pytest.mark.asyncio
async def test_the_preview_counts_and_names_what_is_left_as_it_is(db_session):
    await _local(db_session, {"mqtt_password": "local-value", "mqtt_broker": "here.local"})
    parsed = {SETTINGS_PATH: {"settings": {"mqtt_broker": "elsewhere.example", "currency": "EUR"}}}

    count, detail = await GitHubRestoreService()._count_items(db_session, RestoreCategory.SETTINGS, parsed)

    assert count == 1
    assert detail is not None and detail.code == "settingsDestinationWillKeep"
    assert detail.params == {"destination": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "local", "restored"),
    [
        ("smtp_host", "mail.here.local", "mail.elsewhere.example"),
        ("smtp_port", "587", "25"),
        ("smtp_security", "starttls", "none"),
        ("smtp_use_tls", "true", "false"),
    ],
)
async def test_the_smtp_password_does_not_follow_a_changed_server(db_session, key, local, restored):
    await _local(db_session, {"smtp_password": "local-value", key: local})

    _, written = await _restore(db_session, {key: restored})

    assert await _value(db_session, key) == local
    assert written == set()


@pytest.mark.asyncio
async def test_a_destination_without_a_row_here_is_left_alone_while_a_credential_is_kept(db_session):
    """Without a row there is no value to compare with: counted as a change."""
    await _local(db_session, {"smtp_password": "local-value", "smtp_host": "mail.here.local"})

    _, written = await _restore(db_session, {"smtp_security": "none"})

    assert await _value(db_session, "smtp_security") is None
    assert written == set()


@pytest.mark.asyncio
async def test_the_manyfold_secret_does_not_follow_a_changed_url(db_session):
    await _local(db_session, {"manyfold_client_secret": "local-value", "manyfold_url": "http://here.local"})

    _, written = await _restore(db_session, {"manyfold_url": "http://elsewhere.example", "manyfold_client_id": "id"})

    assert await _value(db_session, "manyfold_url") == "http://here.local"
    assert written == {"manyfold_client_id"}


@pytest.mark.asyncio
async def test_without_a_stored_smtp_password_the_server_is_still_kept(db_session):
    """An unauthenticated relay: no password to protect, but the server still
    receives every email, password reset tokens among them."""
    await _local(db_session, {"smtp_host": "mail.here.local"})

    _, written = await _restore(db_session, {"smtp_host": "mail.elsewhere.example", "smtp_security": "ssl"})

    assert await _value(db_session, "smtp_host") == "mail.here.local"
    assert await _value(db_session, "smtp_security") is None
    assert written == set()


@pytest.mark.asyncio
async def test_a_destination_is_compared_as_it_would_be_written(db_session):
    """ " 587" is stored as "587": the same port, so not held back."""
    await _local(db_session, {"smtp_password": "local-value", "smtp_port": "587"})

    tally, written = await _restore(db_session, {"smtp_port": " 587"})

    assert written == {"smtp_port"}
    assert "settingsDestinationKept" not in [note["code"] for note in tally.notes]
