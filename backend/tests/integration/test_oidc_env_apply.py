"""Upserting the env-managed OIDC provider (#2593).

Startup applies BAMBUDDY_OIDC_* to the database. The row is updated in place,
never delete-recreated: user_oidc_links.provider_id is FK ON DELETE CASCADE, so
recreating the provider would silently unlink every account bound to it.
"""

from __future__ import annotations

import logging
import os

import pytest
from sqlalchemy import select

from backend.app.core.oidc_env import apply_env_oidc_provider
from backend.app.models.oidc_provider import OIDCProvider

REQUIRED = {
    "BAMBUDDY_OIDC_NAME": "Keycloak",
    "BAMBUDDY_OIDC_ISSUER_URL": "https://sso.example.com/realms/main",
    "BAMBUDDY_OIDC_CLIENT_ID": "bambuddy",
    "BAMBUDDY_OIDC_CLIENT_SECRET": "s3cr3t",
}

ALL_VARS = (
    *REQUIRED,
    "BAMBUDDY_OIDC_SCOPES",
    "BAMBUDDY_OIDC_ENABLED",
    "BAMBUDDY_OIDC_AUTO_CREATE_USERS",
    "BAMBUDDY_OIDC_AUTO_LINK_EXISTING",
    "BAMBUDDY_OIDC_EMAIL_CLAIM",
    "BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED",
    "BAMBUDDY_OIDC_ICON_URL",
    "BAMBUDDY_OIDC_AUTOLOGIN",
    "BAMBUDDY_OIDC_DEFAULT_GROUP",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ALL_VARS:
        monkeypatch.delenv(key, raising=False)


def _configure(monkeypatch, **overrides):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)


async def _env_provider(db_session) -> OIDCProvider | None:
    result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.is_env_managed.is_(True)))
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_creates_the_provider_from_env(db_session, monkeypatch):
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None
    assert provider.name == "Keycloak"
    assert provider.client_id == "bambuddy"
    assert provider.is_env_managed is True
    assert provider.client_secret == "s3cr3t"  # property decrypts


@pytest.mark.asyncio
async def test_a_changed_var_updates_the_same_row(db_session, monkeypatch):
    """The id must survive: user_oidc_links references it with ON DELETE
    CASCADE, so a delete-recreate would unlink every bound account."""
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    original_id = (await _env_provider(db_session)).id

    monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_ID", "rotated")
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider.id == original_id
    assert provider.client_id == "rotated"


@pytest.mark.asyncio
async def test_removing_the_env_config_disables_but_keeps_the_row(db_session, monkeypatch):
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    original_id = (await _env_provider(db_session)).id

    for key in ALL_VARS:
        monkeypatch.delenv(key, raising=False)
    await apply_env_oidc_provider(db_session)

    # Looked up by name, not by the flag: releasing the provider clears the flag,
    # and the point of this test is that the ROW survives either way.
    result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "Keycloak"))
    provider = result.scalar_one_or_none()
    assert provider is not None, "deleting would cascade away every account link"
    assert provider.id == original_id
    assert provider.is_enabled is False


@pytest.mark.asyncio
async def test_env_autologin_clears_it_on_other_providers(db_session, monkeypatch):
    """Only one provider may be the autologin target; the env one wins."""
    ui_provider = OIDCProvider(
        name="UI provider",
        issuer_url="https://other.example.com",
        client_id="ui",
        is_autologin=True,
    )
    ui_provider.client_secret = "ui-secret"
    db_session.add(ui_provider)
    await db_session.commit()

    _configure(monkeypatch, BAMBUDDY_OIDC_AUTOLOGIN="true")
    await apply_env_oidc_provider(db_session)

    await db_session.refresh(ui_provider)
    assert (await _env_provider(db_session)).is_autologin is True
    assert ui_provider.is_autologin is False


@pytest.mark.asyncio
async def test_a_ui_provider_is_otherwise_left_alone(db_session, monkeypatch):
    ui_provider = OIDCProvider(name="UI provider", issuer_url="https://other.example.com", client_id="ui")
    ui_provider.client_secret = "ui-secret"
    db_session.add(ui_provider)
    await db_session.commit()

    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)

    await db_session.refresh(ui_provider)
    assert ui_provider.is_env_managed is False
    assert ui_provider.is_enabled is True
    assert ui_provider.client_id == "ui"


@pytest.mark.asyncio
async def test_an_unsafe_auto_link_config_is_skipped_not_raised(db_session, monkeypatch):
    """auto-link + unverified email is the SEC-1 account-takeover shape. The
    schema rejects it for the UI, and env config must not be a way around that
    -- but a bad variable must not stop the app from booting either."""
    _configure(
        monkeypatch,
        BAMBUDDY_OIDC_AUTO_LINK_EXISTING="true",
        BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED="false",
    )

    await apply_env_oidc_provider(db_session)

    assert await _env_provider(db_session) is None


@pytest.mark.asyncio
async def test_a_rejected_config_never_logs_the_client_secret(db_session, monkeypatch, caplog):
    """client_secret has max_length=512, so an over-long value raises
    string_too_long. The rejection must be logged without the value: str(exc)
    embeds input_value=..., which would leak the secret (no-secrets-in-logs)."""
    secret = "S3CR3T" * 100  # > 512 chars -> ValidationError on client_secret
    _configure(monkeypatch, BAMBUDDY_OIDC_CLIENT_SECRET=secret)

    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)

    assert await _env_provider(db_session) is None  # rejected, not booted-through
    assert "rejected" in caplog.text  # the rejection was actually logged
    assert secret not in caplog.text
    assert "S3CR3T" not in caplog.text  # not even a fragment of the value


# --- an unrecognized boolean is rejected, not guessed --------------------------
# `_env_bool` used to return the default for anything outside {true,1,yes}, so
# BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED=on silently read as OFF and
# BAMBUDDY_OIDC_ENABLED=on silently disabled the provider. Strict parsing
# refuses the config instead -- through the same clean path a bad
# DEFAULT_GROUP or a ValidationError already uses, so a typo never releases a
# provider that was running fine.


@pytest.mark.asyncio
async def test_an_unrecognized_require_email_verified_leaves_a_running_provider_intact(db_session, monkeypatch, caplog):
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    original = await _env_provider(db_session)
    original_id, original_enabled = original.id, original.is_enabled

    monkeypatch.setenv("BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED", "on")
    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None, "a typo must not release the provider"
    assert provider.id == original_id
    assert provider.is_enabled == original_enabled
    assert provider.is_env_managed is True
    assert "rejected" in caplog.text
    assert "BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED" in caplog.text


@pytest.mark.asyncio
async def test_an_unrecognized_enabled_leaves_a_running_provider_intact(db_session, monkeypatch, caplog):
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    original = await _env_provider(db_session)
    original_id, original_enabled = original.id, original.is_enabled

    monkeypatch.setenv("BAMBUDDY_OIDC_ENABLED", "on")
    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None, "a typo must not release the provider"
    assert provider.id == original_id
    assert provider.is_enabled == original_enabled
    assert provider.is_env_managed is True
    assert "rejected" in caplog.text
    assert "BAMBUDDY_OIDC_ENABLED" in caplog.text


@pytest.mark.asyncio
async def test_a_non_validation_error_is_survivable_and_leaks_nothing(db_session, monkeypatch, caplog):
    """The generic except branch handles anything that isn't a ValidationError
    (e.g. a library call raising mid-construction). It must not stop boot and,
    since such a message could carry a configured value, must log only the
    exception class -- never str(exc)."""
    # oidc_env imports OIDCProviderCreate inside the function (to avoid an
    # import cycle), so patch it at its source module, not on oidc_env.
    import backend.app.schemas.auth as auth_schemas

    def _raise(**_kwargs):
        raise RuntimeError("boom leaked-secret")

    monkeypatch.setattr(auth_schemas, "OIDCProviderCreate", _raise)
    _configure(monkeypatch, BAMBUDDY_OIDC_CLIENT_SECRET="leaked-secret")

    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)  # must not raise

    assert await _env_provider(db_session) is None
    assert "could not be applied" in caplog.text
    assert "RuntimeError" in caplog.text  # class is logged...
    assert "leaked-secret" not in caplog.text  # ...but nothing from the message


@pytest.mark.asyncio
async def test_a_commit_failure_is_survivable_and_leaks_nothing(db_session, monkeypatch, caplog):
    """The upsert's db.execute/db.commit calls sit outside the inner
    ValidationError guard -- a Postgres blip or a SQLite WAL lock at startup
    must not propagate out of the lifespan either. Only the exception class
    may be logged, never str(exc), since a DB error message can echo a
    configured value."""

    async def _raise_on_commit():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db_session, "commit", _raise_on_commit)
    _configure(monkeypatch, BAMBUDDY_OIDC_CLIENT_SECRET="leaked-secret")

    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)  # must not raise

    assert "could not be applied" in caplog.text
    assert "RuntimeError" in caplog.text  # class is logged...
    assert "leaked-secret" not in caplog.text  # ...but nothing from the message


@pytest.mark.asyncio
async def test_a_failing_rollback_is_also_survivable(db_session, monkeypatch, caplog):
    """The handler rolls back after a failed commit -- but rollback on a wedged
    connection can raise too, and 'never raises' has to hold for that as well
    or the boot dies on the recovery path. The rollback is suppressed."""

    async def _raise_on_commit():
        raise RuntimeError("database is locked")

    async def _raise_on_rollback():
        raise RuntimeError("connection is closed")

    monkeypatch.setattr(db_session, "commit", _raise_on_commit)
    monkeypatch.setattr(db_session, "rollback", _raise_on_rollback)
    _configure(monkeypatch, BAMBUDDY_OIDC_CLIENT_SECRET="leaked-secret")

    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)  # must not raise, even here

    assert "could not be applied" in caplog.text
    assert "leaked-secret" not in caplog.text


@pytest.mark.asyncio
async def test_applying_twice_without_changes_is_a_no_op(db_session, monkeypatch):
    """Every boot re-applies; the second run must not create a second row."""
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    await apply_env_oidc_provider(db_session)

    result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.is_env_managed.is_(True)))
    assert len(result.scalars().all()) == 1


# --- identity is the name, not the flag ---------------------------------------
# The provider is looked up by BAMBUDDY_OIDC_NAME, which is unique on the table.
# Matching on is_env_managed instead made three things impossible: adopting a
# provider that already carries the name (the insert hit the unique constraint
# and took startup down with it), releasing the provider when the config goes
# away, and finding it again afterwards.


@pytest.mark.asyncio
async def test_a_name_collision_adopts_the_existing_provider(db_session, monkeypatch):
    """An operator who names the env provider after one they created in the UI
    must not end up with an app that refuses to boot."""
    ui_provider = OIDCProvider(name="Keycloak", issuer_url="https://old.example.com", client_id="ui-client")
    ui_provider.client_secret = "ui-secret"
    db_session.add(ui_provider)
    await db_session.commit()
    original_id = ui_provider.id

    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None
    assert provider.id == original_id, "adopted, not duplicated"
    assert provider.client_id == "bambuddy"

    result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "Keycloak"))
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_adopting_a_ui_provider_logs_a_distinct_warning(db_session, monkeypatch, caplog):
    """Overwriting a UI-created provider in place is a bigger deal than a
    routine re-apply -- it must not be silent at the same INFO level."""
    ui_provider = OIDCProvider(name="Keycloak", issuer_url="https://old.example.com", client_id="ui-client")
    ui_provider.client_secret = "ui-secret"
    db_session.add(ui_provider)
    await db_session.commit()

    _configure(monkeypatch)
    with caplog.at_level(logging.INFO):
        await apply_env_oidc_provider(db_session)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("adopted" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_a_routine_reapply_does_not_log_an_adoption_warning(db_session, monkeypatch, caplog):
    """The same provider re-applying on the next boot is not an adoption --
    it was already env-managed."""
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        await apply_env_oidc_provider(db_session)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert not any("adopted" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_removing_the_config_releases_the_provider_to_the_ui(db_session, monkeypatch):
    """Nothing manages it any more, so the API must stop refusing edits and
    deletes -- otherwise the row is a dead end only reachable via the database."""
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)

    for key in ALL_VARS:
        monkeypatch.delenv(key, raising=False)
    await apply_env_oidc_provider(db_session)

    result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "Keycloak"))
    provider = result.scalar_one()
    assert provider.is_enabled is False
    assert provider.is_env_managed is False


@pytest.mark.asyncio
async def test_restoring_the_config_finds_the_same_row_again(db_session, monkeypatch):
    """The account links hang off this row; a second provider would orphan them."""
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    original_id = (await _env_provider(db_session)).id

    for key in ALL_VARS:
        monkeypatch.delenv(key, raising=False)
    await apply_env_oidc_provider(db_session)

    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider.id == original_id
    assert provider.is_enabled is True


@pytest.mark.asyncio
async def test_the_issuer_and_client_can_change_under_the_same_name(db_session, monkeypatch):
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    original_id = (await _env_provider(db_session)).id

    monkeypatch.setenv("BAMBUDDY_OIDC_ISSUER_URL", "https://sso.example.com/realms/other")
    monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_ID", "rotated")
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider.id == original_id
    assert provider.issuer_url == "https://sso.example.com/realms/other"
    assert provider.client_id == "rotated"


# --- a rename must not leave the old row managed -------------------------------
# Identity is the name, so renaming BAMBUDDY_OIDC_NAME matches nothing and
# creates a second row. Leaving the flag on the first one is what makes that
# fatal: it stays enabled with a stale issuer and secret on the login page, the
# API refuses every edit/disable/delete on it (409), and the release path's
# scalar_one_or_none() then raises MultipleResultsFound out of the lifespan --
# the app stops booting. Both states are reachable by ordinary config edits.


async def _env_managed(db_session) -> list[OIDCProvider]:
    result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.is_env_managed.is_(True)))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_renaming_the_provider_releases_the_row_it_managed_before(db_session, monkeypatch):
    _configure(monkeypatch, BAMBUDDY_OIDC_AUTOLOGIN="true")
    await apply_env_oidc_provider(db_session)
    old_id = (await _env_provider(db_session)).id

    monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "Authentik")
    await apply_env_oidc_provider(db_session)

    managed = await _env_managed(db_session)
    assert [p.name for p in managed] == ["Authentik"], "exactly one row may carry the flag"

    old = (await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == old_id))).scalar_one()
    # Released, not deleted -- user_oidc_links.provider_id cascades.
    assert old.is_env_managed is False
    assert old.is_enabled is False, "a stale issuer must not stay on the login page"
    assert old.is_autologin is False


@pytest.mark.asyncio
async def test_boot_survives_removing_the_config_after_a_rename(db_session, monkeypatch):
    """The MultipleResultsFound path: rename, then unset. Must not raise."""
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "Authentik")
    await apply_env_oidc_provider(db_session)

    for key in ALL_VARS:
        monkeypatch.delenv(key, raising=False)
    await apply_env_oidc_provider(db_session)  # must not raise

    assert await _env_managed(db_session) == []
    names = (await db_session.execute(select(OIDCProvider.name))).scalars().all()
    assert sorted(names) == ["Authentik", "Keycloak"], "both rows survive, both released"


@pytest.mark.asyncio
async def test_every_managed_row_is_released_not_just_one(db_session, monkeypatch):
    """The upsert's sweep should keep this at one row. Should is not enforced by
    the schema, and the cost of being wrong is the whole release path raising
    MultipleResultsFound out of the lifespan -- so it releases what it finds."""
    for name in ("Keycloak", "Authentik"):
        stale = OIDCProvider(
            name=name,
            issuer_url="https://sso.example.com/realms/main",
            client_id="bambuddy",
            is_env_managed=True,
        )
        stale.client_secret = "s3cr3t"
        db_session.add(stale)
    await db_session.commit()

    await apply_env_oidc_provider(db_session)  # no vars set -> release path

    assert await _env_managed(db_session) == []


@pytest.mark.asyncio
async def test_releasing_the_provider_clears_autologin(db_session, monkeypatch):
    """is_enabled and is_env_managed alone leave a UI-editable row carrying a
    latent autologin claim: update_oidc_provider only runs the exclusivity
    sweep when a request sets is_autologin=True, so merely re-enabling this row
    makes it the autologin target again."""
    _configure(monkeypatch, BAMBUDDY_OIDC_AUTOLOGIN="true")
    await apply_env_oidc_provider(db_session)
    assert (await _env_provider(db_session)).is_autologin is True

    for key in ALL_VARS:
        monkeypatch.delenv(key, raising=False)
    await apply_env_oidc_provider(db_session)

    released = (await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "Keycloak"))).scalar_one()
    assert released.is_autologin is False


# --- default group by name -----------------------------------------------------
# Group ids are not stable across installs, so a declarative deployment cannot
# name one by id. Without this, every auto-created user falls back to Viewers
# (routes/mfa.py) and the env lock means the UI cannot correct the provider.


async def _group(db_session, name: str):
    from backend.app.models.group import Group

    group = Group(name=name, description=f"Test group {name}")
    db_session.add(group)
    await db_session.commit()
    return group


@pytest.mark.asyncio
async def test_the_default_group_is_resolved_by_name(db_session, monkeypatch):
    group = await _group(db_session, "Operators")
    _configure(monkeypatch, BAMBUDDY_OIDC_DEFAULT_GROUP="Operators")

    await apply_env_oidc_provider(db_session)

    assert (await _env_provider(db_session)).default_group_id == group.id


@pytest.mark.asyncio
async def test_an_unknown_group_name_is_rejected_rather_than_defaulted(db_session, monkeypatch, caplog):
    """Silently falling back to Viewers is how a typo mints under-privileged
    users for weeks. The API answers 400 for a default_group_id that does not
    exist; env config gets the same answer, logged and survivable."""
    _configure(monkeypatch, BAMBUDDY_OIDC_DEFAULT_GROUP="Nope")

    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)

    assert await _env_provider(db_session) is None
    assert "BAMBUDDY_OIDC_DEFAULT_GROUP" in caplog.text
    assert "Nope" in caplog.text


@pytest.mark.asyncio
async def test_an_unknown_group_name_leaves_the_previous_provider_intact(db_session, monkeypatch):
    """Rejection happens before the upsert, so the running config survives a
    bad edit -- the provider keeps working until the operator fixes the name."""
    group = await _group(db_session, "Operators")
    _configure(monkeypatch, BAMBUDDY_OIDC_DEFAULT_GROUP="Operators")
    await apply_env_oidc_provider(db_session)

    monkeypatch.setenv("BAMBUDDY_OIDC_DEFAULT_GROUP", "Typo")
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None
    assert provider.default_group_id == group.id


@pytest.mark.asyncio
async def test_no_group_variable_leaves_the_default_group_unset(db_session, monkeypatch):
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)

    assert (await _env_provider(db_session)).default_group_id is None


@pytest.mark.asyncio
async def test_removing_the_group_variable_clears_the_default_group(db_session, monkeypatch):
    """The environment is the whole truth for this row; a group that is no
    longer declared must not linger, since the lock blocks removing it in the UI."""
    await _group(db_session, "Operators")
    _configure(monkeypatch, BAMBUDDY_OIDC_DEFAULT_GROUP="Operators")
    await apply_env_oidc_provider(db_session)

    monkeypatch.delenv("BAMBUDDY_OIDC_DEFAULT_GROUP")
    await apply_env_oidc_provider(db_session)

    assert (await _env_provider(db_session)).default_group_id is None


@pytest.mark.asyncio
async def test_an_empty_group_variable_counts_as_unset(db_session, monkeypatch):
    """Same rule the required vars follow: an empty value in a compose file is
    a forgotten value, not a request to reject the config."""
    _configure(monkeypatch, BAMBUDDY_OIDC_DEFAULT_GROUP="")
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None
    assert provider.default_group_id is None


# --- blank optional strings count as unset, not a refusal ---------------------
# `.env.example` ships `# BAMBUDDY_OIDC_ICON_URL=` commented out, so uncommenting
# it must not take the provider down -- same rule default_group already follows.


@pytest.mark.asyncio
async def test_a_blank_scopes_still_creates_the_provider(db_session, monkeypatch):
    _configure(monkeypatch, BAMBUDDY_OIDC_SCOPES="")
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None, "a blank optional var must not refuse the whole provider"
    assert provider.scopes == "openid email profile"


@pytest.mark.asyncio
async def test_a_blank_email_claim_still_creates_the_provider(db_session, monkeypatch):
    _configure(monkeypatch, BAMBUDDY_OIDC_EMAIL_CLAIM="")
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None, "a blank optional var must not refuse the whole provider"
    assert provider.email_claim == "email"


@pytest.mark.asyncio
async def test_a_blank_icon_url_still_creates_the_provider(db_session, monkeypatch):
    _configure(monkeypatch, BAMBUDDY_OIDC_ICON_URL="")
    await apply_env_oidc_provider(db_session)

    provider = await _env_provider(db_session)
    assert provider is not None, "a blank optional var must not refuse the whole provider"
    assert provider.icon_url is None


# --- account links and collision behavior ------------------------------------


@pytest.mark.asyncio
async def test_renaming_to_match_a_ui_provider_adopts_it_and_releases_the_old_row(db_session, monkeypatch):
    """New name collides with existing UI provider: env config adopts that row,
    old env-managed row is released. Identity is the name, so the collision is
    resolved by matching the new name against the table."""
    # Start with env-managed "Keycloak"
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    old_id = (await _env_provider(db_session)).id

    # Add a UI provider named "Authentik"
    ui_provider = OIDCProvider(name="Authentik", issuer_url="https://auth.example.com", client_id="ui-client")
    ui_provider.client_secret = "ui-secret"
    db_session.add(ui_provider)
    await db_session.commit()
    ui_id = ui_provider.id

    # Rename env provider to "Authentik" — matches the UI provider
    monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "Authentik")
    await apply_env_oidc_provider(db_session)

    # The UI provider is adopted and becomes env-managed
    provider = await _env_provider(db_session)
    assert provider.id == ui_id, "adopted the UI provider"
    assert provider.name == "Authentik"
    assert provider.client_id == "bambuddy"  # updated from env
    assert provider.is_env_managed is True

    # The old Keycloak row is released
    old = (await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == old_id))).scalar_one()
    assert old.name == "Keycloak"
    assert old.is_env_managed is False
    assert old.is_enabled is False


@pytest.mark.asyncio
async def test_account_links_survive_a_provider_rename(db_session, monkeypatch):
    """The provider row is never deleted, only updated: user_oidc_links FK
    ON DELETE CASCADE must not be triggered by a rename."""
    from backend.app.models.oidc_provider import UserOIDCLink
    from backend.app.models.user import User

    # Create a user and link it to the env-managed provider
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    provider_id = (await _env_provider(db_session)).id

    user = User(username="testuser", email="test@example.com")
    db_session.add(user)
    await db_session.flush()

    link = UserOIDCLink(
        user_id=user.id,
        provider_id=provider_id,
        provider_user_id="oidc-sub-12345",
        provider_email="test@idp.example.com",
    )
    db_session.add(link)
    await db_session.commit()

    # Rename the env provider
    monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "Authentik")
    await apply_env_oidc_provider(db_session)

    # The link still exists, pointing to the old row (which is now released)
    result = await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.provider_id == provider_id))
    links = result.scalars().all()
    assert len(links) == 1
    assert links[0].provider_user_id == "oidc-sub-12345"


@pytest.mark.asyncio
async def test_renaming_with_autologin_updates_the_exclusivity_sweep(db_session, monkeypatch):
    """When renamed env config has autologin=true, the sweep clears autologin
    from other rows. The old row is released (autologin cleared there too)."""
    # Setup: env provider "Keycloak" with autologin
    _configure(monkeypatch, BAMBUDDY_OIDC_AUTOLOGIN="true")
    await apply_env_oidc_provider(db_session)
    old_id = (await _env_provider(db_session)).id
    assert (await _env_provider(db_session)).is_autologin is True

    # Another UI provider also has autologin
    ui_provider = OIDCProvider(name="UI", issuer_url="https://ui.example.com", client_id="ui")
    ui_provider.client_secret = "secret"
    ui_provider.is_autologin = True
    db_session.add(ui_provider)
    await db_session.commit()

    # Rename env provider to "Authentik" with autologin=true
    monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "Authentik")
    await apply_env_oidc_provider(db_session)

    # New row is the autologin target
    new_provider = await _env_provider(db_session)
    assert new_provider.name == "Authentik"
    assert new_provider.is_autologin is True

    # Old row is released and autologin cleared
    old = (await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == old_id))).scalar_one()
    assert old.is_env_managed is False
    assert old.is_autologin is False

    # UI provider autologin is cleared (only env-managed can be autologin now)
    await db_session.refresh(ui_provider)
    assert ui_provider.is_autologin is False


@pytest.mark.asyncio
async def test_group_name_matching_is_case_sensitive(db_session, monkeypatch, caplog):
    """Group name is resolved by exact match; 'operators' != 'Operators'."""
    await _group(db_session, "Operators")  # capital O
    _configure(monkeypatch, BAMBUDDY_OIDC_DEFAULT_GROUP="operators")  # lowercase

    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)

    # Config is rejected
    assert await _env_provider(db_session) is None
    assert "operators" in caplog.text
    assert "BAMBUDDY_OIDC_DEFAULT_GROUP" in caplog.text


@pytest.mark.asyncio
async def test_group_name_rejection_does_not_log_the_secret(db_session, monkeypatch, caplog):
    """Group resolution happens before schema validation, so the secret is
    not yet in scope, but verify it's not leaked by the error path."""
    _configure(monkeypatch, BAMBUDDY_OIDC_DEFAULT_GROUP="NonExistent")
    secret = os.environ["BAMBUDDY_OIDC_CLIENT_SECRET"]

    with caplog.at_level(logging.ERROR):
        await apply_env_oidc_provider(db_session)

    # Config is rejected but secret is safe
    assert await _env_provider(db_session) is None
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_restoring_env_config_after_rename_then_unset_finds_the_original_row(db_session, monkeypatch):
    """Rename Keycloak → Authentik, unset everything, restore Keycloak.
    Must re-enable the original row, not create a new one."""
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)
    original_id = (await _env_provider(db_session)).id

    # Rename to Authentik
    monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "Authentik")
    await apply_env_oidc_provider(db_session)
    assert (await _env_provider(db_session)).name == "Authentik"

    # Unset everything
    for key in ALL_VARS:
        monkeypatch.delenv(key, raising=False)
    await apply_env_oidc_provider(db_session)

    # Restore the original Keycloak config
    _configure(monkeypatch)
    await apply_env_oidc_provider(db_session)

    # Same row, re-enabled
    provider = await _env_provider(db_session)
    assert provider.id == original_id
    assert provider.name == "Keycloak"
    assert provider.is_enabled is True
    assert provider.is_env_managed is True
