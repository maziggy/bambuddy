"""Read the single OIDC provider defined by BAMBUDDY_OIDC_* env vars (#2593).

A declarative deployment (compose, Helm, GitOps) has no way to click through
the settings UI, so one provider can be configured entirely from the
environment. This module only reads and defaults; validity is decided by the
same OIDCProviderCreate schema the API uses, so env config cannot bypass a
check the UI enforces.
"""

from __future__ import annotations

import logging
import os

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


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    return default if value is None else value.strip().lower() in _TRUTHY


def read_env_oidc_config() -> dict | None:
    """The provider's fields from the environment, or None if it isn't configured.

    An empty required var counts as unset -- `BAMBUDDY_OIDC_CLIENT_SECRET=` in
    a compose file is a forgotten value, not an intentional empty secret.
    """
    if not all(os.environ.get(key) for key in _REQUIRED):
        return None

    return {
        "name": os.environ["BAMBUDDY_OIDC_NAME"],
        "issuer_url": os.environ["BAMBUDDY_OIDC_ISSUER_URL"],
        "client_id": os.environ["BAMBUDDY_OIDC_CLIENT_ID"],
        "client_secret": os.environ["BAMBUDDY_OIDC_CLIENT_SECRET"],
        "scopes": os.environ.get("BAMBUDDY_OIDC_SCOPES", "openid email profile"),
        "is_enabled": _env_bool("BAMBUDDY_OIDC_ENABLED", True),
        "auto_create_users": _env_bool("BAMBUDDY_OIDC_AUTO_CREATE_USERS", False),
        "auto_link_existing_accounts": _env_bool("BAMBUDDY_OIDC_AUTO_LINK_EXISTING", False),
        "email_claim": os.environ.get("BAMBUDDY_OIDC_EMAIL_CLAIM", "email"),
        "require_email_verified": _env_bool("BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED", True),
        "icon_url": os.environ.get("BAMBUDDY_OIDC_ICON_URL"),
        "is_autologin": _env_bool("BAMBUDDY_OIDC_AUTOLOGIN", False),
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
)


async def apply_env_oidc_provider(db: AsyncSession) -> None:
    """Upsert the env-managed provider, or release it when the config is gone.

    Never raises: this runs during startup, and a typo in one variable must not
    stop the app from booting. A rejected config is logged and skipped.
    """
    # Imported here rather than at module scope: app.core is imported by the
    # models themselves, so a top-level import would be a cycle.
    from backend.app.models.oidc_provider import OIDCProvider
    from backend.app.schemas.auth import OIDCProviderCreate

    config = read_env_oidc_config()

    if config is None:
        # Nothing to look up by name any more, so the previously managed row is
        # found by the flag -- and then released.
        released = (
            await db.execute(select(OIDCProvider).where(OIDCProvider.is_env_managed.is_(True)))
        ).scalar_one_or_none()
        if released is not None:
            # Disabled, never deleted: user_oidc_links.provider_id is FK ON
            # DELETE CASCADE, so removing the row would unlink every bound
            # account and the links would not come back when the variables do.
            # The flag is cleared as well: with no config behind it, a provider
            # the API still refuses to edit or delete would be a dead end
            # reachable only through the database.
            released.is_enabled = False
            released.is_env_managed = False
            await db.commit()
            logger.info(
                "BAMBUDDY_OIDC_* is unset -- provider %r disabled and released to the UI.",
                released.name,
            )
        return

    # Identity is the name, which is unique on the table. Matching on the flag
    # instead meant an operator who named the env provider after one that
    # already existed hit that unique constraint during startup -- and this
    # function runs in the lifespan, so the app would not boot.
    existing = (await db.execute(select(OIDCProvider).where(OIDCProvider.name == config["name"]))).scalar_one_or_none()

    try:
        # The same schema the API uses, so env config cannot reach a state the
        # UI would have refused (notably the SEC-1 auto-link check).
        validated = OIDCProviderCreate(**config)
    except Exception as exc:  # noqa: BLE001 -- any rejection must be survivable
        logger.error("BAMBUDDY_OIDC_* config rejected, provider not applied: %s", exc)
        return

    if existing is None:
        existing = OIDCProvider(is_env_managed=True)
        db.add(existing)
    for field in _APPLIED_FIELDS:
        setattr(existing, field, getattr(validated, field))
    existing.client_secret = validated.client_secret
    existing.is_env_managed = True
    await db.flush()  # the id is needed by the autologin sweep below

    if existing.is_autologin:
        await db.execute(
            update(OIDCProvider)
            .where(OIDCProvider.id != existing.id, OIDCProvider.is_autologin.is_(True))
            .values(is_autologin=False)
        )
    await db.commit()
    logger.info("Env-managed OIDC provider %r applied.", existing.name)
