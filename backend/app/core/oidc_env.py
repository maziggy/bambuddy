"""Read the single OIDC provider defined by BAMBUDDY_OIDC_* env vars (#2593).

A declarative deployment (compose, Helm, GitOps) has no way to click through
the settings UI, so one provider can be configured entirely from the
environment. This module only reads and defaults; validity is decided by the
same OIDCProviderCreate schema the API uses, so env config cannot bypass a
check the UI enforces.
"""

from __future__ import annotations

import contextlib
import logging
import os

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# All four or nothing: a provider missing its secret would be written to the
# database and then fail at authorize time, long after the operator could
# connect the failure to a typo in their compose file.
_REQUIRED = (
    "BAMBUDDY_OIDC_NAME",
    "BAMBUDDY_OIDC_ISSUER_URL",
    "BAMBUDDY_OIDC_CLIENT_ID",
    "BAMBUDDY_OIDC_CLIENT_SECRET",
)

_TRUTHY = {"true", "1", "yes"}
_FALSY = {"false", "0", "no"}


class EnvOIDCConfigError(Exception):
    """A BAMBUDDY_OIDC_* value the reader cannot interpret. Only ever carries a
    boolean variable's name and value -- booleans are not secret, so the message
    is safe to log in full (unlike client_secret, which never reaches here)."""


def env_bool(key: str, default: bool, *, strict: bool = True) -> bool:
    """Parse a boolean env var. Absent or blank -> default (empty == unset).

    strict (the default): an unrecognized non-empty value raises
    EnvOIDCConfigError, so a typo is refused loudly rather than silently read as
    the wrong thing. strict=False: an unrecognized value falls back to the
    default instead -- for a caller on a request path where a raise would be a
    500, not a skipped startup config (see _local_login_env_bypass).
    """
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default  # absent or blank == unset -> default, per the module's promise
    norm = value.strip().lower()
    if norm in _TRUTHY:
        return True
    if norm in _FALSY:
        return False
    if strict:
        raise EnvOIDCConfigError(f"{key}={value!r} is not a recognized boolean (use true/1/yes or false/0/no)")
    return default


def read_env_oidc_config() -> dict | None:
    """The provider's fields from the environment, or None if it isn't configured.

    An empty required var counts as unset -- `BAMBUDDY_OIDC_CLIENT_SECRET=` in
    a compose file is a forgotten value, not an intentional empty secret. Blank
    means blank *after* stripping, and the surviving value is stripped too: a
    Kubernetes Secret written as a block scalar (``stringData: secret: |``) or
    created from a file carries a trailing newline that nothing downstream
    rejects -- max_length is the only bound the schema puts on these four. An
    issuer_url with a trailing newline is stored and enabled, and then fails
    with httpx.InvalidURL on the first click of the SSO button, which is the
    authorize-time failure the all-or-nothing rule above exists to prevent.
    """
    required = {key: (os.environ.get(key) or "").strip() for key in _REQUIRED}
    if not all(required.values()):
        return None

    return {
        "name": required["BAMBUDDY_OIDC_NAME"],
        "issuer_url": required["BAMBUDDY_OIDC_ISSUER_URL"],
        "client_id": required["BAMBUDDY_OIDC_CLIENT_ID"],
        "client_secret": required["BAMBUDDY_OIDC_CLIENT_SECRET"],
        "scopes": (os.environ.get("BAMBUDDY_OIDC_SCOPES") or "").strip() or "openid email profile",
        "is_enabled": env_bool("BAMBUDDY_OIDC_ENABLED", True),
        "auto_create_users": env_bool("BAMBUDDY_OIDC_AUTO_CREATE_USERS", False),
        "auto_link_existing_accounts": env_bool("BAMBUDDY_OIDC_AUTO_LINK_EXISTING", False),
        "email_claim": (os.environ.get("BAMBUDDY_OIDC_EMAIL_CLAIM") or "").strip() or "email",
        "require_email_verified": env_bool("BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED", True),
        "icon_url": (os.environ.get("BAMBUDDY_OIDC_ICON_URL") or "").strip() or None,
        "is_autologin": env_bool("BAMBUDDY_OIDC_AUTOLOGIN", False),
        # A name, not an id: ids are assigned per install, so the same compose
        # file would point at a different group on every deployment. Resolved
        # against the database in apply_env_oidc_provider -- the reader has no
        # session and stays dumb.
        "default_group": (os.environ.get("BAMBUDDY_OIDC_DEFAULT_GROUP") or "").strip() or None,
    }


# Everything the schema validates and the model stores, except client_secret --
# that one goes through the property so it is encrypted at rest.
_APPLIED_FIELDS = (
    "name",
    "issuer_url",
    "client_id",
    "scopes",
    "is_enabled",
    "auto_create_users",
    "auto_link_existing_accounts",
    "email_claim",
    "require_email_verified",
    "icon_url",
    "is_autologin",
    # Written on every boot, so a group that is no longer declared is cleared:
    # the environment is the whole truth for this row, and the API lock means
    # a lingering value could not be removed in the UI either.
    "default_group_id",
)


async def apply_env_oidc_provider(db: AsyncSession) -> None:
    """Upsert the env-managed provider, or release it when the config is gone.

    Never raises: this runs during startup, and a typo in one variable -- or a
    DB error on commit -- must not stop the app from booting. A rejected
    config is logged and skipped.
    """
    try:
        await _apply_env_oidc_provider(db)
    except Exception as exc:  # noqa: BLE001 -- startup must survive any failure here
        # Never str(exc): a DB error message can echo a configured value. Class only.
        logger.error("BAMBUDDY_OIDC_* could not be applied: %s", type(exc).__name__)
        # A commit may have half-applied; roll back so the shared session is
        # left clean for the rest of startup. Suppressed because rollback on a
        # wedged connection can itself raise -- and the whole point here is that
        # nothing in this path takes the boot down. The session is discarded by
        # the caller's `async with` regardless.
        with contextlib.suppress(Exception):
            await db.rollback()


async def _apply_env_oidc_provider(db: AsyncSession) -> None:
    # Imported here rather than at module scope: app.core is imported by the
    # models themselves, so a top-level import would be a cycle.
    from backend.app.models.group import Group
    from backend.app.models.oidc_provider import OIDCProvider
    from backend.app.schemas.auth import OIDCProviderCreate

    try:
        config = read_env_oidc_config()
    except EnvOIDCConfigError as exc:
        # Same disposition as a ValidationError or an unmatched DEFAULT_GROUP:
        # log clearly and leave any running provider as it was. Safe to log the
        # full message -- EnvOIDCConfigError only ever carries a boolean var.
        logger.error("BAMBUDDY_OIDC_* config rejected, provider not applied: %s", exc)
        return

    if config is None:
        # Nothing to look up by name any more, so the previously managed rows are
        # found by the flag -- and then released. All of them: the upsert's sweep
        # should keep that at one, but scalar_one_or_none() would raise
        # MultipleResultsFound out of the lifespan the moment it isn't, and
        # losing the boot is too steep a price for an invariant check.
        released_rows = (
            (await db.execute(select(OIDCProvider).where(OIDCProvider.is_env_managed.is_(True)))).scalars().all()
        )
        for released in released_rows:
            # Disabled, never deleted: user_oidc_links.provider_id is FK ON
            # DELETE CASCADE, so removing the row would unlink every bound
            # account and the links would not come back when the variables do.
            # The flag is cleared as well: with no config behind it, a provider
            # the API still refuses to edit or delete would be a dead end
            # reachable only through the database.
            released.is_enabled = False
            released.is_env_managed = False
            # Cleared too, or the released row keeps a latent autologin claim:
            # update_oidc_provider only re-runs the exclusivity sweep when a
            # request sets is_autologin=True, so re-enabling this row in the UI
            # would silently make it the autologin target again.
            released.is_autologin = False
            logger.info(
                "BAMBUDDY_OIDC_* is unset -- provider %r disabled and released to the UI.",
                released.name,
            )
        if released_rows:
            await db.commit()
        return

    # Identity is the name, which is unique on the table. Matching on the flag
    # instead meant an operator who named the env provider after one that
    # already existed hit that unique constraint during startup -- and this
    # function runs in the lifespan, so the app would not boot.
    existing = (await db.execute(select(OIDCProvider).where(OIDCProvider.name == config["name"]))).scalar_one_or_none()

    # Resolved before anything is written, so a name that matches no group
    # leaves the running provider untouched. Refused rather than defaulted:
    # falling back would put every auto-created user in Viewers (routes/mfa.py)
    # for as long as the typo lives, and the API answers 422 for a
    # default_group_id that does not exist -- env config gets the same answer.
    group_name = config.pop("default_group", None)
    if group_name is not None:
        group = (await db.execute(select(Group).where(Group.name == group_name))).scalar_one_or_none()
        if group is None:
            # Spelled out because the two cases differ sharply: an existing
            # provider keeps running on its last good config, while on a first
            # boot nothing is created at all and the login page has no SSO
            # button until the name matches.
            logger.error(
                "BAMBUDDY_OIDC_DEFAULT_GROUP=%r matches no group, provider not applied (%s).",
                group_name,
                "previous config left running" if existing is not None else "no provider created",
            )
            return
        config["default_group_id"] = group.id

    try:
        # The same schema the API uses, so env config cannot reach a state the
        # UI would have refused (notably the SEC-1 auto-link check).
        validated = OIDCProviderCreate(**config)
    except ValidationError as exc:
        # errors(include_input=False) strips the submitted values -- str(exc)
        # embeds input_value=... and would leak BAMBUDDY_OIDC_CLIENT_SECRET.
        logger.error(
            "BAMBUDDY_OIDC_* config rejected, provider not applied: %s",
            exc.errors(include_input=False),
        )
        return
    except Exception as exc:  # noqa: BLE001 -- any rejection must be survivable
        # Log only the exception class, never str(exc): an unexpected error here
        # could carry a configured value in its message. Structural guarantee,
        # not one contingent on which exceptions the schema validators raise.
        logger.error("BAMBUDDY_OIDC_* config could not be applied: %s", type(exc).__name__)
        return

    # Computed before `existing` is reassigned below: a freshly-created row is
    # not an adoption, and a found row that was already env-managed is a
    # routine re-apply -- only a found row that the UI created is an adoption.
    adopted_ui_provider = existing is not None and not existing.is_env_managed

    if existing is None:
        existing = OIDCProvider(is_env_managed=True)
        db.add(existing)
    for field in _APPLIED_FIELDS:
        setattr(existing, field, getattr(validated, field))
    existing.client_secret = validated.client_secret
    existing.is_env_managed = True
    await db.flush()  # the id is needed by the sweeps below

    # Renaming BAMBUDDY_OIDC_NAME matches nothing, so the row managed until now
    # stays behind. Left flagged it would keep a stale issuer and secret on the
    # login page while the API refuses every edit, disable and delete on it
    # (409) -- the dead end reachable only through the database that the release
    # path exists to prevent -- and the next release would find two rows and
    # take the boot down with MultipleResultsFound. Released, not deleted, for
    # the same cascade reason as everywhere else.
    await db.execute(
        update(OIDCProvider)
        .where(OIDCProvider.id != existing.id, OIDCProvider.is_env_managed.is_(True))
        .values(is_env_managed=False, is_enabled=False, is_autologin=False)
    )

    if existing.is_autologin:
        await db.execute(
            update(OIDCProvider)
            .where(OIDCProvider.id != existing.id, OIDCProvider.is_autologin.is_(True))
            .values(is_autologin=False)
        )
    await db.commit()
    if adopted_ui_provider:
        logger.warning(
            "Env-managed OIDC provider %r adopted an existing UI-created provider of the "
            "same name; its issuer, client and secret are now managed by BAMBUDDY_OIDC_*.",
            existing.name,
        )
    else:
        logger.info("Env-managed OIDC provider %r applied.", existing.name)
